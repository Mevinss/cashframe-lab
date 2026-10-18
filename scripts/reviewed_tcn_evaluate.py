"""Frozen weak-TCN validation diagnosis using numeric caches only; never fits or activates."""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import time

import psutil

from cashframe.data import read_json, sha256_file
from cashframe.research import binary_metrics
from cashframe.sequence_candidate import checked_split, load_cached_sequence
from cashframe.sequence_training import (baseline_artifact_hashes, baseline_cached_events, load_candidate, predict,
                                        temporal_point_metrics)
from cashframe.weak_review import load_weak_review


TOLERANCES = (2., 5.)


def summarize_point_metrics(rows):
    """Micro-average one-to-one counts, preserving misses, extras and unresolved signals."""
    if not rows or len({row['tolerance_sec'] for row in rows}) != 1:
        raise ValueError('Aligned nonempty temporal point counts required')
    keys = ('reference_points', 'predictions', 'localized_predictions', 'unresolved_predictions',
            'matched', 'missed', 'extra', 'absolute_error_sum_sec')
    result = {key: sum(row[key] for row in rows) for key in keys}
    matched = result['matched']
    result.update(tolerance_sec=rows[0]['tolerance_sec'],
                  precision=matched / max(1, result['predictions']),
                  recall=matched / max(1, result['reference_points']),
                  f1=2 * matched / max(1, result['predictions'] + result['reference_points']),
                  mean_absolute_error_sec=result['absolute_error_sum_sec'] / matched if matched else None)
    return result


def numeric_events(events, duration_sec):
    """Keep numeric timing only; never serialize model/human text, types or legal references."""
    result = []
    for event in events:
        item = {key: float(event[key]) for key in ('start_sec', 'end_sec', 'timecode_sec')}
        if (not all(math.isfinite(value) for value in item.values())
                or not 0 <= item['start_sec'] <= item['end_sec'] <= duration_sec
                or not 0 <= item['timecode_sec'] <= duration_sec):
            raise ValueError('Predicted numeric event falls outside source bounds')
        status = event.get('localization_status', 'model_proposal')
        if status not in ('unresolved', 'model_proposal', 'localized'):
            raise ValueError('Unsupported localization status')
        item['localization_status'] = status
        if 'score' in event:
            item['score'] = float(event['score'])
            if not math.isfinite(item['score']):
                raise ValueError('Event score must be finite')
        result.append(item)
    return result


def _baseline_hashes(directory, expected):
    """Verify selection-time identity; current bytes never become a new authority."""
    directory = Path(directory)
    if (not isinstance(expected, dict) or not expected or 'config.json' not in expected
            or any(not isinstance(key, str) or not isinstance(value, str)
                   or not re.fullmatch(r'[0-9a-f]{64}', value) for key, value in expected.items())):
        raise ValueError('Selection-time baseline_artifact_sha256 is required; legacy records cannot be evaluated')
    if baseline_artifact_hashes(directory) != expected:
        raise ValueError('Frozen baseline artifact inventory/hash differs from selection-time identity')
    snapshot = directory / 'snapshot-sha256.json'
    if snapshot.exists():
        for relative, digest in read_json(snapshot)['files'].items():
            path = (directory / relative).resolve()
            if not path.is_relative_to(directory.resolve()) or sha256_file(path) != digest:
                raise ValueError('Frozen baseline snapshot artifact changed')
    return {directory / relative: digest for relative, digest in expected.items()}


