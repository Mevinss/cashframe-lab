"""Weak point supervision, frozen-feature baseline and real classifier fitting."""
from pathlib import Path
import json
import time

import numpy as np

from .data import read_json, write_json
from .numerics import finite_array, finite_scalar


def window_features(times, features, duration_sec, radius=1., stride=.5):
    times = finite_array(times, 'sample timestamps', 1)
    features = finite_array(features, 'encoder features', 2)
    duration_sec = finite_scalar(duration_sec, 'duration', positive=True)
    radius = finite_scalar(radius, 'window radius', positive=True)
    stride = finite_scalar(stride, 'window stride', positive=True)
    if len(times) != len(features) or np.any(np.diff(times) <= 0):
        raise ValueError('Invalid feature timestamps')
    if duration_sec / stride > 10000:
        raise ValueError('Too many scoring windows')
    centers = np.unique(np.r_[np.arange(0, duration_sec, stride), duration_sec])
    rows = []
    for center in centers:
        mask=np.abs(times-center)<=radius
        local = features[mask]
        local_times=times[mask]
        if not len(local):
            local = features[[np.argmin(np.abs(times-center))]]
        motion=(np.abs(np.diff(local,axis=0))/np.maximum(np.diff(local_times)[:,None],1e-6)).mean(0) if len(local)>1 else np.zeros(features.shape[1])
        rows.append(np.r_[local.mean(0), local.std(0), motion])
    return centers, np.asarray(rows, dtype=np.float64)


def supervised_mask(centers, label, points):
    if label == 0:
        mask = np.ones(len(centers), dtype=bool)
    elif label == 1 and points:
        mask = np.min(np.abs(centers[:, None] - np.asarray(points)[None, :]), axis=1) <= .75
    else:
        raise ValueError('Positive training video needs point supervision')
    return mask, np.full(int(mask.sum()), label, dtype=np.int64)


def metrics(y, predictions):
    y, p = np.asarray(y), np.asarray(predictions)
    tp, tn = int(((y==1)&(p==1)).sum()), int(((y==0)&(p==0)).sum())
    fp, fn = int(((y==0)&(p==1)).sum()), int(((y==1)&(p==0)).sum())
    precision, recall = tp/max(1,tp+fp), tp/max(1,tp+fn)
    return {'precision': precision, 'recall': recall, 'f1': 2*precision*recall/max(1e-12,precision+recall),
            'confusion_matrix': [[tn, fp], [fn, tp]], 'n': len(y)}


def select_threshold(scores, labels, partition='validation'):
    if partition != 'validation':
        raise ValueError('Threshold selection is permitted only on validation')
    candidates = np.unique(np.r_[np.asarray(scores), np.nextafter(max(scores), np.inf)])
    best = None
    for threshold in candidates:
        result = metrics(labels, np.asarray(scores) >= threshold)
        key = (result['f1'], result['recall'], result['precision'], float(threshold))
        if best is None or key > best[0]:
            best = key, float(threshold), result
    return best[1], best[2]


def sigmoid(x):
    finite_array(x, 'classifier logits')
    return 1/(1+np.exp(-np.clip(x, -40, 40)))


def video_features(windows):
    return np.r_[windows.mean(0), windows.std(0), windows.max(0)]


def video_score(model, windows):
    point=float(model_scores(model,windows).max())
    if 'bag_coef' not in model:return point
    bag=video_features(windows)
    bag=np.clip(finite_array((bag-model['bag_mean'])/model['bag_std'],'normalized video features'),-8,8)
    return finite_scalar(sigmoid(bag@model['bag_coef']+model['bag_bias']), 'video score')


def fit_linear(x, y, sample_weights, decay, epochs=250):
    w, b = np.zeros(x.shape[1]), 0.
    m, v, mb, vb = np.zeros_like(w), np.zeros_like(w), 0., 0.
    log = []
    sw = sample_weights / sample_weights.sum()
    for epoch in range(1, epochs+1):
        p = sigmoid(x@w+b)
        error = (p-y)*sw
        gw, gb = x.T@error + decay*w, float(error.sum())
        m, v = .9*m+.1*gw, .999*v+.001*gw*gw
        mb, vb = .9*mb+.1*gb, .999*vb+.001*gb*gb
        w -= .01*(m/(1-.9**epoch))/(np.sqrt(v/(1-.999**epoch))+1e-8)
        b -= .01*(mb/(1-.9**epoch))/(np.sqrt(vb/(1-.999**epoch))+1e-8)
        if epoch == 1 or epoch % 10 == 0:
            loss = -float(np.sum(sw*(y*np.log(p+1e-12)+(1-y)*np.log(1-p+1e-12))))+.5*decay*float(w@w)
            log.append({'epoch': epoch, 'loss': loss, 'gradient_norm': float(np.linalg.norm(gw))})
    return w, b, log


