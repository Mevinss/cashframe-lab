"""Negative-reference persistence MIL; synthetic-tested, not production-integrated."""
import argparse
import json
from pathlib import Path
import time
import numpy as np
from cashframe.research import group_folds
from research.model_quality.compare import metrics, compare

SPECS = tuple({'persistence_samples': n, 'l2': l2} for n in (3, 7) for l2 in (1., 10.))
PROTOCOL = {'version':1, 'method':'fold_fitted_negative_reference_persistence_MIL',
            'candidates':SPECS,'folds':4,'seed':42,
            'selection':'train-source OOF balanced accuracy, specificity, F1, declared order',
            'threshold':'train OOF only; recall >= .75; balanced accuracy, specificity, F1, higher threshold',
            'validation':'one descriptive comparison against fixed V2; never retune',
            'test_access':False,'production_promotion':False,
            'distinction_from_v2_v3':'Fold-fitted negative prototype distances and persistent tail evidence; not global mean/std/kernel/pyramid descriptors.',
            'limitations':['Sample-count persistence is not a confirmed event duration.', 'Negative prototypes can learn camera/background differences.', 'OOF used for selection is not an unbiased nested-CV performance estimate.']}


def sequence(value):
    x=np.asarray(value,dtype=float)
    if x.ndim!=2 or not len(x) or not x.shape[1] or not np.isfinite(x).all():
        raise ValueError('Finite nonempty feature sequence required')
    return x


def descriptor(x, model):
    x=sequence(x)
    if x.shape[1]!=len(model['negative_mean']):raise ValueError('Feature dimension mismatch')
    distances=np.mean(np.clip((x-model['negative_mean'])/model['negative_std'],-8,8)**2,axis=1)
    n=min(model['persistence_samples'],len(x))
    persistent=np.convolve(distances,np.ones(n)/n,mode='valid')
    active=distances>model['negative_cutoff']
    longest=run=0
    for flag in active:
        run=run+1 if flag else 0;longest=max(longest,run)
    return np.r_[np.quantile(distances,[.5,.8,.95]),persistent.max(),active.mean(),longest/len(x)]


def fit(bags, labels, spec, check=lambda:None):
    x=[sequence(bag) for bag in bags];y=np.asarray(labels)
    if len(x)!=len(y) or set(y.tolist())!={0,1}:raise ValueError('Both training classes required')
    if len({bag.shape[1] for bag in x})!=1:raise ValueError('Inconsistent dimensions')
    if spec not in SPECS:raise ValueError('Undeclared candidate')
    negative=[bag for bag,label in zip(x,y) if label==0]
    mean=np.mean([bag.mean(0) for bag in negative],axis=0)
    variance=np.mean([np.mean((bag-mean)**2,axis=0) for bag in negative],axis=0)
    model={'negative_mean':mean,'negative_std':np.maximum(np.sqrt(variance),.01),**spec}
    # Each negative source contributes equally to the cutoff, not each frame.
    model['negative_cutoff']=float(np.mean([np.quantile(np.mean(np.clip((bag-mean)/model['negative_std'],-8,8)**2,axis=1),.95) for bag in negative]))
    features=np.stack([descriptor(bag,model) for bag in x]);mu=features.mean(0);std=np.maximum(features.std(0),.01)
    z=np.clip((features-mu)/std,-8,8);w=np.zeros(z.shape[1]);bias=0.
    weights=np.array([len(y)/(2*sum(y==label)) for label in y]);weights/=weights.sum()
    for _ in range(200):
        check();p=1/(1+np.exp(-np.clip(z@w+bias,-40,40)));error=(p-y)*weights
        w-=.1*(z.T@error+spec['l2']*w/len(y));bias-=.1*error.sum()
    return {**model,'descriptor_mean':mu,'descriptor_std':std,'coef':w,'bias':float(bias)}


def score(model,bags):
    z=np.stack([descriptor(bag,model) for bag in bags])
    z=np.clip((z-model['descriptor_mean'])/model['descriptor_std'],-8,8)
    return z@model['coef']+model['bias']  # decision values, not calibrated probabilities


def select_threshold(scores,labels):
    values=np.asarray(scores)
    if not len(values) or len(values)!=len(labels) or not np.isfinite(values).all():raise ValueError('Invalid OOF scores')
    choices=[]
    for threshold in np.r_[np.unique(values),np.nextafter(values.max(),np.inf)]:
        result=metrics(labels,values>=threshold)
        if result['recall']>=.75:choices.append(((result['balanced_accuracy'],result['specificity'],result['f1'],float(threshold)),float(threshold),result))
    _,threshold,result=max(choices)
    return threshold,result


def select(ids,bags,labels,check=lambda:None):
    if len(ids)!=len(bags) or len(ids)!=len(labels):raise ValueError('Misaligned training data')
    folds=group_folds(ids,labels,n_folds=4,seed=42);index={gid:i for i,gid in enumerate(ids)};rows=[]
    for number,spec in enumerate(SPECS):
        oof=np.zeros(len(ids))
        for fitting,held in folds:
            check();fit_idx=[index[i] for i in fitting];held_idx=[index[i] for i in held]
            model=fit([bags[i] for i in fit_idx],[labels[i] for i in fit_idx],spec,check)
            oof[held_idx]=score(model,[bags[i] for i in held_idx])
        threshold,result=select_threshold(oof,labels)
        rows.append({'index':number,'spec':spec,'threshold':threshold,'oof':result,'scores':oof.tolist()})
    winner=max(rows,key=lambda row:(row['oof']['balanced_accuracy'],row['oof']['specificity'],row['oof']['f1'],-row['index']))
    return fit(bags,labels,winner['spec'],check),winner,rows


