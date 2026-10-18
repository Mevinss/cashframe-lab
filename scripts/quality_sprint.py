"""One predeclared CPU binary candidate; private reports, no runtime activation.

Selection opens only train/validation cached numeric features. A separate CLI
phase evaluates the already-viewed legacy test after the artifacts are frozen.
Filenames are report context only and never enter features or predictions.
"""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import shutil
import stat
import time

import numpy as np
import psutil

from cashframe.data import read_json, sha256_file, write_json
from cashframe.learning import inference_window_parameters, window_features
from cashframe.research import (
    audit_partitions, binary_metrics, fit_video_model, load_frozen,
    meets_threshold, save_frozen, score_videos,
)


def snapshot_baseline(source, target):
    """Copy once, hash every byte-bearing artifact, and mark copies read-only."""
    source, target = Path(source).resolve(), Path(target).resolve()
    if target.exists():
        raise FileExistsError('Baseline snapshot already exists; never overwrite it')
    if source == target or source in target.parents:
        raise ValueError('Snapshot must be separate from active artifacts')
    files = {str(path.relative_to(source)).replace('\\', '/'): sha256_file(path)
             for path in sorted(source.rglob('*')) if path.is_file()}
    if not files:
        raise ValueError('Baseline directory is empty')
    shutil.copytree(source, target)
    for relative, digest in files.items():
        if sha256_file(source / relative) != digest or sha256_file(target / relative) != digest:
            raise ValueError('Baseline changed during snapshot')
    result = {'source': str(source), 'created_utc': datetime.now(timezone.utc).isoformat(),
              'files': files, 'overwrite_permitted': False,
              'immutability': 'write-once API, SHA-256 integrity, read-only copied files'}
    write_json(target / 'snapshot-sha256.json', result)
    for path in target.rglob('*'):
        if path.is_file():
            path.chmod(stat.S_IREAD)
    return result


def select_recall_preserving_threshold(scores, labels, min_recall, partition='validation'):
    """Select one threshold on validation without sacrificing baseline recall."""
    if partition != 'validation':
        raise ValueError('Threshold selection is restricted to validation')
    scores = np.asarray(scores, dtype=float)
    labels = np.asarray(labels)
    if scores.ndim != 1 or not len(scores) or not np.isfinite(scores).all():
        raise ValueError('Nonempty finite one-dimensional scores required')
    if labels.shape != scores.shape or set(labels.tolist()) != {0, 1}:
        raise ValueError('Aligned validation labels from both classes required')
    if not np.isfinite(min_recall) or not 0 <= min_recall <= 1:
        raise ValueError('Recall constraint must be between zero and one')
    best = None
    for threshold in np.unique(scores):
        result = binary_metrics(labels, meets_threshold(scores, threshold))
        if result['recall'] < min_recall:
            continue
        key = (result['balanced_accuracy'], result['f1'], result['recall'], float(threshold))
        if best is None or key > best[0]:
            best = key, float(threshold), result
    return best[1], best[2]


def score_audit(group_ids, by_id, scores, threshold):
    """Report source-level scores and errors; metadata cannot affect scoring."""
    scores = np.asarray(scores, dtype=float)
    if len(group_ids) != len(set(group_ids)):
        raise ValueError('Every source group must contribute exactly once')
    if scores.shape != (len(group_ids),) or not np.isfinite(scores).all():
        raise ValueError('Aligned finite source scores required')
    labels = np.array([by_id[group_id]['label'] for group_id in group_ids])
    predictions = meets_threshold(scores, threshold).astype(int)
    result = binary_metrics(labels, predictions)
    rows = [{'group_id': group_id, 'files': by_id[group_id].get('files', []),
             'truth': int(label), 'score': float(score), 'prediction': int(prediction),
             'margin_to_threshold': float(score - threshold)}
            for group_id, label, score, prediction in zip(group_ids, labels, scores, predictions)]
    distribution = {}
    for label, name in ((0, 'negative'), (1, 'positive')):
        values = scores[labels == label]
        distribution[name] = {'n': len(values), 'min': float(values.min()) if len(values) else None,
                              'q25': float(np.quantile(values, .25)) if len(values) else None,
                              'median': float(np.median(values)) if len(values) else None,
                              'q75': float(np.quantile(values, .75)) if len(values) else None,
                              'max': float(values.max()) if len(values) else None}
    return {'threshold': float(threshold), 'metrics': result, 'per_video': rows,
            'score_distribution': distribution,
            'false_positive_groups': [row['group_id'] for row in rows if row['truth'] == 0 and row['prediction'] == 1],
            'false_negative_groups': [row['group_id'] for row in rows if row['truth'] == 1 and row['prediction'] == 0]}


