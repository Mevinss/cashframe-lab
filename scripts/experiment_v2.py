"""Predeclared cached-feature experiment. Default phase never opens test features.

Run selection once; separately evaluate the frozen artifact once. Detailed rows
and all feature/model artifacts are private under the ignored local directory.
"""
import argparse
from pathlib import Path
import time
import numpy as np
import psutil
from cashframe.data import read_json,write_json,sha256_file
from cashframe.learning import window_features,supervised_mask,load_model,model_scores,video_score
from cashframe.research import (audit_partitions,binary_metrics,candidate_specs,
    select_from_train_validation,save_frozen,load_frozen,score_videos)


def cached_windows(gids,by_id,feature_dir,check_budget=lambda:None):
    rows=[]
    for gid in gids:
        check_budget()
        with np.load(feature_dir/(gid+'.npz'),allow_pickle=False) as sequence:
            centers,windows=window_features(sequence['times'],sequence['values'],by_id[gid]['duration_sec'])
        rows.append((centers,windows))
    return rows


def old_diagnostics(gids,by_id,windows,old_model_dir):
    if not (old_model_dir/'config.json').exists():return None
    model,config,threshold=load_model(old_model_dir)
    rows=[]
    for gid,(centers,features) in zip(gids,windows):
        scores=model_scores(model,features)
        rows.append({'group_id':gid,'truth':by_id[gid]['label'],'windows':len(scores),
                     'window_positive_fraction':float((scores>=config.get('event_threshold',threshold)).mean()),
                     'point_max':float(scores.max()),'video_score':video_score(model,features),
                     'prediction':int(video_score(model,features)>=threshold)})
    return {'metrics':binary_metrics([r['truth'] for r in rows],[r['prediction'] for r in rows]),
            'threshold':threshold,'video_bag_enabled':'bag_coef' in model,'per_video':rows}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',default='local/dataset/manifest.json')
    parser.add_argument('--split',default='local/dataset/split.json')
    parser.add_argument('--features',default='local/features')
    parser.add_argument('--output',default='local/experiments/v2')
    parser.add_argument('--old-model',default='local/experiment')
    parser.add_argument('--phase',choices=['select','evaluate'],default='select')
    parser.add_argument('--max-seconds',type=float,default=60.)
    parser.add_argument('--max-rss-mb',type=float,default=512.)
    args=parser.parse_args(argv);start=time.perf_counter()
    def check_budget():
        if time.perf_counter()-start>args.max_seconds:raise RuntimeError('Experiment time budget exhausted')
        if psutil.Process().memory_info().rss/1e6>args.max_rss_mb:raise RuntimeError('Experiment RAM budget exhausted')
    check_budget()
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    manifest=read_json(args.manifest);split=read_json(args.split);by_id={r['group_id']:r for r in manifest}
    audit=audit_partitions(manifest,split);feature_dir=Path(args.features)
    inputs={'manifest_sha256':sha256_file(args.manifest),'split_sha256':sha256_file(args.split),
            'feature_metadata_sha256':sha256_file(feature_dir/'metadata.json')}
    if args.phase=='select':
        if (output/'selection-frozen.json').exists():raise ValueError('Selection already frozen; preserve existing experiment')
        protocol={'protocol_version':2,'seed':42,'folds':4,'candidates':candidate_specs(),
                  'architecture_selection':'training groupwise OOF balanced accuracy, F1, recall, declared order',
                  'threshold_selection':'validation balanced accuracy, F1, recall, highest threshold',
                  'test_status':'Already viewed by v1; exploratory reuse only. No new-camera data.',
                  'budget':{'cached_features_only':True,'max_seconds':args.max_seconds,'max_rss_mb':args.max_rss_mb,'cooperative_checks':True},**inputs}
        write_json(output/'predeclared-protocol.json',protocol)
        train=cached_windows(split['train'],by_id,feature_dir,check_budget)
        valid=cached_windows(split['validation'],by_id,feature_dir,check_budget)
        result=select_from_train_validation(split['train'],[r[1] for r in train],[by_id[i]['label'] for i in split['train']],
            [r[1] for r in valid],[by_id[i]['label'] for i in split['validation']],check_budget=check_budget)
        model=result.pop('model')
        supervised=[]
        for gid,(centers,windows) in zip(split['train'],train):
            group=by_id[gid];mask,labels=supervised_mask(centers,group['label'],group['points_sec'])
            supervised.append({'group_id':gid,'label':group['label'],'windows':len(centers),'supervised_windows':int(mask.sum()),
                               'duration_sec':group['duration_sec'],'point_count':len(group['points_sec'])})
        result.update(inputs=inputs,encoder=read_json(feature_dir/'metadata.json')['encoder'],audit=audit,
                      source_split=split,supervision=supervised,
                      old_validation=old_diagnostics(split['validation'],by_id,valid,Path(args.old_model)),
                      train_validation_feature_sha256={gid:sha256_file(feature_dir/(gid+'.npz')) for gid in split['train']+split['validation']},
                      training_wall_sec=time.perf_counter()-start,rss_mb=psutil.Process().memory_info().rss/1e6,
                      selected_model_score_kind='uncalibrated kernel decision value',test_status=protocol['test_status'])
        save_frozen(output,model,result['threshold'],result)
        print({'phase':'selected','spec':result['selected_spec'],'train_cv':result['train_cv'],
               'validation':result['validation'],'validation_at_train_cv_threshold':result['validation_at_train_cv_threshold'],
               'wall_sec':round(result['training_wall_sec'],3),'rss_mb':round(result['rss_mb'],1)})
        return result
    if (output/'test-report.json').exists():raise ValueError('Test already evaluated; read the saved report without retuning')
    model,threshold,config=load_frozen(output)
    if config['inputs']!=inputs:raise ValueError('Dataset or feature metadata changed after selection')
    for gid,digest in config['train_validation_feature_sha256'].items():
        if sha256_file(feature_dir/(gid+'.npz'))!=digest:raise ValueError('Training/validation feature hash mismatch')
    windows=cached_windows(split['test'],by_id,feature_dir,check_budget)
    scores=score_videos(model,[r[1] for r in windows]);labels=[by_id[i]['label'] for i in split['test']]
    rows=[{'group_id':gid,'truth':label,'score':float(score),'prediction':int(score>=threshold)} for gid,label,score in zip(split['test'],labels,scores)]
    report={'status':'exploratory reuse of already-viewed test; no new-camera claim','selection_frozen_sha256':sha256_file(output/'selection-frozen.json'),
            'selected':binary_metrics(labels,scores>=threshold),'constant_positive':binary_metrics(labels,np.ones(len(labels))),
            'constant_negative':binary_metrics(labels,np.zeros(len(labels))),
            'old':old_diagnostics(split['test'],by_id,windows,Path(args.old_model)),
            'per_video':rows,'threshold':threshold,'scoring_wall_sec':time.perf_counter()-start,
            'rss_mb':psutil.Process().memory_info().rss/1e6,'test_feature_sha256':{gid:sha256_file(feature_dir/(gid+'.npz')) for gid in split['test']}}
    write_json(output/'test-report.json',report)
    print({k:report[k] for k in ['status','selected','constant_positive','constant_negative','scoring_wall_sec','rss_mb']})
    return report

if __name__=='__main__':main()
