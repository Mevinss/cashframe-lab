"""Small, CPU-only video classifiers with source-group selection boundaries.

Scores are decision values, not calibrated probabilities. This module never
reads videos, images, or test labels during model/threshold selection.
"""
import numpy as np
from .learning import metrics
from .numerics import finite_array, finite_scalar


def meets_threshold(scores,threshold):
    """Treat FP64 reduction roundoff at an inclusive frozen boundary as a tie.

    The tolerance is a fixed machine-arithmetic bound, not data calibration.
    Different BLAS batch shapes can otherwise flip equality by a few ULPs.
    """
    values=finite_array(scores,'decision scores')
    threshold=finite_scalar(threshold,'decision threshold')
    tolerance=32*np.finfo(np.float64).eps*max(1.,abs(threshold))
    with np.errstate(over='ignore'):
        return (values>=threshold)|(np.abs(values-threshold)<=tolerance)


def audit_partitions(manifest, split):
    by_id={row['group_id']:row for row in manifest}
    if len(by_id)!=len(manifest):raise ValueError('Duplicate source group IDs')
    seen={}
    for partition in ('train','validation','test'):
        for gid in split[partition]:
            if gid not in by_id:raise ValueError('Unknown source group')
            digest=by_id[gid].get('sha256',gid)
            if digest in seen:raise ValueError('Duplicate source across or within partitions')
            seen[digest]=partition
    if len(seen)!=len(manifest):raise ValueError('Every source must belong to one partition')
    return {'source_groups':len(seen),'partition_overlap':False,'exact_source_overlap':False}


def group_folds(ids, labels, n_folds=4, seed=42):
    if len(ids)!=len(set(ids)):raise ValueError('Duplicate source IDs')
    labels=np.asarray(labels)
    if len(ids)!=len(labels) or set(labels)!={0,1}:raise ValueError('Both binary classes required')
    rng=np.random.default_rng(seed); buckets=[[] for _ in range(n_folds)]
    for label in (0,1):
        indices=np.flatnonzero(labels==label)
        if len(indices)<n_folds:raise ValueError('Too few groups per class for folds')
        rng.shuffle(indices)
        for offset,index in enumerate(indices):buckets[offset%n_folds].append(ids[index])
    return [([gid for gid in ids if gid not in bucket],sorted(bucket)) for bucket in buckets]


def binary_metrics(labels,predictions):
    y,p=np.asarray(labels),np.asarray(predictions)
    if not len(y) or y.shape!=p.shape or not np.isin(y,[0,1]).all() or not np.isin(p,[0,1]).all():
        raise ValueError('Aligned nonempty binary labels and predictions required')
    result=metrics(y,p);tn,fp=result['confusion_matrix'][0]
    result.update(accuracy=float((y==p).mean()),specificity=tn/max(1,tn+fp))
    result['balanced_accuracy']=(result['recall']+result['specificity'])/2
    return result


def calibrate_threshold(scores,labels,partition):
    if partition not in ('train_cv','validation'):raise ValueError('Threshold selection cannot use test or other partitions')
    scores=np.asarray(scores,dtype=float)
    if not len(scores) or not np.isfinite(scores).all():raise ValueError('Nonempty finite scores required')
    if len(scores)!=len(labels):raise ValueError('Scores and labels must align')
    values=np.unique(scores)
    # The all-negative sentinel must exceed the same fixed roundoff band as runtime.
    sentinel=values[-1]+128*np.finfo(np.float64).eps*max(1.,abs(values[-1]))
    candidates=np.r_[values,sentinel] if np.isfinite(sentinel) else values
    best=None
    for threshold in candidates:
        result=binary_metrics(labels,meets_threshold(scores,threshold))
        key=(result['balanced_accuracy'],result['f1'],result['recall'],float(threshold))
        if best is None or key>best[0]:best=(key,float(threshold),result)
    return best[1],best[2]


def describe_video(windows,descriptor):
    x=np.asarray(windows,dtype=float)
    if x.ndim!=2 or not len(x) or not np.isfinite(x).all():raise ValueError('Finite nonempty windows required')
    if descriptor=='mean':return x.mean(0)
    if descriptor=='mean_std':return np.r_[x.mean(0),x.std(0)]
    if descriptor=='robust':return np.r_[x.mean(0),x.std(0),np.quantile(x,.1,axis=0),np.quantile(x,.9,axis=0)]
    if descriptor=='temporal_pyramid':
        return np.concatenate([x.mean(0),x.std(0),*[part.mean(0) if len(part) else x[-1] for part in np.array_split(x,3)]])
    if descriptor=='dynamics':
        difference=np.abs(np.diff(x,axis=0)) if len(x)>1 else np.zeros_like(x)
        return np.r_[x.mean(0),difference.mean(0),np.quantile(difference,.9,axis=0)]
    raise ValueError('Unknown descriptor')


def _kernel(x,z,kernel,bandwidth):
    dot=finite_array(x@z.T/x.shape[1],'kernel dot products')
    if kernel=='linear':return dot
    if kernel!='rbf':raise ValueError('Unknown kernel')
    distances=np.maximum(0,(x*x).mean(1)[:,None]+(z*z).mean(1)[None,:]-2*dot)
    return np.exp(-finite_array(distances/(2*bandwidth**2),'kernel distances'))


def fit_video_model(windows,labels,spec):
    x=np.stack([describe_video(w,spec['descriptor']) for w in windows]); y=np.asarray(labels)
    if len(x)!=len(y) or set(y)!={0,1}:raise ValueError('Both training classes required')
    mean=x.mean(0);std=np.maximum(x.std(0),.05)
    z=np.clip((x-mean)/std,-8,8)
    kernel=_kernel(z,z,spec['kernel'],spec.get('bandwidth',1.))
    # Equal total mass per class; all clips contribute once, irrespective of length.
    weights=np.array([len(y)/(2*sum(y==label)) for label in y])
    alpha=np.linalg.solve(kernel+np.diag(spec['ridge']/weights),2*y-1.)
    return {'mean':mean,'std':std,'reference':z,'alpha':alpha,**spec}