def run_selection(manifest_path, split_path, feature_dir, baseline_dir, output, audit_dir,
                  max_seconds=120., max_rss_mb=512., candidate_kind='ridge'):
    """Fit exactly one stronger-ridge head and freeze before any legacy access."""
    if candidate_kind not in ('ridge', 'activity'):
        raise ValueError('Only the two fixed sprint candidates are supported')
    start = time.perf_counter()
    output, audit_dir = Path(output), Path(audit_dir)
    feature_dir, baseline_dir = Path(feature_dir), Path(baseline_dir)
    if output.exists():
        raise FileExistsError('Selection directory already exists; preserve the frozen candidate')
    report_prefix = 'quality-80' if candidate_kind == 'ridge' else 'quality-80-activity'
    baseline_report_path = audit_dir / (report_prefix + '-baseline.json')
    candidate_report_path = audit_dir / (report_prefix + '-binary.json')
    if baseline_report_path.exists() or candidate_report_path.exists():
        raise FileExistsError('Sprint reports already exist; preserve the audit')
    process = psutil.Process()
    peak_rss = 0.

    def check_budget():
        nonlocal peak_rss
        peak_rss = max(peak_rss, process.memory_info().rss / 1e6)
        if time.perf_counter() - start > max_seconds:
            raise TimeoutError('CPU experiment time budget exhausted')
        if peak_rss > max_rss_mb:
            raise MemoryError('CPU experiment RAM budget exhausted')

    manifest, split = read_json(manifest_path), read_json(split_path)
    audit = audit_partitions(manifest, split)
    audit.update(source_grouping='exact source SHA-256', related_scene_split_verified=False,
                 limitation='No scene/session/related-view provenance in manifest; distinct scenes and new-angle independence unverified')
    by_id = {row['group_id']: row for row in manifest}
    config = read_json(baseline_dir / 'config.json')
    encoder = read_json(feature_dir / 'metadata.json')['encoder']
    if config['encoder'] != encoder:
        raise ValueError('Cached encoder differs from baseline')
    baseline, baseline_threshold, baseline_metadata = load_frozen(baseline_dir, expected_encoder=encoder)
    inputs = {'manifest_sha256': sha256_file(manifest_path), 'split_sha256': sha256_file(split_path),
              'feature_metadata_sha256': sha256_file(feature_dir / 'metadata.json')}
    if baseline_metadata.get('inputs', inputs) != inputs:
        raise ValueError('Dataset inputs changed from the frozen baseline')
    if baseline_metadata.get('source_split', split) != split:
        raise ValueError('Source split changed from the frozen baseline')
    baseline_artifacts = {str(path.relative_to(baseline_dir)).replace('\\', '/'): sha256_file(path)
                          for path in sorted(baseline_dir.rglob('*')) if path.is_file()}
    # Exactly one predeclared numerical model. No architecture/seed grid.
    spec = {**baseline_metadata['selected_spec'], 'ridge': 100.}
    if candidate_kind == 'activity':
        spec['descriptor'] = 'activity_only'
    hypothesis = ('Stronger ridge may reduce reliance on source-specific descriptors; not a verified causal explanation'
                  if candidate_kind == 'ridge' else
                  'Drop the absolute embedding mean; pool within-window std/motion and between-window mean differences')
    historical_context = ('The sprint ridge100 run repeats the historical unaugmented v4 winner; it is a fresh regression, not a novel model. '
                          'V3 dynamics retained raw x.mean(0); activity_only removes that absolute mean and was absent from v3/v4.')
    protocol = {'seed': 42, 'candidates': [spec], 'candidate_count': 1,
                'sprint_candidate_number': 1 if candidate_kind == 'ridge' else 2,
                'hypothesis': hypothesis, 'historical_context': historical_context,
                'fitting': 'Frozen cached DINO features; train-only descriptor mean/std and kernel solve',
                'threshold_selection': 'Validation balanced accuracy, F1, recall, highest threshold; recall >= measured active baseline',
                'eligibility': 'Validation balanced accuracy strictly improves, recall does not decline',
                'selection_partitions': ['train', 'validation'], 'test_access': False,
                'legacy_status': 'Previously used eight-source test is regression only, never selection or generalization evidence',
                'active_replacement': False, 'inputs': inputs, 'baseline_artifact_sha256': baseline_artifacts,
                'budget': {'max_seconds': max_seconds, 'max_rss_mb': max_rss_mb}}
    output.mkdir(parents=True)
    write_json(output / 'predeclared-protocol.json', protocol)
    partitions, hashes = {}, {}
    window_parameters = inference_window_parameters(config)
    for partition in ('train', 'validation'):
        windows = []
        for group_id in split[partition]:
            check_budget()
            path = feature_dir / (group_id + '.npz')
            hashes[group_id] = sha256_file(path)
            expected = baseline_metadata.get('train_validation_feature_sha256', {}).get(group_id)
            if expected is not None and hashes[group_id] != expected:
                raise ValueError('Train/validation feature cache changed from baseline')
            with np.load(path, allow_pickle=False) as sequence:
                windows.append(window_features(sequence['times'], sequence['values'],
                                                by_id[group_id]['duration_sec'], **window_parameters)[1])
        partitions[partition] = windows
    baseline_scores = {partition: score_videos(baseline, windows) for partition, windows in partitions.items()}
    baseline_audits = {partition: score_audit(split[partition], by_id, scores, baseline_threshold)
                       for partition, scores in baseline_scores.items()}
    validation_labels = [by_id[group_id]['label'] for group_id in split['validation']]
    min_recall = baseline_audits['validation']['metrics']['recall']
    threshold_only, threshold_metrics = select_recall_preserving_threshold(
        baseline_scores['validation'], validation_labels, min_recall)
    threshold_sweep = [{'threshold': float(value),
                        'metrics': binary_metrics(validation_labels, meets_threshold(baseline_scores['validation'], value))}
                       for value in np.unique(baseline_scores['validation'])]
    baseline_report = {'source_split_audit': audit, 'inputs': inputs, 'baseline_artifact_sha256': baseline_artifacts,
                       'train': baseline_audits['train'], 'validation': baseline_audits['validation'],
                       'recall_preserving_threshold_diagnostic': {'threshold': threshold_only, 'metrics': threshold_metrics,
                           'strict_improvement_possible': threshold_metrics['balanced_accuracy'] > baseline_audits['validation']['metrics']['balanced_accuracy']},
                       'validation_threshold_sweep': threshold_sweep, 'test_evaluated': False,
                       'interpretation': 'Training is in-sample diagnosis. Validation has been used in prior research. Scores are uncalibrated decision values.',
                       'visual_review_performed': False,
                       'causal_hypotheses': ['Background and routine-action confusion are unverified hypotheses; numeric cache cannot establish visible causes.']}
    write_json(baseline_report_path, baseline_report)
    check_budget()
    model = fit_video_model(partitions['train'], [by_id[group_id]['label'] for group_id in split['train']], spec)
    check_budget()
    candidate_scores = {partition: score_videos(model, windows) for partition, windows in partitions.items()}
    threshold, candidate_validation_metrics = select_recall_preserving_threshold(
        candidate_scores['validation'], validation_labels, min_recall)
    candidate_audits = {partition: score_audit(split[partition], by_id, scores, threshold)
                        for partition, scores in candidate_scores.items()}
    eligible = (candidate_validation_metrics['balanced_accuracy'] > baseline_audits['validation']['metrics']['balanced_accuracy']
                and candidate_validation_metrics['recall'] >= min_recall)
    metadata = {'encoder': encoder, 'selected_spec': spec, 'inputs': inputs,
                'selection_partitions': ['train', 'validation'], 'source_split': split,
                'train_validation_feature_sha256': hashes, 'baseline_artifact_sha256': baseline_artifacts,
                'baseline_dir': str(baseline_dir.resolve()), 'baseline_threshold': baseline_threshold,
                'baseline_validation': baseline_audits['validation']['metrics'],
                'validation': candidate_validation_metrics, 'min_validation_recall': min_recall,
                'binary_gate_passed': eligible, 'active_replacement': False,
                'test_evaluated': False, 'candidate_count': 1,
                'sprint_candidate_number': protocol['sprint_candidate_number'],
                'window_parameters': window_parameters, 'protocol_sha256': sha256_file(output / 'predeclared-protocol.json')}
    save_frozen(output, model, threshold, metadata)
    check_budget()
    result = {'candidate_count': 1, 'test_evaluated': False, 'inputs': inputs, 'feature_sha256': hashes,
              'baseline': baseline_audits, 'candidate': candidate_audits,
              'candidate_spec': spec, 'binary_gate_passed': eligible,
              'sprint_candidate_number': protocol['sprint_candidate_number'],
              'historical_context': historical_context,
              'decision': 'binary_candidate_eligible_pending_event_acceptance' if eligible else 'keep_baseline',
              'promotion_performed': False, 'source_split_audit': audit,
              'selection_frozen_sha256': sha256_file(output / 'selection-frozen.json'),
              'selected_sha256': sha256_file(output / 'selected.npz'),
              'wall_sec': time.perf_counter() - start, 'rss_mb': peak_rss,
              'limitations': ['Small repeatedly used validation; no unbiased new-quality estimate',
                             'No confirmed event-type/localization or new-angle acceptance in this binary experiment',
                             'No bank images or videos were opened; cached numeric embeddings only']}
    write_json(candidate_report_path, result)
    # Write-once guard plus hashes establish reproducibility, not OS-level WORM storage.
    for path in output.iterdir():
        if path.is_file():
            path.chmod(stat.S_IREAD)
    return result