def validate_model(model):
    if model['kind'] == 'linear':
        for key in ('mean', 'std', 'coef'):
            finite_array(model[key], key, 1)
        if model['mean'].shape != model['std'].shape or model['mean'].shape != model['coef'].shape:
            raise ValueError('Classifier dimensions do not match')
        if np.any(model['std'] <= 0):
            raise ValueError('Invalid classifier standard deviations')
        finite_scalar(model['bias'], 'bias')
        bag_keys = ('bag_mean', 'bag_std', 'bag_coef', 'bag_bias')
        if any(key in model for key in bag_keys):
            for key in bag_keys[:3]:
                value = finite_array(model[key], key, 1)
                if value.size != model['mean'].size * 3:
                    raise ValueError('Invalid video classifier dimensions')
            if np.any(model['bag_std'] <= 0):
                raise ValueError('Invalid video classifier standard deviations')
            finite_scalar(model['bag_bias'], 'bag bias')
    elif model['kind'] == 'distance':
        normal = finite_array(model['normal_vectors'], 'normal references', 2)
        norms=finite_array(np.linalg.norm(normal, axis=1),'reference norms')
        if normal.shape[1] != 384 or np.any(norms < 1e-12):
            raise ValueError('Invalid normal reference vectors')
    else:
        raise ValueError('Unsupported classifier kind')


def model_scores(model, x):
    x = finite_array(x, 'scoring windows', 2)
    validate_model(model)
    if model['kind'] == 'linear':
        if x.shape[1] != len(model['mean']):
            raise ValueError('Classifier and feature dimensions do not match')
        z = np.clip(finite_array((x-model['mean'])/model['std'],'normalized windows'), -8, 8)
        return finite_array(sigmoid(z@model['coef']+float(model['bias'])), 'window scores', 1)
    z = x[:, :384]
    z = z/np.maximum(finite_array(np.linalg.norm(z, axis=1, keepdims=True),'feature norms'), 1e-12)
    return finite_array(1-(z@model['normal_vectors'].T).max(1), 'distance scores', 1)


def load_model(model_dir, variant=None):
    model_dir = Path(model_dir)
    config = read_json(model_dir / 'config.json')
    variant = variant or config['selected_variant']
    if variant not in ('trained', 'baseline'):
        raise ValueError('Unsupported classifier variant')
    with np.load(model_dir / f'{variant}.npz', allow_pickle=False) as archive:
        model = {k: archive[k] for k in archive.files}
    model['kind'] = 'linear' if variant == 'trained' else 'distance'
    validate_model(model)
    threshold = finite_scalar(config['thresholds'][variant], 'decision threshold')
    finite_scalar(config.get('event_threshold', threshold), 'event threshold')
    inference_window_parameters(config)
    return model, config, threshold


def inference_window_parameters(config):
    return {'radius': finite_scalar(config.get('window_radius_sec', 1.), 'window radius', positive=True),
            'stride': finite_scalar(config.get('window_stride_sec', .5), 'window stride', positive=True)}