def score_videos(model,windows):
    for name in ('mean', 'std', 'reference', 'alpha'):
        finite_array(model[name], name)
    if np.any(model['std'] <= 0):raise ValueError('Invalid video classifier standard deviations')
    x=np.stack([describe_video(w,str(model['descriptor'])) for w in windows])
    z=np.clip(finite_array((x-model['mean'])/model['std'],'normalized video descriptor'),-8,8)
    return finite_array(_kernel(z,model['reference'],str(model['kernel']),finite_scalar(model.get('bandwidth',1.), 'bandwidth', positive=True))@model['alpha'], 'video scores', 1)


def candidate_specs():
    specs=[]
    for descriptor in ('mean','mean_std','robust'):
        for ridge in (.1,1.,10.):specs.append(dict(descriptor=descriptor,kernel='linear',ridge=ridge,bandwidth=1.))
        for bandwidth in (.25,1.,4.):
            for ridge in (.1,1.):specs.append(dict(descriptor=descriptor,kernel='rbf',ridge=ridge,bandwidth=bandwidth))
    return specs


def select_from_train_validation(ids,train_windows,train_labels,validation_windows,validation_labels,specs=None,n_folds=4,check_budget=None):
    """Choose architecture on source-disjoint training OOF, threshold on validation.

    Test data is intentionally absent from this interface. OOF metrics are
    selection statistics, not an unbiased final performance estimate.
    """
    check_budget=check_budget or (lambda:None)
    check_budget()
    specs=candidate_specs() if specs is None else specs
    folds=group_folds(ids,train_labels,n_folds=n_folds); index={gid:i for i,gid in enumerate(ids)}
    rows=[]
    for number,spec in enumerate(specs):
        scores=np.zeros(len(ids))
        for fit_ids,eval_ids in folds:
            check_budget()
            fit=[index[i] for i in fit_ids]; held=[index[i] for i in eval_ids]
            model=fit_video_model([train_windows[i] for i in fit],[train_labels[i] for i in fit],spec)
            scores[held]=score_videos(model,[train_windows[i] for i in held])
        threshold,result=calibrate_threshold(scores,train_labels,partition='train_cv')
        rows.append({'index':number,'spec':spec,'train_cv':result,'train_cv_threshold':threshold,'oof_scores':scores.tolist()})
    selected=max(rows,key=lambda r:(r['train_cv']['balanced_accuracy'],r['train_cv']['f1'],r['train_cv']['recall'],-r['index']))
    check_budget()
    model=fit_video_model(train_windows,train_labels,selected['spec'])
    scores=score_videos(model,validation_windows)
    threshold,result=calibrate_threshold(scores,validation_labels,partition='validation')
    return {'model':model,'threshold':threshold,'selected_spec':selected['spec'],'train_cv':selected['train_cv'],
            'train_cv_threshold':selected['train_cv_threshold'],'validation':result,'validation_scores':scores.tolist(),
            'validation_at_train_cv_threshold':binary_metrics(validation_labels,meets_threshold(scores,selected['train_cv_threshold'])),
            'selection_partitions':['train','validation'],'candidates':rows,'folds':folds}


def save_frozen(folder,model,threshold,metadata):
    from pathlib import Path
    from .data import write_json,sha256_file
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    np.savez_compressed(folder/'selected.npz',**model)
    config={**metadata,'research_kind':metadata.get('research_kind','kernel_video_v2'),'threshold':float(threshold),
            'selected_sha256':sha256_file(folder/'selected.npz')}
    write_json(folder/'selection-frozen.json',config)
    return config


def load_frozen(folder,expected_encoder=None):
    from pathlib import Path
    from .data import read_json,sha256_file
    folder=Path(folder);config=read_json(folder/'selection-frozen.json')
    if expected_encoder is not None and config.get('encoder')!=expected_encoder:raise ValueError('Candidate encoder/config mismatch')
    if sha256_file(folder/'selected.npz')!=config['selected_sha256']:raise ValueError('Selected model hash mismatch')
    with np.load(folder/'selected.npz',allow_pickle=False) as archive:model={key:archive[key] for key in archive.files}
    return model,finite_scalar(config['threshold'], 'frozen decision threshold'),config


def prepare_opt_in(base_dir,candidate_dir,target_dir):
    """Create an explicit opt-in clone; never overwrite a live/base directory."""
    from pathlib import Path
    import shutil
    from .data import read_json,write_json
    base,candidate,target=Path(base_dir).resolve(),Path(candidate_dir).resolve(),Path(target_dir).resolve()
    if target.exists():raise FileExistsError('Opt-in target already exists; preserve its artifacts')
    if base==target or base in target.parents or candidate==target or candidate in target.parents:
        raise ValueError('Opt-in target must be separate from source directories')
    _,_,metadata=load_frozen(candidate)
    config=read_json(base/'config.json')
    if config.get('encoder')!=metadata.get('encoder'):raise ValueError('Candidate encoder differs from base model')
    shutil.copytree(base,target)
    for filename in ('selected.npz','selection-frozen.json'):shutil.copy2(candidate/filename,target/filename)
    shutil.copy2(base/'config.json',target/'config-v1.json')
    config.update(video_classifier='selected.npz',video_classifier_metadata='selection-frozen.json',video_classifier_status='experimental')
    write_json(target/'config.json',config)
    return target
