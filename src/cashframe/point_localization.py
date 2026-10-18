"""Point-supervised temporal proposals over frozen features; no labels at inference.

Only the labeled point neighborhood and true-negative videos supervise fitting.
Per-recording centering removes static scene offsets without using labels. Fitted
feature standardization is learned exclusively from training source groups.
"""
from pathlib import Path
import hashlib
import json

import numpy as np

from .events import propose_events
from .learning import window_features, sigmoid
from .numerics import finite_array, finite_scalar


DEFAULT_GUARD = {'min_score': .5, 'min_range': .05, 'min_prominence': .05,
                 'tail_fraction': .15, 'max_coverage_fraction': .5, 'max_events': 12}


def point_targets(centers, label, points, tolerance_sec=.75):
    centers = finite_array(centers, 'point centers', 1)
    if label not in (0, 1):
        raise ValueError('Point supervision label must be binary')
    if label == 0:
        return np.zeros(len(centers), dtype=np.int64)
    points = finite_array(points, 'positive points', 1)
    if not len(points):
        raise ValueError('Positive source needs point supervision')
    targets = np.full(len(centers), -1, dtype=np.int64)
    targets[np.min(np.abs(centers[:, None]-points[None, :]), axis=1) <= tolerance_sec] = 1
    return targets


def point_features(times, features, duration_sec, mode='combined', radius=1.):
    times = finite_array(times, 'sample timestamps', 1)
    features = finite_array(features, 'encoder features', 2)
    duration_sec = finite_scalar(duration_sec, 'duration', positive=True)
    if (not len(times) or len(features) != len(times) or not features.shape[1]
            or np.any(np.diff(times) <= 0) or times[0] < 0 or times[-1] > duration_sec):
        raise ValueError('Invalid ordered feature sequence')
    if mode not in ('residual', 'dynamics', 'combined'):
        raise ValueError('Unknown point feature mode')
    # These are inference-time, label-free recording statistics, not fitted dataset statistics.
    centered = features-np.median(features, axis=0)
    scale = np.maximum(np.std(centered, axis=0), .05)
    centers, windows = window_features(times, centered, duration_sec, radius=radius, stride=.5)
    dim = features.shape[1]
    residual = windows[:, :dim]/scale
    dynamics = np.c_[windows[:, dim:2*dim]/scale, windows[:, 2*dim:]/scale]
    x = residual if mode == 'residual' else dynamics if mode == 'dynamics' else np.c_[residual, dynamics]
    return centers, finite_array(np.clip(x, -12, 12), 'point window features', 2)


def _validate_model(model):
    for key in ('mean', 'std', 'coef'):
        finite_array(model[key], key, 1)
    if (not len(model['coef']) or model['mean'].shape != model['std'].shape
            or model['mean'].shape != model['coef'].shape or np.any(model['std'] <= 0)):
        raise ValueError('Invalid point model dimensions or standard deviations')
    finite_scalar(model['bias'], 'point bias')


def _validate_metadata(metadata):
    if metadata.get('schema_version') != 1 or metadata.get('feature_mode') not in ('residual', 'dynamics', 'combined'):
        raise ValueError('Unsupported point localizer metadata')
    if not isinstance(metadata.get('encoder'), dict) or not metadata['encoder']:
        raise ValueError('Missing point localizer encoder')
    dim = metadata.get('feature_dim')
    if not isinstance(dim, int) or dim < 1 or dim > 16384:
        raise ValueError('Invalid point localizer feature dimension')
    finite_scalar(metadata['radius_sec'], 'point radius', positive=True)
    guard = metadata['guard']
    for key in ('min_score', 'min_range', 'min_prominence', 'tail_fraction', 'max_coverage_fraction'):
        value = finite_scalar(guard[key], 'point guard '+key)
        if not 0 < value <= 1:
            raise ValueError('Invalid point localizer guard')
    if not isinstance(guard['max_events'], int) or not 1 <= guard['max_events'] <= 12:
        raise ValueError('Invalid point localizer event limit')