def run_legacy_regression(manifest_path, split_path, feature_dir, baseline_dir, candidate_dir, report_path):
    """Read legacy test only after verifying the frozen decision and provenance."""
    start = time.perf_counter()
    feature_dir, baseline_dir, candidate_dir, report_path = map(Path, (feature_dir, baseline_dir, candidate_dir, report_path))
    if report_path.exists():
        raise FileExistsError('Legacy regression already evaluated; do not retune')
    candidate, threshold, metadata = load_frozen(candidate_dir)
    inputs = {'manifest_sha256': sha256_file(manifest_path), 'split_sha256': sha256_file(split_path),
              'feature_metadata_sha256': sha256_file(feature_dir / 'metadata.json')}
    if metadata['inputs'] != inputs:
        raise ValueError('Dataset inputs changed after candidate freeze')
    if sha256_file(candidate_dir / 'predeclared-protocol.json') != metadata['protocol_sha256']:
        raise ValueError('Protocol changed after candidate freeze')
    for relative, digest in metadata['baseline_artifact_sha256'].items():
        if sha256_file(baseline_dir / relative) != digest:
            raise ValueError('Baseline artifacts changed after candidate freeze')
    for group_id, digest in metadata['train_validation_feature_sha256'].items():
        if sha256_file(feature_dir / (group_id + '.npz')) != digest:
            raise ValueError('Train/validation feature cache changed after candidate freeze')
    baseline, baseline_threshold, _ = load_frozen(baseline_dir, expected_encoder=metadata['encoder'])
    manifest, split = read_json(manifest_path), read_json(split_path)
    audit_partitions(manifest, split)
    by_id = {row['group_id']: row for row in manifest}
    windows, feature_hashes = [], {}
    for group_id in split['test']:
        path = feature_dir / (group_id + '.npz')
        feature_hashes[group_id] = sha256_file(path)
        with np.load(path, allow_pickle=False) as sequence:
            windows.append(window_features(sequence['times'], sequence['values'], by_id[group_id]['duration_sec'],
                                            **metadata['window_parameters'])[1])
    report = {'status': 'Exploratory regression on previously viewed eight-source test; no fresh quality or new-angle claim',
              'selection_frozen_sha256': sha256_file(candidate_dir / 'selection-frozen.json'),
              'selection_uses_test': False, 'retuning_permitted': False,
              'binary_gate_passed_before_test': metadata['binary_gate_passed'],
              'baseline': score_audit(split['test'], by_id, score_videos(baseline, windows), baseline_threshold),
              'candidate': score_audit(split['test'], by_id, score_videos(candidate, windows), threshold),
              'test_feature_sha256': feature_hashes, 'wall_sec': time.perf_counter() - start}
    write_json(report_path, report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=['snapshot', 'select', 'legacy'], default='select')
    parser.add_argument('--candidate', choices=['ridge', 'activity'], default='ridge')
    parser.add_argument('--manifest', default='local/dataset/manifest.json')
    parser.add_argument('--split', default='local/dataset/split.json')
    parser.add_argument('--features', default='local/features')
    parser.add_argument('--active', default='local/experiment-v2')
    parser.add_argument('--baseline', default='local/baselines/v2-before-quality-sprint')
    parser.add_argument('--output')
    parser.add_argument('--audit', default='local/audit')
    args = parser.parse_args(argv)
    output = args.output or ('local/experiments/quality-80-sprint/binary' if args.candidate == 'ridge'
                             else 'local/experiments/quality-80-sprint/activity-only')
    if args.phase == 'snapshot':
        result = snapshot_baseline(args.active, args.baseline)
        print({'snapshot': args.baseline, 'file_count': len(result['files'])})
    elif args.phase == 'select':
        result = run_selection(args.manifest, args.split, args.features, args.baseline, output, args.audit,
                               candidate_kind=args.candidate)
        print({'decision': result['decision'], 'wall_sec': result['wall_sec'], 'rss_mb': result['rss_mb'],
               'baseline_validation': result['baseline']['validation']['metrics'],
               'candidate_validation': result['candidate']['validation']['metrics']})
    else:
        report_name = ('quality-80-legacy-regression.json' if args.candidate == 'ridge'
                       else 'quality-80-activity-legacy-regression.json')
        result = run_legacy_regression(args.manifest, args.split, args.features, args.baseline, output,
                                       Path(args.audit) / report_name)
        print({'status': result['status'], 'baseline': result['baseline']['metrics'],
               'candidate': result['candidate']['metrics']})
    return result


if __name__ == '__main__':
    main()
