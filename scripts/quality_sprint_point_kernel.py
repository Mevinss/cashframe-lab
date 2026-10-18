"""One fixed weak-point RBF window candidate, with no runtime activation.

Organizer binary/point labels are weak source supervision, not human-confirmed
event intervals or full coverage. No test feature loader exists in this runner.
"""
import argparse
import os
from pathlib import Path
import stat
import time

os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('OMP_NUM_THREADS', '1')
os.environ.setdefault('MKL_NUM_THREADS', '1')

import numpy as np
import psutil

from cashframe.data import read_json, sha256_file, write_json
from cashframe.learning import inference_window_parameters, window_features
from cashframe.numerics import finite_array, finite_scalar
from cashframe.research import audit_partitions, load_frozen, score_videos
from quality_sprint import score_audit, select_recall_preserving_threshold


MODEL_KEYS = {'mean', 'std', 'reference', 'alpha', 'bandwidth', 'ridge'}


def weak_training_rows(group_ids, groups, cache):
    """Nearest point window positive, source-label0 windows weak negative."""
    if not group_ids or len(group_ids) != len(set(group_ids)):
        raise ValueError('Distinct nonempty training source groups required')
    classes = [groups[group_id]['label'] for group_id in group_ids]
    if set(classes) != {0, 1}:
        raise ValueError('Both binary training source classes required')
    rows, labels, weights, per_source = [], [], [], []
    ignored = 0
    for group_id, label in zip(group_ids, classes):
        centers, windows = cache[group_id]
        centers = finite_array(centers, 'training window centers', 1)
        windows = finite_array(windows, 'training windows', 2)
        if not len(windows) or len(centers) != len(windows) or np.any(np.diff(centers) <= 0):
            raise ValueError('Aligned ordered nonempty training windows required')
        if label == 0:
            selected = np.arange(len(windows))
            points = []
        else:
            points = finite_array(groups[group_id]['points_sec'], 'original positive points', 1)
            duration = finite_scalar(groups[group_id]['duration_sec'], 'source duration', positive=True)
            if not len(points) or np.any(points < 0) or np.any(points > duration):
                raise ValueError('Original positive points must fall within source duration')
            selected = np.unique(np.argmin(np.abs(centers[:, None] - points[None, :]), axis=0))
            ignored += len(windows) - len(selected)
        rows.append(windows[selected])
        labels.extend([label] * len(selected))
        # Total loss mass is the number of original sources, independent of video
        # duration/window count; each class receives half that original mass.
        source_weight = len(group_ids) / (2 * classes.count(label))
        weights.extend([source_weight / len(selected)] * len(selected))
        per_source.append({'group_id': group_id, 'weak_label': label, 'windows': len(windows),
                           'selected_window_indices': selected.tolist(),
                           'selected_centers_sec': centers[selected].tolist(),
                           'original_point_count': len(points), 'source_loss_mass': source_weight})
    labels = np.asarray(labels, dtype=np.int64)
    return np.vstack(rows), labels, np.asarray(weights), {
        'supervision_kind': 'source_weak_supervision', 'human_confirmed': False,
        'negative_source_semantics': 'Organizer video label0; no new human full-coverage confirmation',
        'positive_source_semantics': 'Nearest scoring window to each original point; no event interval inferred',
        'positive_background': 'Unknown, excluded from fitting',
        'training_windows': len(labels), 'training_positive_windows': int(labels.sum()),
        'training_negative_windows': int((labels == 0).sum()), 'ignored_positive_windows': ignored,
        'per_source': per_source}


def _rbf(x, references, bandwidth):
    dot = x @ references.T / x.shape[1]
    distances = np.maximum(0., (x * x).mean(1)[:, None] + (references * references).mean(1)[None, :] - 2 * dot)
    return np.exp(-finite_array(distances, 'window kernel distances') / (2 * bandwidth * bandwidth))


def _validate(model):
    mean = finite_array(model['mean'], 'window mean', 1)
    std = finite_array(model['std'], 'window standard deviation', 1)
    reference = finite_array(model['reference'], 'window kernel references', 2)
    alpha = finite_array(model['alpha'], 'window kernel coefficients', 1)
    if (not len(mean) or std.shape != mean.shape or np.any(std <= 0)
            or not len(reference) or reference.shape[1] != len(mean) or len(alpha) != len(reference)):
        raise ValueError('Window kernel dimensions or standard deviations are invalid')
    finite_scalar(model['bandwidth'], 'window kernel bandwidth', positive=True)
    finite_scalar(model['ridge'], 'window kernel ridge', positive=True)


