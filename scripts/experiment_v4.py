"""Bounded, opt-in descriptor regularization research using cached DINO features.

Source-group folds precede normalization and synthetic descriptor generation.
No test-feature loader, external download, or active-model replacement exists.
Descriptor perturbations are regularizers, not simulated camera viewpoints.
"""
import argparse
from pathlib import Path
import time

import numpy as np
import psutil

from cashframe.data import read_json, write_json, sha256_file
from cashframe.learning import inference_window_parameters, window_features
from cashframe.research import (
    audit_partitions, binary_metrics, calibrate_threshold, describe_video,
    group_folds, load_frozen, meets_threshold, save_frozen, score_videos,
)


def candidate_specs():
    return [dict(descriptor=descriptor, kernel='linear', ridge=ridge,
                 bandwidth=1., augmentation=augmentation)
            for descriptor in ('mean', 'mean_std', 'robust')
            for ridge in (10., 100.)
            for augmentation in ('none', 'noise', 'mixup')]


def augment_descriptors(values, labels, source_ids, method, seed=42):
    """Generate one copy per fitting source with unchanged total class/source mass."""
    x = np.asarray(values, dtype=float)
    y = np.asarray(labels)
    if (x.ndim != 2 or not len(x) or not np.isfinite(x).all()
            or len(x) != len(y) or len(x) != len(source_ids)
            or len(set(source_ids)) != len(source_ids) or set(y) != {0, 1}):
        raise ValueError('Finite aligned descriptors and distinct binary fitting sources required')
    if method not in ('none', 'noise', 'mixup'):
        raise ValueError('Unknown descriptor regularizer')
    parents = [(gid,) for gid in source_ids]
    weights = np.array([len(y) / (2 * sum(y == label)) for label in y])
    if method == 'none':
        return x.copy(), y.copy(), weights, parents
    rng = np.random.default_rng(seed)
    if method == 'noise':
        synthetic = np.clip(x + rng.normal(0., .1, size=x.shape), -8., 8.)
        synthetic_parents = list(parents)
    else:
        synthetic = np.empty_like(x)
        synthetic_parents = []
        for index, (row, label) in enumerate(zip(x, y)):
            peers = np.flatnonzero((y == label) & (np.arange(len(y)) != index))
            if not len(peers):
                raise ValueError('Same-class mixup requires two distinct fitting sources per class')
            partner = int(rng.choice(peers))
            fraction = rng.beta(2., 2.)
            synthetic[index] = fraction * row + (1 - fraction) * x[partner]
            synthetic_parents.append((source_ids[index], source_ids[partner]))
    # Each anchor contributes half its original mass to its real and synthetic row.
    return (np.concatenate([x, synthetic]), np.tile(y, 2),
            np.tile(weights / 2., 2), parents + synthetic_parents)


def fit_augmented(windows, labels, source_ids, spec, seed=42):
    x = np.stack([describe_video(rows, spec['descriptor']) for rows in windows])
    # Fit on original fitting groups only; augmented/held/validation rows cannot affect it.
    mean = x.mean(axis=0)
    std = np.maximum(x.std(axis=0), .05)
    normalized = np.clip((x - mean) / std, -8., 8.)
    z, targets, weights, _ = augment_descriptors(normalized, labels, source_ids, spec['augmentation'], seed)
    kernel = z @ z.T / z.shape[1]
    alpha = np.linalg.solve(kernel + np.diag(spec['ridge'] / weights), 2 * targets - 1.)
    return dict(mean=mean, std=std, reference=z, alpha=alpha, **spec)