def train_experiment(manifest_path, split_path, feature_dir, output_dir, evaluate_test=True):
    start = time.perf_counter()
    manifest, split = read_json(manifest_path), read_json(split_path)
    feature_dir, output_dir = Path(feature_dir), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if any(set(split[a]) & set(split[b]) for a,b in [('train','validation'),('train','test'),('validation','test')]):
        raise ValueError('Source groups leak across split partitions')
    by_id, windows = {g['group_id']:g for g in manifest}, {}
    for g in manifest:
        if not evaluate_test and g['group_id'] not in split['train']+split['validation']:continue
        with np.load(feature_dir / (g['group_id']+'.npz'), allow_pickle=False) as sequence:
            windows[g['group_id']] = window_features(sequence['times'], sequence['values'], g['duration_sec'])
    xs, ys, sample_weights = [], [], []
    normal_vectors = []
    for gid in split['train']:
        g = by_id[gid]; centers, features = windows[gid]
        mask, labels = supervised_mask(centers, g['label'], g['points_sec'])
        xs.append(features[mask]); ys.append(labels)
        class_groups = sum(by_id[i]['label'] == g['label'] for i in split['train'])
        sample_weights.extend([1/(mask.sum()*class_groups)] * int(mask.sum()))
        if not g['label']:
            normal_vectors.append(features[:, :384])
    x, y = np.vstack(xs), np.concatenate(ys)
    mean, std = x.mean(0), np.maximum(x.std(0), .05)
    z = np.clip((x-mean)/std, -8, 8)
    normal = np.vstack(normal_vectors)
    normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-12)
    baseline = {'kind':'distance','normal_vectors':normal}
    logs, candidates = [], []
    for decay in (.01, .1, 1.):
        coef, bias, history = fit_linear(z, y, np.asarray(sample_weights), decay)
        candidate = {'kind':'linear','mean':mean,'std':std,'coef':coef,'bias':bias}
        val_scores = [float(model_scores(candidate, windows[gid][1]).max()) for gid in split['validation']]
        threshold, result = select_threshold(val_scores, [by_id[i]['label'] for i in split['validation']])
        candidates.append((result['f1'], result['recall'], -decay, candidate, threshold, result))
        logs.append({'decay':decay,'epochs':250,'history':history,'validation':result,'threshold':threshold})
    _, _, selected_decay, trained, trained_threshold, trained_validation = max(candidates, key=lambda c:c[:3])
    event_threshold=trained_threshold
    bx=np.stack([video_features(windows[gid][1]) for gid in split['train']])
    by=np.array([by_id[gid]['label'] for gid in split['train']])
    bag_mean,bag_std=bx.mean(0),np.maximum(bx.std(0),.05)
    bz=np.clip((bx-bag_mean)/bag_std,-8,8)
    bag_candidates=[]
    for decay in (.01,.1,1.):
        coef,bias,history=fit_linear(bz,by,np.ones(len(by)),decay)
        candidate=dict(trained,bag_mean=bag_mean,bag_std=bag_std,bag_coef=coef,bag_bias=bias)
        scores=[video_score(candidate,windows[gid][1]) for gid in split['validation']]
        threshold,result=select_threshold(scores,[by_id[gid]['label'] for gid in split['validation']])
        bag_candidates.append((result['f1'],result['recall'],-decay,candidate,threshold,result))
        logs.append({'component':'video_bag','decay':decay,'epochs':250,'history':history,'validation':result,'threshold':threshold})
    bag_best=max(bag_candidates,key=lambda c:c[:3])
    if bag_best[0]>trained_validation['f1']:
        trained,trained_threshold,trained_validation=bag_best[3:]
    baseline_scores = [float(model_scores(baseline, windows[gid][1]).max()) for gid in split['validation']]
    baseline_threshold, baseline_validation = select_threshold(baseline_scores, [by_id[i]['label'] for i in split['validation']])
    selected = 'trained' if trained_validation['f1'] >= baseline_validation['f1'] else 'baseline'
    thresholds = {'baseline':baseline_threshold,'trained':trained_threshold}
    encoder = read_json(feature_dir / 'metadata.json')['encoder']
    config = {'seed':42,'selected_variant':selected,'thresholds':thresholds,'selected_decay':-selected_decay,
              'encoder':encoder,'window_radius_sec':1.,'window_stride_sec':.5,'positive_center_tolerance_sec':.75,
              'split':split,'training_windows':len(y),'training_positive_windows':int(y.sum()),'taxonomy_confirmed':False,
              'event_threshold':event_threshold,'video_bag_enabled':'bag_coef' in trained}
    report = {'baseline_name':'Frozen DINOv2 normal-reference distance (not semantic zero-shot)',
              'validation':{'baseline':baseline_validation,'trained':trained_validation},'selected_variant':selected,'test':{},
              'unsupported_metrics':['confirmed type accuracy','human evidence relevance','new-angle robustness','semantic zero-shot baseline'],
              'limitations':['Only eight held-out source videos; same staged camera scene.','Point-localization is a weak-label proxy, not complete event coverage.','Types and normative matches are unconfirmed.']}
    for name, model in [('baseline',baseline),('trained',trained)]:
        rows=[]; errors=[]
        for gid in split['test'] if evaluate_test else []:
            centers, features=windows[gid]; scores=model_scores(model,features); peak=int(np.argmax(scores)); g=by_id[gid]
            predicted=int(video_score(model,features)>=thresholds[name])
            err=min(abs(float(centers[peak])-p) for p in g['points_sec']) if predicted and g['label'] else None
            if err is not None:errors.append(err)
            rows.append({'group_id':gid,'truth':g['label'],'prediction':predicted,'score':float(scores[peak]),'peak_sec':float(centers[peak]),'point_error_sec':err})
        if evaluate_test:report['test'][name] = {**metrics([r['truth'] for r in rows],[r['prediction'] for r in rows]),'point_mae_on_detected_positive_sec':float(np.mean(errors)) if errors else None,'per_video':rows}
    report['test_evaluated']=evaluate_test
    report['training_wall_sec']=time.perf_counter()-start
    np.savez_compressed(output_dir/'baseline.npz',normal_vectors=normal)
    np.savez_compressed(output_dir/'trained.npz',**{k:v for k,v in trained.items() if k!='kind'})
    write_json(output_dir/'config.json',config)
    write_json(output_dir/'training-log.json',logs)
    write_json(output_dir/'report.json',report)
    return report