def fit_window_kernel(x, labels, weights, bandwidth=1., ridge=1.):
    """Train-only weighted normalization and one deterministic RBF ridge solve."""
    x = finite_array(x, 'weak training rows', 2)
    labels = np.asarray(labels)
    weights = finite_array(weights, 'source-class training weights', 1)
    bandwidth = finite_scalar(bandwidth, 'kernel bandwidth', positive=True)
    ridge = finite_scalar(ridge, 'kernel ridge', positive=True)
    if (not len(x) or not x.shape[1] or labels.shape != (len(x),) or weights.shape != labels.shape
            or set(labels.tolist()) != {0, 1} or np.any(weights <= 0)):
        raise ValueError('Aligned positive weights and both binary training classes required')
    normalized_weights = weights / weights.sum()
    mean = np.sum(x * normalized_weights[:, None], axis=0)
    std = np.maximum(np.sqrt(np.sum((x - mean) ** 2 * normalized_weights[:, None], axis=0)), .05)
    z = np.clip((x - mean) / std, -8, 8)
    gram = _rbf(z, z, bandwidth)
    alpha = np.linalg.solve(gram + np.diag(ridge / weights), 2 * labels - 1.)
    model = {'mean': mean, 'std': std, 'reference': z, 'alpha': alpha,
             'bandwidth': np.asarray(bandwidth), 'ridge': np.asarray(ridge)}
    _validate(model)
    return model


def window_scores(model, windows, chunk_size=128):
    """Label-free bounded scoring; arrays are frozen fitting statistics only."""
    _validate(model)
    x = finite_array(windows, 'scoring windows', 2)
    if not len(x) or x.shape[1] != len(model['mean']) or not isinstance(chunk_size, int) or chunk_size <= 0:
        raise ValueError('Aligned nonempty scoring windows and positive chunk size required')
    z = np.clip((x - model['mean']) / model['std'], -8, 8)
    scores = np.concatenate([_rbf(z[start:start + chunk_size], model['reference'], float(model['bandwidth'])) @ model['alpha']
                             for start in range(0, len(z), chunk_size)])
    return finite_array(scores, 'RBF window decision values', 1)


def bag_scores(model, windows):
    """The aggregation is fixed max, never chosen from validation alternatives."""
    return np.asarray([float(window_scores(model, rows).max()) for rows in windows])


def save_candidate(folder, model, metadata):
    _validate(model)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if (folder / 'selection-frozen.json').exists() or (folder / 'point-kernel.npz').exists():
        raise FileExistsError('Weak-point kernel already frozen; preserve artifacts')
    np.savez_compressed(folder / 'point-kernel.npz', **{key: model[key] for key in sorted(MODEL_KEYS)})
    metadata = {**metadata, 'research_kind': 'rbf_window_max_v1',
                'artifact_sha256': sha256_file(folder / 'point-kernel.npz')}
    write_json(folder / 'selection-frozen.json', metadata)
    return metadata


def load_candidate(folder, expected_encoder=None):
    folder = Path(folder)
    metadata = read_json(folder / 'selection-frozen.json')
    if metadata.get('research_kind') != 'rbf_window_max_v1':
        raise ValueError('Unsupported window kernel artifact kind')
    if expected_encoder is not None and metadata.get('encoder') != expected_encoder:
        raise ValueError('Window kernel encoder mismatch')
    if sha256_file(folder / 'point-kernel.npz') != metadata['artifact_sha256']:
        raise ValueError('Window kernel artifact hash mismatch')
    with np.load(folder / 'point-kernel.npz', allow_pickle=False) as archive:
        if set(archive.files) != MODEL_KEYS:
            raise ValueError('Window kernel artifact keys mismatch')
        model = {key: archive[key] for key in archive.files}
    _validate(model)
    finite_scalar(metadata['threshold'], 'window-max video threshold')
    return model, metadata


