"""One predeclared train/validation experiment; never open test feature files.

Compare temporal descriptors and lightweight ridge/RBF heads. A conservative
threshold is accepted only with validation specificity >= .75 and recall >= .5.
All reported OOF/validation numbers are selection estimates, not independent tests.
"""
import argparse
from pathlib import Path
import time
import numpy as np

from cashframe.data import read_json,write_json,sha256_file
from cashframe.learning import load_model,video_score,inference_window_parameters,window_features
from cashframe.research import (audit_partitions,binary_metrics,group_folds,fit_video_model,
                               score_videos,calibrate_threshold,save_frozen)


def specs():
    return [dict(descriptor=descriptor,kernel=kernel,ridge=ridge,bandwidth=bandwidth)
            for descriptor in ('mean','mean_std','robust','temporal_pyramid','dynamics')
            for kernel,ridge,bandwidth in [('linear',r,1.) for r in (.1,1.,10.)]+[('rbf',r,b) for r in (1.,10.) for b in (.5,1.,2.)]]


def conservative_threshold(scores, labels):
    scores=np.asarray(scores)
    if not np.isfinite(scores).all():raise ValueError('Non-finite calibration scores')
    candidates=[]
    for threshold in np.r_[np.unique(scores),np.nextafter(max(scores),np.inf)]:
        result=binary_metrics(labels,scores>=threshold)
        if result['specificity']>=.75 and result['recall']>=.5:
            candidates.append((result['balanced_accuracy'],result['specificity'],result['f1'],float(threshold),result))
    if not candidates:return None
    selected=max(candidates,key=lambda row:row[:4])
    return selected[3],selected[4]


def run(manifest_path, split_path, feature_dir, output, base_dir):
    start=time.perf_counter();output=Path(output)
    if output.exists():raise FileExistsError('Use a fresh experiment directory; existing selection is immutable')
    output.mkdir(parents=True)
    protocol={'version':3,'candidates':specs(),'seed':42,'folds':4,
              'architecture_selection':'training OOF balanced accuracy, specificity, F1, declared order',
              'threshold_selection':'validation balanced accuracy subject to specificity >= .75 and recall >= .5',
              'promotion':'validation accuracy improves and false positives decrease versus restored base',
              'test_access':False,'optical_flow':False,'max_seconds':180}
    write_json(output/'predeclared-protocol.json',protocol)
    manifest,split=read_json(manifest_path),read_json(split_path)
    audit_partitions(manifest,split);by_id={r['group_id']:r for r in manifest}
    base,config,base_threshold=load_model(base_dir)
    feature_dir=Path(feature_dir);train_ids=split['train'];valid_ids=split['validation']
    windows={}
    for gid in train_ids+valid_ids:
        with np.load(feature_dir/(gid+'.npz'),allow_pickle=False) as sequence:
            _,windows[gid]=window_features(sequence['times'],sequence['values'],by_id[gid]['duration_sec'],**inference_window_parameters(config))
    labels=np.array([by_id[g]['label'] for g in train_ids]);valid_labels=np.array([by_id[g]['label'] for g in valid_ids])
    index={g:i for i,g in enumerate(train_ids)};folds=group_folds(train_ids,labels)
    rows=[]
    for number,spec in enumerate(protocol['candidates']):
        scores=np.zeros(len(train_ids))
        for fit_ids,held_ids in folds:
            if time.perf_counter()-start>180:raise TimeoutError('Experiment budget exceeded')
            model=fit_video_model([windows[g] for g in fit_ids],[by_id[g]['label'] for g in fit_ids],spec)
            scores[[index[g] for g in held_ids]]=score_videos(model,[windows[g] for g in held_ids])
        threshold,metrics=calibrate_threshold(scores,labels,'train_cv')
        rows.append({'index':number,'spec':spec,'oof':metrics,'oof_threshold':threshold,'oof_scores':scores.tolist()})
    winner=max(rows,key=lambda r:(r['oof']['balanced_accuracy'],r['oof']['specificity'],r['oof']['f1'],-r['index']))
    model=fit_video_model([windows[g] for g in train_ids],labels,winner['spec'])
    scores=score_videos(model,[windows[g] for g in valid_ids])
    calibrated=conservative_threshold(scores,valid_labels)
    base_scores=np.array([video_score(base,windows[g]) for g in valid_ids])
    baseline=binary_metrics(valid_labels,base_scores>=base_threshold)
    unconstrained_threshold,unconstrained=calibrate_threshold(scores,valid_labels,'validation')
    threshold,validation=calibrated or (unconstrained_threshold,unconstrained)
    promote=bool(calibrated and validation['accuracy']>baseline['accuracy'] and validation['confusion_matrix'][0][1]<baseline['confusion_matrix'][0][1])
    metadata={'research_kind':'kernel_video_v3','encoder':config['encoder'],'selected_spec':winner['spec'],
              'train_cv':winner['oof'],'validation':validation,'base_validation':baseline,
              'validation_unconstrained':unconstrained,'threshold_gate_passed':calibrated is not None,'promotion_gate_passed':promote,
              'threshold':threshold,'test_evaluated':False,'selection_partitions':['train','validation'],
              'candidates':rows,'folds':folds,'source_split':split,'training_wall_sec':time.perf_counter()-start,
              'feature_hashes':{g:sha256_file(feature_dir/(g+'.npz')) for g in train_ids+valid_ids},
              'validation_scores':scores.tolist(),'validation_labels':valid_labels.tolist(),
              'protocol_sha256':sha256_file(output/'predeclared-protocol.json'),
              'limitations':['Selection statistics on small, previously used validation; not independent accuracy.',
                             'Video-level training does not validate action localization or event types.']}
    save_frozen(output,model,threshold,metadata)
    print({k:metadata[k] for k in ('selected_spec','train_cv','base_validation','validation','threshold_gate_passed','promotion_gate_passed','training_wall_sec','test_evaluated')})
    return metadata


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',default='local/dataset/manifest.json')
    parser.add_argument('--split',default='local/dataset/split.json')
    parser.add_argument('--features',default='local/features')
    parser.add_argument('--output',default='local/experiments/v3')
    parser.add_argument('--base',default='local/experiment')
    args=parser.parse_args()
    run(args.manifest,args.split,args.features,args.output,args.base)