def run_prepared(manifest_path,split_path,features,output,v2_path,*,authorized=False,max_seconds=120):
    # Gate before reading any path. No function opens open.zip or source videos.
    if not authorized:raise PermissionError('Codespaces data authorization is required')
    from cashframe.data import read_json,sha256_file
    from cashframe.learning import window_features,inference_window_parameters
    from cashframe.research import load_frozen,score_videos,audit_partitions
    import psutil
    root=Path(__file__).resolve().parents[2];local=(root/'local').resolve();output=Path(output).resolve()
    if local not in output.parents or output.exists():raise ValueError('Use a fresh ignored local output directory')
    for path in (manifest_path,split_path,features,v2_path):
        if local not in Path(path).resolve().parents:raise ValueError('Inputs must remain inside ignored local directory')
    start=time.monotonic()
    def budget():
        if time.monotonic()-start>max_seconds or psutil.Process().memory_info().rss>1024**3:raise RuntimeError('Experiment resource budget exceeded')
    # Freeze protocol before any feature or label access.
    output.mkdir(parents=True,exist_ok=False)
    (output/'predeclared-protocol.json').write_text(json.dumps(PROTOCOL,indent=2))
    manifest,split=read_json(manifest_path),read_json(split_path)
    audit_partitions(manifest,split)  # identity audit; does not consume test labels
    by_id={row['group_id']:row for row in manifest}
    configuration=read_json(Path(v2_path)/'config.json');encoder=read_json(Path(features)/'metadata.json')['encoder']
    if encoder!=configuration['encoder']:raise ValueError('Feature encoder mismatch')
    v2,threshold,_=load_frozen(v2_path,expected_encoder=encoder)
    cache={};windows={};digests={}
    for gid in split['train']+split['validation']:
        budget()
        if not isinstance(gid,str) or len(gid)!=64 or any(c not in '0123456789abcdef' for c in gid):raise ValueError('Expected trusted hash source ID')
        path=Path(features)/(gid+'.npz')
        if path.is_symlink():raise ValueError('Feature symlink is not permitted')
        with np.load(path,allow_pickle=False) as data:
            times=np.array(data['times']);values=sequence(data['values'])
        # Cache was built by the team extractor; preserve source/window parameters.
        _,windows[gid]=window_features(times,values,by_id[gid]['duration_sec'],**inference_window_parameters(configuration))
        cache[gid]=values;digests[gid]=sha256_file(path)
    train=split['train'];valid=split['validation'];labels=[by_id[g]['label'] for g in train]
    model,winner,rows=select(train,[cache[g] for g in train],labels,budget)
    budget();candidate=score(model,[cache[g] for g in valid])>=winner['threshold']
    reference=score_videos(v2,[windows[g] for g in valid])>=threshold
    result=compare([by_id[g]['label'] for g in valid],reference,candidate)
    result.update({'selected_spec':winner['spec'],'train_oof':winner['oof'],'test_evaluated':False,
                   'data_kind':'operator_supplied_prepared_features','encoder_unchanged':True,'elapsed_sec':time.monotonic()-start})
    arrays={k:np.asarray(v) for k,v in model.items()}
    with (output/'candidate.npz').open('xb') as stream:np.savez_compressed(stream,**arrays)
    provenance={'manifest_sha256':sha256_file(manifest_path),'split_sha256':sha256_file(split_path),
                'feature_metadata_sha256':sha256_file(Path(features)/'metadata.json'),
                'v2_config_sha256':sha256_file(Path(v2_path)/'config.json'),
                'v2_selected_sha256':sha256_file(Path(v2_path)/'selected.npz'),
                'candidate_sha256':sha256_file(output/'candidate.npz'),
                'protocol_sha256':sha256_file(output/'predeclared-protocol.json'),
                'feature_hashes':digests,'source_split':split,'candidates':rows,'threshold':winner['threshold']}
    (output/'private-provenance.json').write_text(json.dumps(provenance,indent=2))
    (output/'aggregate-report.json').write_text(json.dumps(result,indent=2))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--authorized-codespaces-data',action='store_true')
    parser.add_argument('--manifest',default='local/dataset/manifest.json');parser.add_argument('--split',default='local/dataset/split.json')
    parser.add_argument('--features',default='local/features');parser.add_argument('--output',required=True)
    parser.add_argument('--v2',default='local/experiment-v2')
    args=parser.parse_args()
    try:result=run_prepared(args.manifest,args.split,args.features,args.output,args.v2,authorized=args.authorized_codespaces_data)
    except (ValueError,PermissionError,OSError,RuntimeError):
        print(json.dumps({'status':'not_run','reason':'Authorization, prepared inputs or resource constraints not satisfied; no private details displayed.'}));return 2
    print(json.dumps(result));return 0


if __name__=='__main__':raise SystemExit(main())