def save_point_localizer(model_dir, model, metadata):
    _validate_model(model)
    _validate_metadata(metadata)
    if len(model['coef']) != metadata['feature_dim']:
        raise ValueError('Point localizer dimensions differ from metadata')
    root = Path(model_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root/'point-localizer.npz'
    np.savez_compressed(path, **{key: model[key] for key in ('mean', 'std', 'coef', 'bias')})
    metadata = dict(metadata, artifact_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    (root/'point-localizer.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def load_point_localizer(model_dir, expected_encoder):
    root = Path(model_dir)
    metadata = json.loads((root/'point-localizer.json').read_text(encoding='utf-8'))
    _validate_metadata(metadata)
    if metadata['encoder'] != expected_encoder:
        raise ValueError('Point localizer encoder mismatch')
    path = root/'point-localizer.npz'
    if hashlib.sha256(path.read_bytes()).hexdigest() != metadata.get('artifact_sha256'):
        raise ValueError('Point localizer artifact hash mismatch')
    with np.load(path, allow_pickle=False) as archive:
        if set(archive.files) != {'mean', 'std', 'coef', 'bias'}:
            raise ValueError('Unsupported point localizer parameter keys')
        model = {key: archive[key] for key in archive.files}
    _validate_model(model)
    if len(model['coef']) != metadata['feature_dim']:
        raise ValueError('Point localizer dimensions differ from metadata')
    model['feature_mode'] = metadata['feature_mode']
    model['radius_sec'] = metadata['radius_sec']
    return model, metadata


def point_scores(model, times, features, duration_sec):
    _validate_model(model)
    centers, x = point_features(times, features, duration_sec, model['feature_mode'], model['radius_sec'])
    if x.shape[1] != len(model['coef']):
        raise ValueError('Point localizer and encoder dimensions do not match')
    scores = sigmoid(np.clip((x-model['mean'])/model['std'], -8, 8)@model['coef']+float(model['bias']))
    return centers, finite_array(scores, 'point localizer scores', 1)


def events_from_point_scores(times, scores, duration_sec, positive, guard=None):
    guard = DEFAULT_GUARD if guard is None else guard
    times = finite_array(times, 'point score timestamps', 1)
    scores = finite_array(scores, 'point scores', 1)
    duration_sec = finite_scalar(duration_sec, 'duration', positive=True)
    if not len(scores) or times.shape != scores.shape or np.any(np.diff(times) <= 0):
        raise ValueError('Invalid point score sequence')
    if not positive:
        return []
    # Quantile alone always selects something: absolute confidence and temporal contrast
    # must both pass before a point can be proposed. No source identity is consulted.
    threshold = max(float(guard['min_score']), float(np.quantile(scores, 1-guard['tail_fraction'])))
    if (float(np.ptp(scores)) < guard['min_range']
            or float(np.max(scores)-np.median(scores)) < guard['min_prominence']
            or float(np.max(scores)) < guard['min_score']):
        events = propose_events(times, np.full_like(scores, float(np.max(scores))), threshold, duration_sec, True)
        events[0]['localization_reason'] = 'point_localizer_low_contrast_or_confidence'
        return events
    events = propose_events(times, scores, threshold, duration_sec, True)
    coverage = sum(e['end_sec']-e['start_sec'] for e in events)/duration_sec
    if coverage >= guard['max_coverage_fraction'] or len(events) > guard['max_events']:
        events = propose_events(times, np.full_like(scores, float(np.max(scores))), threshold, duration_sec, True)
        events[0]['localization_reason'] = 'point_localizer_broad_or_fragmented'
    for event in events:
        event['localization_model'] = 'weak-point-v1'
    return events


def localized_events(model_dir, times, features, duration_sec, positive, encoder_config):
    model, metadata = load_point_localizer(model_dir, encoder_config)
    centers, scores = point_scores(model, times, features, duration_sec)
    return events_from_point_scores(centers, scores, duration_sec, positive, metadata['guard'])


def fit_point_model(rows, target_groups, decay, epochs=200):
    """Equal class/group mass; neither unknown rows nor validation fit statistics."""
    from .learning import fit_linear
    if len(rows) != len(target_groups) or not rows:
        raise ValueError('Missing point training groups')
    selected = []
    labels = []
    weights = []
    classes = []
    for x, targets in zip(rows, target_groups):
        x = finite_array(x, 'point training rows', 2)
        targets = np.asarray(targets)
        if targets.shape != (len(x),) or not np.isin(targets, [-1, 0, 1]).all():
            raise ValueError('Invalid point training targets')
        known = targets >= 0
        if not known.any() or len(np.unique(targets[known])) != 1:
            raise ValueError('Each source needs one known supervision class')
        classes.append(int(targets[known][0]))
        selected.append(x[known])
        labels.append(targets[known])
    if set(classes) != {0, 1}:
        raise ValueError('Point training requires both binary classes')
    for x, cls in zip(selected, classes):
        weights.extend([1/(2*classes.count(cls)*len(x))]*len(x))
    x, y, weights = np.vstack(selected), np.concatenate(labels), np.asarray(weights)
    mean = np.sum(x*weights[:, None], axis=0)
    std = np.maximum(np.sqrt(np.sum((x-mean)**2*weights[:, None], axis=0)), .05)
    coef, bias, history = fit_linear(np.clip((x-mean)/std, -8, 8), y, weights, decay, epochs)
    return {'mean': mean, 'std': std, 'coef': coef, 'bias': np.asarray(bias)}, {
        'training_windows': len(y), 'training_positive_windows': int(y.sum()), 'history': history}