def run_selection(manifest_path, split_path, feature_dir, baseline_dir, output, audit_dir,
                  max_seconds=180., max_rss_mb=512.):
    start = time.perf_counter()
    output, audit_dir, feature_dir, baseline_dir = map(Path, (output, audit_dir, feature_dir, baseline_dir))
    report_path = audit_dir / 'quality-80-point-kernel.json'
    if output.exists() or report_path.exists():
        raise FileExistsError('Weak-point experiment already exists; no repeated selection')
    peak_rss = 0.
    process = psutil.Process()

    def check_budget():
        nonlocal peak_rss
        peak_rss = max(peak_rss, process.memory_info().rss / 1e6)
        if time.perf_counter() - start > max_seconds:
            raise TimeoutError('Weak-point kernel CPU budget exhausted')
        if peak_rss > max_rss_mb:
            raise MemoryError('Weak-point kernel memory budget exhausted')

    manifest, split = read_json(manifest_path), read_json(split_path)
    partition_audit = audit_partitions(manifest, split)
    groups = {row['group_id']: row for row in manifest}
    config = read_json(baseline_dir / 'config.json')
    encoder = read_json(feature_dir / 'metadata.json')['encoder']
    if encoder != config['encoder']:
        raise ValueError('Feature cache encoder differs from active baseline')
    baseline, baseline_threshold, baseline_metadata = load_frozen(baseline_dir, expected_encoder=encoder)
    inputs = {'manifest_sha256': sha256_file(manifest_path), 'split_sha256': sha256_file(split_path),
              'feature_metadata_sha256': sha256_file(feature_dir / 'metadata.json')}
    if inputs != baseline_metadata.get('inputs', inputs):
        raise ValueError('Weak-point source inputs differ from frozen baseline')
    window_parameters = inference_window_parameters(config)
    baseline_artifacts = {str(path.relative_to(baseline_dir)).replace('\\', '/'): sha256_file(path)
                          for path in sorted(baseline_dir.rglob('*')) if path.is_file()}
    protocol = {'sprint_candidate_number': 3, 'candidate_count': 1,
                'spec': {'window_features': 'original mean/std/motion', 'kernel': 'rbf', 'bandwidth': 1.,
                         'ridge': 1., 'aggregation': 'max', **window_parameters},
                'hypothesis': 'A nonlinear classifier of point-near windows may separate positive local appearances from ordinary activity better than global video means',
                'history_check': 'Earlier point and v1 window heads were linear; earlier RBF heads pooled whole-video descriptors',
                'supervision_kind': 'source_weak_supervision', 'human_confirmed': False,
                'training': 'Nearest scoring window per original positive point; all organizer label0 video windows; positive background ignored',
                'weights': 'Each class has source_count/2 loss mass; each source within a class equal; windows share source mass',
                'normalization': 'Weighted selected training rows only, std floor .05, normalized feature clipping +/-8',
                'threshold_selection': 'Validation BA/F1/recall/higher threshold, subject to recall >= active baseline',
                'binary_gate': 'Strict validation BA improvement with no recall loss; event acceptance still required',
                'selection_partitions': ['train', 'validation'], 'test_access': False,
                'active_replacement': False, 'inputs': inputs,
                'baseline_artifact_sha256': baseline_artifacts,
                'budget': {'max_seconds': max_seconds, 'max_rss_mb': max_rss_mb}}
    output.mkdir(parents=True)
    write_json(output / 'predeclared-protocol.json', protocol)
    cache, hashes = {}, {}
    for group_id in split['train'] + split['validation']:
        check_budget()
        path = feature_dir / (group_id + '.npz')
        hashes[group_id] = sha256_file(path)
        original = baseline_metadata.get('train_validation_feature_sha256', {}).get(group_id)
        if original is not None and original != hashes[group_id]:
            raise ValueError('Frozen training/validation feature cache changed')
        with np.load(path, allow_pickle=False) as sequence:
            cache[group_id] = window_features(sequence['times'], sequence['values'],
                                               groups[group_id]['duration_sec'], **window_parameters)
    x, labels, weights, supervision = weak_training_rows(split['train'], groups, cache)
    check_budget()
    model = fit_window_kernel(x, labels, weights)
    check_budget()
    candidate_scores, baseline_scores, peaks = {}, {}, {}
    for partition in ('train', 'validation'):
        windows = [cache[group_id][1] for group_id in split[partition]]
        baseline_scores[partition] = score_videos(baseline, windows)
        candidate_scores[partition], peaks[partition] = [], []
        for group_id, rows in zip(split[partition], windows):
            check_budget()
            scores = window_scores(model, rows)
            peak = int(np.argmax(scores))
            candidate_scores[partition].append(float(scores[peak]))
            peaks[partition].append(float(cache[group_id][0][peak]))
    baseline_audits = {partition: score_audit(split[partition], groups, scores, baseline_threshold)
                       for partition, scores in baseline_scores.items()}
    minimum_recall = baseline_audits['validation']['metrics']['recall']
    threshold, metrics = select_recall_preserving_threshold(candidate_scores['validation'],
                            [groups[group_id]['label'] for group_id in split['validation']], minimum_recall)
    candidate_audits = {partition: score_audit(split[partition], groups, scores, threshold)
                        for partition, scores in candidate_scores.items()}
    for partition, audit in candidate_audits.items():
        for row, peak_sec in zip(audit['per_video'], peaks[partition]):
            row['peak_sec'] = peak_sec
            points = groups[row['group_id']].get('points_sec', [])
            row['nearest_original_point_error_sec'] = min(abs(peak_sec - point) for point in points) if row['truth'] and points else None
    eligible = metrics['balanced_accuracy'] > baseline_audits['validation']['metrics']['balanced_accuracy'] and metrics['recall'] >= minimum_recall
    metadata = {'schema_version': 1, 'threshold': threshold, 'encoder': encoder,
                'window_parameters': window_parameters, 'aggregation': 'max',
                'supervision_kind': 'source_weak_supervision', 'human_confirmed': False,
                'taxonomy_confirmed': False, 'full_event_coverage_confirmed': False,
                'sprint_candidate_number': 3, 'selection_partitions': ['train', 'validation'],
                'inputs': inputs, 'train_validation_feature_sha256': hashes,
                'baseline_artifact_sha256': baseline_artifacts,
                'baseline_validation': baseline_audits['validation']['metrics'], 'validation': metrics,
                'binary_gate_passed': eligible, 'test_evaluated': False, 'active_replacement': False,
                'protocol_sha256': sha256_file(output / 'predeclared-protocol.json')}
    save_candidate(output, model, metadata)
    check_budget()
    result = {'sprint_candidate_number': 3, 'candidate_count': 1, 'test_evaluated': False,
              'inputs': inputs, 'feature_sha256': hashes, 'supervision': supervision,
              'baseline': baseline_audits, 'candidate': candidate_audits,
              'binary_gate_passed': eligible,
              'decision': 'binary_candidate_eligible_pending_event_acceptance' if eligible else 'keep_baseline',
              'promotion_performed': False, 'source_split_audit': partition_audit,
              'selection_frozen_sha256': sha256_file(output / 'selection-frozen.json'),
              'point_kernel_sha256': sha256_file(output / 'point-kernel.npz'),
              'wall_sec': time.perf_counter() - start, 'rss_mb': peak_rss,
              'limitations': ['Organizer weak source/point supervision, no human-confirmed negative coverage or intervals',
                             'No action types or TCN, no fresh independent test or camera split',
                             'Max aggregation can turn a single spurious peak into a video-level false alarm',
                             'Peak-point distance is weak-label diagnosis, not verified event localization',
                             'Small repeatedly used validation cannot establish generalization']}
    write_json(report_path, result)
    for path in output.iterdir():
        if path.is_file():
            path.chmod(stat.S_IREAD)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', default='local/dataset/manifest.json')
    parser.add_argument('--split', default='local/dataset/split.json')
    parser.add_argument('--features', default='local/features')
    parser.add_argument('--baseline', default='local/baselines/v2-before-quality-sprint')
    parser.add_argument('--output', default='local/experiments/quality-80-sprint/point-kernel')
    parser.add_argument('--audit', default='local/audit')
    args = parser.parse_args(argv)
    result = run_selection(args.manifest, args.split, args.features, args.baseline, args.output, args.audit)
    print({'decision': result['decision'], 'train': result['candidate']['train']['metrics'],
           'baseline_validation': result['baseline']['validation']['metrics'],
           'candidate_validation': result['candidate']['validation']['metrics'],
           'training_windows': result['supervision']['training_windows'],
           'training_positive_windows': result['supervision']['training_positive_windows'],
           'wall_sec': result['wall_sec'], 'rss_mb': result['rss_mb']})
    return result


if __name__ == '__main__':
    main()