def evaluate_reviewed_tcn(manifest_path, split_path, feature_dir, review_snapshot,
                          candidate_dir, baseline_dir, output_path):
    """Evaluate an already frozen weak binary candidate on validation, without selection.

    Train cache bytes are hashed to verify the training record. Only validation
    caches are opened as arrays. Original organizer points are weak references,
    not fully adjudicated event truth. Per-source output contains hashes/numbers.
    """
    output = Path(output_path)
    if output.exists():
        raise FileExistsError('Validation evaluation already exists; refusing to overwrite it')
    started = time.perf_counter()
    process = psutil.Process()
    peak_rss = process.memory_info().rss / 1024 ** 2
    manifest_path, split_path, feature_dir, review_snapshot, candidate_dir, baseline_dir = map(
        Path, (manifest_path, split_path, feature_dir, review_snapshot, candidate_dir, baseline_dir))
    manifest, split = read_json(manifest_path), read_json(split_path)
    by_id = checked_split(manifest, split)
    if not split['validation']:
        raise ValueError('Nonempty frozen validation partition required')
    frozen_path = candidate_dir / 'frozen.json'
    frozen = read_json(frozen_path)
    feature_metadata = read_json(feature_dir / 'metadata.json')
    baseline_config = read_json(baseline_dir / 'config.json')
    if (frozen.get('supervision') != 'weak_reviewed_boundaries'
            or frozen.get('human_target_confirmation') is not False
            or frozen.get('legal_applicability') is not False or frozen.get('classes') != []
            or frozen.get('selection_partitions') != ['train', 'validation']):
        raise ValueError('Frozen weak binary candidate contract required')
    encoder = feature_metadata.get('encoder')
    if not isinstance(encoder, dict) or not encoder or frozen.get('encoder') != encoder or baseline_config.get('encoder') != encoder:
        raise ValueError('Frozen candidate, baseline and feature encoder differ')
    if baseline_config.get('split') != split:
        raise ValueError('Frozen baseline source split differs from validation inputs')
    expected_inputs = {'manifest_sha256': manifest_path, 'split_sha256': split_path,
                       'training_review_sha256': review_snapshot / 'metadata.json',
                       'baseline_config_sha256': baseline_dir / 'config.json'}
    for key, path in expected_inputs.items():
        if frozen.get(key) != sha256_file(path):
            raise ValueError('Frozen manifest/split/review/baseline hash differs from evaluation inputs')
    baseline_hashes = _baseline_hashes(baseline_dir, frozen.get('baseline_artifact_sha256'))
    recorded_metadata = frozen.get('input_sha256', {}).get(str((feature_dir / 'metadata.json').resolve()))
    if recorded_metadata is not None and recorded_metadata != sha256_file(feature_dir / 'metadata.json'):
        raise ValueError('Feature metadata changed after candidate freeze')
    expected_ids = {gid for partition in ('train', 'validation') for gid in split[partition]}
    feature_hashes = frozen.get('train_validation_feature_sha256')
    if (not isinstance(feature_hashes, dict) or set(feature_hashes) != expected_ids
            or any(not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value)
                   for value in feature_hashes.values())):
        raise ValueError('Exact frozen train/validation feature cache hashes required; test hashes are forbidden')
    for gid, digest in feature_hashes.items():
        if sha256_file(feature_dir / (gid + '.npz')) != digest:
            raise ValueError('Frozen train/validation feature cache changed')
    load_weak_review(review_snapshot, manifest, split, partitions=('validation',))
    tracked = {path: sha256_file(path) for path in [manifest_path, split_path, frozen_path,
               feature_dir / 'metadata.json', candidate_dir / 'weights.pt', candidate_dir / 'normalization.npz',
               *[review_snapshot / name for name in ('metadata.json', 'manifest.json', 'split.json',
                                                     'events-review.csv', 'review-coverage.csv')]]}
    tracked.update(baseline_hashes)
    tracked.update({feature_dir / (gid + '.npz'): digest for gid, digest in feature_hashes.items()})
    # Keep the frozen commitments rather than recapturing modified inputs after
    # their first verification. The final check must detect that entire race.
    tracked.update({path: frozen[key] for key, path in expected_inputs.items()})
    snapshot_metadata = read_json(review_snapshot / 'metadata.json')
    tracked.update({review_snapshot / name: snapshot_metadata['inputs'][name]['sha256']
                    for name in ('events-review.csv', 'review-coverage.csv', 'manifest.json', 'split.json')})
    if recorded_metadata is not None:
        tracked[feature_dir / 'metadata.json'] = recorded_metadata
    model, mean, std, loaded = load_candidate(candidate_dir, encoder)
    if loaded != frozen or not math.isfinite(float(frozen['threshold'])):
        raise ValueError('Candidate record changed while loading or threshold is invalid')
    threshold = float(frozen['threshold'])
    peak_rss = max(peak_rss, process.memory_info().rss / 1024 ** 2)
    per_source = []
    labels = []
    predictions = {'candidate': [], 'baseline': []}
    point_rows = {name: {str(int(tolerance)): [] for tolerance in TOLERANCES}
                  for name in predictions}
    for gid in split['validation']:
        group = by_id[gid]
        duration = float(group['duration_sec'])
        points = group.get('points_sec') or ([group['point_sec']] if group.get('point_sec') is not None else [])
        points = [float(point) for point in points]
        if any(not math.isfinite(point) or not 0 <= point <= duration for point in points):
            raise ValueError('Organizer reference point lies outside its source')
        times, values = load_cached_sequence(feature_dir, gid)
        inference_started = time.perf_counter()
        candidate_events, score = predict(model, mean, std, times, values, duration, frozen)
        candidate_seconds = time.perf_counter() - inference_started
        if not math.isfinite(score):
            raise ValueError('Candidate score must be finite')
        peak_rss = max(peak_rss, process.memory_info().rss / 1024 ** 2)
        inference_started = time.perf_counter()
        baseline_events, baseline_positive = baseline_cached_events(baseline_dir, times, values, duration, encoder)
        baseline_seconds = time.perf_counter() - inference_started
        peak_rss = max(peak_rss, process.memory_info().rss / 1024 ** 2)
        row = {'source_sha256': gid, 'organizer_label': int(group['label']), 'organizer_points_sec': points,
               'duration_sec': duration, 'feature_sha256': feature_hashes[gid], 'models': {}}
        labels.append(int(group['label']))
        for name, events, positive, seconds in [('candidate', candidate_events, score >= threshold, candidate_seconds),
                                                ('baseline', baseline_events, baseline_positive, baseline_seconds)]:
            events = numeric_events(events, duration)
            predictions[name].append(int(positive))
            weak_points = {str(int(tolerance)): temporal_point_metrics(events, points, tolerance)
                           for tolerance in TOLERANCES}
            row['models'][name] = {'prediction': int(positive), 'events': events,
                                    'point_metrics': weak_points, 'cached_wall_sec': seconds}
            if name == 'candidate':
                row['models'][name]['score'] = float(score)
            for tolerance, result in weak_points.items():
                point_rows[name][tolerance].append(result)
        per_source.append(row)
    _baseline_hashes(baseline_dir, frozen['baseline_artifact_sha256'])
    if any(sha256_file(path) != digest for path, digest in tracked.items()):
        raise ValueError('Evaluation input, artifact or feature cache changed during frozen inference')
    report = {'schema_version': 'cashframe.reviewed-tcn-validation/1',
              'created_at': datetime.now(timezone.utc).isoformat(), 'partition': 'validation',
              'supervision': 'organizer_video_labels_with_human_action_boundaries',
              'human_target_confirmation': False, 'type_metrics': None, 'legal_mapping_performed': False,
              'legal_applicability': False, 'promotion_performed': False, 'fit_performed': False,
              'threshold_selection_performed': False, 'test_features_opened': False,
              'reference_status': 'original_organizer_points_are_weak_references_not_gold_event_truth',
              'validation_reused_for_selection': True, 'candidate_threshold': threshold,
              'source_groups': len(per_source), 'models': {}, 'per_source': per_source,
              'hashes': {'manifest': tracked[manifest_path], 'split': tracked[split_path],
                         'feature_metadata': tracked[feature_dir / 'metadata.json'],
                         'review_metadata': tracked[review_snapshot / 'metadata.json'],
                         'candidate_frozen': tracked[frozen_path],
                         'candidate_weights': tracked[candidate_dir / 'weights.pt'],
                         'candidate_normalization': tracked[candidate_dir / 'normalization.npz'],
                         'train_validation_features': feature_hashes,
                         'baseline_artifacts': {path.relative_to(baseline_dir).as_posix(): digest
                                                for path, digest in baseline_hashes.items()}},
              'wall_sec': time.perf_counter() - started, 'sampled_peak_rss_mb': peak_rss,
              'rss_sampling': 'Before/after model load and each cached inference; sampled, not allocation peak.',
              'timing_scope': 'Numeric cache diagnosis only. Baseline helper reloads artifacts per source. No raw-video benchmark.',
              'limitations': ['Small validation already used for fitting decisions; no unbiased generalization estimate.',
                             'Point matching is one-to-one at 2/5 seconds; unresolved signals cannot match points.',
                             'Unmatched proposals are extras against weak references, not verified false violations.',
                             'Event type quality, legal applicability and new-camera performance are unmeasured.']}
    for name in predictions:
        report['models'][name] = {'binary': binary_metrics(labels, predictions[name]),
                                  'point_metrics': {tolerance: summarize_point_metrics(results)
                                                    for tolerance, results in point_rows[name].items()}}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, allow_nan=False, indent=2)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', default='local/dataset/manifest.json')
    parser.add_argument('--split', default='local/dataset/split.json')
    parser.add_argument('--features', default='local/features')
    parser.add_argument('--review', default='local/annotations/weak-reviewed-20261018-v1')
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--baseline', default='local/baselines/v2-before-quality-sprint')
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    try:
        report = evaluate_reviewed_tcn(args.manifest, args.split, args.features, args.review,
                                       args.candidate, args.baseline, args.output)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # Error categories remain actionable without echoing private source text.
        print(json.dumps({'status': 'blocked', 'error_class': type(exc).__name__}))
        return 2
    summary = {name: {'accuracy': values['binary']['accuracy'],
                      'balanced_accuracy': values['binary']['balanced_accuracy'],
                      'recall': values['binary']['recall'],
                      'point_f1_2_sec': values['point_metrics']['2']['f1'],
                      'point_f1_5_sec': values['point_metrics']['5']['f1']}
               for name, values in report['models'].items()}
    print(json.dumps({'status': 'validation_evaluated', 'source_groups': report['source_groups'],
                      'models': summary, 'wall_sec': report['wall_sec'],
                      'sampled_peak_rss_mb': report['sampled_peak_rss_mb'], 'promotion_performed': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