def run(manifest_path, split_path, feature_dir, output, base_dir,
        max_seconds=120., max_rss_mb=512.):
    start = time.perf_counter()
    process = psutil.Process()
    peak_rss = 0.

    def check_budget():
        nonlocal peak_rss
        peak_rss = max(peak_rss, process.memory_info().rss / 1e6)
        if time.perf_counter() - start >= max_seconds:
            raise TimeoutError('Experiment time budget exhausted')
        if peak_rss > max_rss_mb:
            raise MemoryError('Experiment RAM budget exhausted')

    check_budget()
    output, feature_dir, base_dir = Path(output), Path(feature_dir), Path(base_dir)
    if output.exists():
        raise FileExistsError('Use a fresh experiment directory; preserve the frozen selection')
    manifest, split = read_json(manifest_path), read_json(split_path)
    audit = audit_partitions(manifest, split)
    by_id = {row['group_id']: row for row in manifest}
    config = read_json(base_dir / 'config.json')
    feature_metadata = read_json(feature_dir / 'metadata.json')
    if feature_metadata['encoder'] != config['encoder']:
        raise ValueError('Cached encoder differs from active model')
    baseline, baseline_threshold, baseline_metadata = load_frozen(base_dir, expected_encoder=config['encoder'])
    baseline_spec = baseline_metadata['selected_spec']
    specs = candidate_specs()
    baseline_index = next((i for i, spec in enumerate(specs)
                           if {key: value for key, value in spec.items() if key != 'augmentation'} == baseline_spec
                           and spec['augmentation'] == 'none'), None)
    if baseline_index is None:
        raise ValueError('Active baseline must be a declared unaugmented control')
    train_ids, validation_ids = split['train'], split['validation']
    labels = np.array([by_id[gid]['label'] for gid in train_ids])
    validation_labels = np.array([by_id[gid]['label'] for gid in validation_ids])
    folds = group_folds(train_ids, labels, n_folds=4, seed=42)
    baseline_hashes = {path.name: sha256_file(path) for path in base_dir.iterdir() if path.is_file()}
    protocol = {
        'version': 4, 'seed': 42, 'folds': 4, 'candidates': specs,
        'architecture_selection': 'Training source-group OOF balanced accuracy, F1, recall, declared order',
        'threshold_selection': 'Winner only: validation balanced accuracy, F1, recall, higher threshold',
        'normalization': 'Original fitting-source descriptors only, independently inside every fold',
        'regularization': {'noise_sigma': .1, 'same_class_mixup_beta': [2., 2.], 'synthetic_copies_per_source': 1,
                           'weights': 'Copies share source/class mass; original class totals unchanged'},
        'eligibility_gate': 'OOF balanced accuracy >= matched active control; validation accuracy strictly improves, recall and specificity do not decrease',
        'test_access': False, 'active_replacement': False,
        'budget': {'cached_features_only': True, 'max_seconds': max_seconds, 'max_rss_mb': max_rss_mb, 'cooperative_checks': True},
        'inputs': {'manifest_sha256': sha256_file(manifest_path), 'split_sha256': sha256_file(split_path),
                   'feature_metadata_sha256': sha256_file(feature_dir / 'metadata.json')},
        'active_artifact_sha256': baseline_hashes,
        'limits': 'Synthetic descriptors do not add independent samples or simulate real camera angles; repeated validation is exploratory',
    }
    output.mkdir(parents=True)
    write_json(output / 'predeclared-protocol.json', protocol)
    windows = {}
    for gid in train_ids + validation_ids:
        check_budget()
        with np.load(feature_dir / (gid + '.npz'), allow_pickle=False) as sequence:
            _, windows[gid] = window_features(sequence['times'], sequence['values'], by_id[gid]['duration_sec'],
                                             **inference_window_parameters(config))
    index = {gid: i for i, gid in enumerate(train_ids)}
    rows = []
    for number, spec in enumerate(specs):
        oof_scores = np.empty(len(train_ids))
        for fold_index, (fit_ids, held_ids) in enumerate(folds):
            check_budget()
            model = fit_augmented([windows[g] for g in fit_ids], [by_id[g]['label'] for g in fit_ids],
                                  fit_ids, spec, seed=42 + fold_index)
            oof_scores[[index[g] for g in held_ids]] = score_videos(model, [windows[g] for g in held_ids])
        threshold, metrics = calibrate_threshold(oof_scores, labels, 'train_cv')
        rows.append(dict(index=number, spec=spec, train_cv=metrics, train_cv_threshold=threshold,
                         oof_scores=oof_scores.tolist()))
    winner = max(rows, key=lambda row: (row['train_cv']['balanced_accuracy'], row['train_cv']['f1'],
                                       row['train_cv']['recall'], -row['index']))
    check_budget()
    train_windows = [windows[g] for g in train_ids]
    valid_windows = [windows[g] for g in validation_ids]
    model = fit_augmented(train_windows, labels, train_ids, winner['spec'], seed=42)
    scores = score_videos(model, valid_windows)
    threshold, validation = calibrate_threshold(scores, validation_labels, 'validation')
    baseline_validation = binary_metrics(validation_labels, meets_threshold(score_videos(baseline, valid_windows), baseline_threshold))
    baseline_train = binary_metrics(labels, meets_threshold(score_videos(baseline, train_windows), baseline_threshold))
    train = binary_metrics(labels, meets_threshold(score_videos(model, train_windows), threshold))
    control = rows[baseline_index]
    gates = {
        'training_oof_nonregression': winner['train_cv']['balanced_accuracy'] >= control['train_cv']['balanced_accuracy'],
        'validation_accuracy_strictly_improves': validation['accuracy'] > baseline_validation['accuracy'],
        'validation_recall_nonregression': validation['recall'] >= baseline_validation['recall'],
        'validation_specificity_nonregression': validation['specificity'] >= baseline_validation['specificity'],
    }
    check_budget()
    if baseline_hashes != {path.name: sha256_file(path) for path in base_dir.iterdir() if path.is_file()}:
        raise ValueError('Active artifact changed during experiment')
    metadata = dict(
        research_kind='kernel_video_v4_descriptor_regularization', encoder=config['encoder'],
        selected_spec=winner['spec'], threshold=threshold, train_cv=winner['train_cv'], train=train,
        train_cv_threshold=winner['train_cv_threshold'], validation=validation,
        validation_at_train_cv_threshold=binary_metrics(validation_labels, meets_threshold(scores, winner['train_cv_threshold'])),
        base_validation=baseline_validation, base_train=baseline_train, base_train_cv=control['train_cv'],
        base_threshold=baseline_threshold, gates=gates, promotion_gate_passed=all(gates.values()),
        active_replaced=False, test_evaluated=False, selection_partitions=['train', 'validation'],
        candidates=rows, folds=folds, source_split=split, audit=audit,
        validation_scores=scores.tolist(), validation_labels=validation_labels.tolist(),
        feature_hashes={gid: sha256_file(feature_dir / (gid + '.npz')) for gid in train_ids + validation_ids},
        active_artifact_sha256=baseline_hashes, protocol_sha256=sha256_file(output / 'predeclared-protocol.json'),
        inputs=protocol['inputs'], training_wall_sec=time.perf_counter() - start, observed_peak_rss_mb=peak_rss,
        limitations=[protocol['limits'], 'Training/resubstitution and threshold-optimized OOF/validation are selection estimates.',
                     'No action-type labels, camera IDs, independent new-camera data, or localization validation added.'],
    )
    save_frozen(output, model, threshold, metadata)
    print({key: metadata[key] for key in ('selected_spec', 'train_cv', 'train', 'base_train_cv', 'base_validation',
                                          'validation', 'gates', 'promotion_gate_passed', 'training_wall_sec',
                                          'observed_peak_rss_mb', 'test_evaluated')})
    return metadata


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', default='local/dataset/manifest.json')
    parser.add_argument('--split', default='local/dataset/split.json')
    parser.add_argument('--features', default='local/features')
    parser.add_argument('--output', default='local/experiments/v4-robust-2026-10-18')
    parser.add_argument('--base', default='local/experiment-v2')
    parser.add_argument('--max-seconds', type=float, default=120.)
    parser.add_argument('--max-rss-mb', type=float, default=512.)
    args = parser.parse_args()
    run(args.manifest, args.split, args.features, args.output, args.base, args.max_seconds, args.max_rss_mb)
