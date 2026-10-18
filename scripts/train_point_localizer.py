"""Bounded local point-supervised experiment. Test evaluation is a separate frozen run.

Never uploads recordings/features. No full temporal intervals are fabricated.
Run train first, inspect the frozen selection, then explicitly run evaluate-test.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time

# Bound memory/CPU before importing NumPy on machines with many logical cores.
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('OMP_NUM_THREADS', '1')
os.environ.setdefault('MKL_NUM_THREADS', '1')

import numpy as np

from cashframe.data import read_json, write_json
from cashframe.events import propose_events
from cashframe.learning import load_model, model_scores, window_features
from cashframe.point_localization import (DEFAULT_GUARD, events_from_point_scores, fit_point_model,
    load_point_localizer, point_features, point_targets, save_point_localizer)
from cashframe.sequence_candidate import checked_split

CANDIDATES = [{'feature_mode': mode, 'decay': decay, 'radius_sec': 1., 'epochs': 200}
              for mode in ('residual', 'dynamics', 'combined') for decay in (.01, .1, 1.)]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def summarize(rows):
    errors, penalized = [], []
    points = hit2 = hit5 = unresolved = negative_events = negative_with_events = 0
    coverages = []
    for row in rows:
        proposals = [e for e in row['events'] if e['localization_status'] == 'model_proposal']
        if row['truth']:
            unresolved += int(not proposals)
            points += len(row['points_sec'])
            for point in row['points_sec']:
                error = min((abs(e['timecode_sec']-point) for e in proposals), default=None)
                if error is not None:
                    errors.append(error)
                    hit2 += int(error <= 2.)
                    hit5 += int(error <= 5.)
                penalized.append(row['duration_sec'] if error is None else error)
        else:
            negative_events += len(proposals)
            negative_with_events += int(bool(proposals))
        coverages.append(sum(e['end_sec']-e['start_sec'] for e in proposals)/row['duration_sec'])
    return {'source_videos': len(rows), 'positive_videos': sum(r['truth'] for r in rows),
            'point_count': points, 'recall_within_2_sec': hit2/max(1, points),
            'recall_within_5_sec': hit5/max(1, points),
            'point_mae_resolved_sec': float(np.mean(errors)) if errors else None,
            'point_median_error_resolved_sec': float(np.median(errors)) if errors else None,
            'point_max_error_resolved_sec': float(np.max(errors)) if errors else None,
            'duration_penalized_point_mae_sec': float(np.mean(penalized)) if penalized else None,
            'unresolved_positive_videos': unresolved, 'negative_video_event_count': negative_events,
            'negative_videos_with_events': negative_with_events,
            'mean_proposed_coverage_fraction': float(np.mean(coverages)) if coverages else 0.,
            'max_proposed_coverage_fraction': max(coverages, default=0.)}


def row_for(group, events):
    proposals = [e for e in events if e['localization_status'] == 'model_proposal']
    return {'group_id': group['group_id'], 'truth': group['label'], 'points_sec': group['points_sec'],
            'duration_sec': group['duration_sec'], 'events': events,
            'point_absolute_errors_sec': [min((abs(e['timecode_sec']-p) for e in proposals), default=None)
                                          for p in group['points_sec']]}


def read_sequences(ids, feature_dir):
    sequences = {}
    for gid in ids:
        with np.load(Path(feature_dir)/(gid+'.npz'), allow_pickle=False) as archive:
            sequences[gid] = (archive['times'], archive['values'])
    return sequences


def cache_windows(sequences, groups, mode, radius):
    return {gid: point_features(*seq, groups[gid]['duration_sec'], mode, radius)
            for gid, seq in sequences.items()}


def fit(ids, cache, groups, candidate):
    targets = [point_targets(cache[gid][0], groups[gid]['label'], groups[gid]['points_sec']) for gid in ids]
    model, details = fit_point_model([cache[gid][1] for gid in ids], targets, candidate['decay'], candidate['epochs'])
    return model, details


def evaluate(ids, cache, model, groups, guard=None):
    rows = []
    for gid in ids:
        centers, x = cache[gid]
        logits = np.clip((x-model['mean'])/model['std'], -8, 8)@model['coef']+float(model['bias'])
        scores = 1/(1+np.exp(-np.clip(logits, -40, 40)))
        events = events_from_point_scores(centers, scores, groups[gid]['duration_sec'], True, DEFAULT_GUARD if guard is None else guard)
        rows.append(row_for(groups[gid], events))
    return rows


def baseline_rows(ids, sequences, groups, baseline_dir):
    model, config, threshold = load_model(baseline_dir)
    rows = []
    for gid in ids:
        centers, x = window_features(*sequences[gid], groups[gid]['duration_sec'])
        scores = model_scores(model, x)
        events = propose_events(centers, scores, config.get('event_threshold', threshold), groups[gid]['duration_sec'], True)
        rows.append(row_for(groups[gid], events))
    return rows


def folds_for(train_ids, groups):
    rng = np.random.default_rng(20261018)
    folds = [[], [], []]
    for label in (0, 1):
        ids = sorted(gid for gid in train_ids if groups[gid]['label'] == label)
        for index, gid in enumerate(rng.permutation(ids)):
            folds[index % 3].append(str(gid))
    return folds


def train(args):
    start = time.perf_counter()
    output = Path(args.output)
    if (output/'selection-frozen.json').exists():
        raise FileExistsError('Selection already frozen: choose a new isolated output directory')
    output.mkdir(parents=True, exist_ok=True)
    manifest, split = read_json(args.manifest), read_json(args.split)
    groups = checked_split(manifest, split)
    # The test archive is deliberately absent until an explicit evaluate-test run.
    ids = split['train']+split['validation']
    sequences = read_sequences(ids, args.features)
    folds = folds_for(split['train'], groups)
    protocol = {'seed': 20261018, 'candidates': CANDIDATES, 'guard': DEFAULT_GUARD,
                'folds': folds, 'split': split, 'point_tolerances_sec': [2., 5.],
                'supervision': 'point neighborhoods <=0.75 seconds positive; true-negative video windows negative; other positive-video windows unknown/excluded',
                'selection_key': 'mean(CV recall5, validation recall5), mean(CV recall2, validation recall2), negative videos with events penalty, duration-penalized MAE',
                'manifest_sha256': digest(args.manifest), 'split_sha256': digest(args.split),
                'test_used_for_selection': False}
    write_json(output/'protocol-before-training.json', protocol)
    results = []
    fitted = []
    for candidate in CANDIDATES:
        cache = cache_windows(sequences, groups, candidate['feature_mode'], candidate['radius_sec'])
        cv_rows = []
        for held in folds:
            train_ids = [gid for gid in split['train'] if gid not in held]
            model, _ = fit(train_ids, cache, groups, candidate)
            cv_rows.extend(evaluate(held, cache, model, groups))
        model, details = fit(split['train'], cache, groups, candidate)
        val_rows = evaluate(split['validation'], cache, model, groups)
        cv, val = summarize(cv_rows), summarize(val_rows)
        key = ((cv['recall_within_5_sec']+val['recall_within_5_sec'])/2,
               (cv['recall_within_2_sec']+val['recall_within_2_sec'])/2,
               -(cv['negative_videos_with_events']/11+val['negative_videos_with_events']/4)/2,
               -(cv['duration_penalized_point_mae_sec']+val['duration_penalized_point_mae_sec'])/2)
        result = {'candidate': candidate, 'cv': cv, 'validation': val, 'selection_key': key,
                  'cv_per_video': cv_rows, 'validation_per_video': val_rows, 'fit': details}
        results.append(result)
        fitted.append(model)
        print(json.dumps({'candidate': candidate, 'cv': cv, 'validation': val}), flush=True)
    selected_index = max(range(len(results)), key=lambda i: tuple(results[i]['selection_key']))
    selected = results[selected_index]
    candidate, model = selected['candidate'], fitted[selected_index]
    encoder = read_json(Path(args.features)/'metadata.json')['encoder']
    metadata = {'schema_version': 1, 'encoder': encoder, 'feature_mode': candidate['feature_mode'],
                'feature_dim': len(model['coef']), 'radius_sec': candidate['radius_sec'], 'guard': DEFAULT_GUARD,
                'training_source_count': len(split['train']), 'training_only_fitted_statistics': True,
                'weak_point_supervision': True, 'taxonomy_confirmed': False,
                'model_version': 'weak-point-v1', 'selection_test_seen': False}
    save_point_localizer(output, model, metadata)
    baseline = {part: baseline_rows(split[part], sequences, groups, args.baseline)
                for part in ('train', 'validation')}
    cache = cache_windows(sequences, groups, candidate['feature_mode'], candidate['radius_sec'])
    train_rows = evaluate(split['train'], cache, model, groups)
    frozen = {'protocol': protocol, 'selected_index': selected_index, 'selected_candidate': candidate,
              'artifact_sha256': digest(output/'point-localizer.npz'),
              'metadata_sha256': digest(output/'point-localizer.json'),
              'candidate_results': results, 'selected_train': summarize(train_rows),
              'selected_train_per_video': train_rows,
              'old_baseline': {part: {'metrics': summarize(rows), 'per_video': rows} for part, rows in baseline.items()},
              'training_wall_sec': time.perf_counter()-start,
              'test_evaluated': False, 'test_status': 'Previously reused test is exploratory only, never a new generalization estimate.'}
    write_json(output/'selection-frozen.json', frozen)
    print(json.dumps({'selected': candidate, 'cv': selected['cv'], 'validation': selected['validation'],
                      'old_validation': summarize(baseline['validation']), 'wall_sec': frozen['training_wall_sec']}), flush=True)


def evaluate_test(args):
    output = Path(args.output)
    frozen = read_json(output/'selection-frozen.json')
    if (digest(output/'point-localizer.npz') != frozen['artifact_sha256']
            or digest(output/'point-localizer.json') != frozen['metadata_sha256']
            or digest(args.manifest) != frozen['protocol']['manifest_sha256']
            or digest(args.split) != frozen['protocol']['split_sha256']):
        raise ValueError('Frozen model, metadata or split changed before exploratory evaluation')
    groups = checked_split(read_json(args.manifest), read_json(args.split))
    encoder = read_json(Path(args.features)/'metadata.json')['encoder']
    model, metadata = load_point_localizer(output, encoder)
    split = frozen['protocol']['split']
    sequences = read_sequences(split['test'], args.features)
    candidate = frozen['selected_candidate']
    cache = cache_windows(sequences, groups, candidate['feature_mode'], candidate['radius_sec'])
    new_rows = evaluate(split['test'], cache, model, groups, guard=metadata['guard'])
    old_rows = baseline_rows(split['test'], sequences, groups, args.baseline)
    result = {'status': 'exploratory_reused_test_no_new_generalization_claim',
              'selection_frozen_sha256': digest(output/'selection-frozen.json'),
              'selected': {'metrics': summarize(new_rows), 'per_video': new_rows},
              'old': {'metrics': summarize(old_rows), 'per_video': old_rows},
              'evaluation_mode': 'video-positive gate forced on for all recordings to isolate temporal behavior; negative proposals explicitly reported'}
    write_json(output/'exploratory-reused-test.json', result)
    print(json.dumps({key: result[key]['metrics'] for key in ('selected', 'old')}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('train', 'evaluate-test'))
    parser.add_argument('--manifest', default='local/dataset/manifest.json')
    parser.add_argument('--split', default='local/dataset/split.json')
    parser.add_argument('--features', default='local/features')
    parser.add_argument('--baseline', default='local/experiment')
    parser.add_argument('--output', default='local/experiments/point-localizer-2026-10-18')
    args = parser.parse_args()
    (train if args.mode == 'train' else evaluate_test)(args)


if __name__ == '__main__':
    main()
