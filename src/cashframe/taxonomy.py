"""Local, supervised visual event types; binary labels never supply a taxonomy.

The centroid baseline is intentionally conservative: it is usable only after
independent source validation and declines ambiguous/out-of-distribution events.
No output from this module confirms a fact or a legal rule.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np

from .data import read_json
from .event_labels import is_specific_type


VERSION = 'cashframe-types-centroid-v1'
FEATURE_VERSION = 'interval-mean-std-time-motion-v1'
MIN_TRAIN_GROUPS = 3
MIN_VALIDATION_GROUPS = 2
MIN_QUALITY = .8
MIN_MARGIN = .2



class InsufficientTypeData(ValueError):
    """Confirmed, visible and independently split visual labels are missing."""


def _fingerprint(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def interval_features(times, values, start, end):
    """Aggregate cached visual vectors inside an interval, without nearest-frame invention."""
    times, values = np.asarray(times, dtype=float), np.asarray(values, dtype=float)
    start, end = float(start), float(end)
    if (times.ndim != 1 or values.ndim != 2 or not len(times) or len(times) != len(values)
            or not values.shape[1] or not np.isfinite(times).all() or not np.isfinite(values).all()
            or not np.isfinite([start, end]).all() or start < 0 or start > end
            or np.any(np.diff(times) <= 0)):
        raise ValueError('Invalid interval or cached feature sequence')
    mask = (times >= start) & (times <= end)
    local, local_times = values[mask], times[mask]
    if not len(local):
        raise ValueError('No cached visual samples inside the annotated interval')
    motion = (np.abs(np.diff(local, axis=0)) / np.diff(local_times)[:, None]).mean(0) if len(local) > 1 else np.zeros(values.shape[1])
    return np.r_[local.mean(0), local.std(0), motion]


def _trusted_sources(manifest, split):
    """Resolve membership using source hashes, including aliases across partitions."""
    membership = {}
    for partition, groups in split.items():
        if not isinstance(groups, list):
            raise ValueError('Split partitions must contain source group lists')
        for gid in groups:
            if gid in membership:
                raise ValueError('Source groups leak across split partitions')
            membership[gid] = partition
    by_source, hashes = {}, {}
    for group in manifest:
        gid, digest = group['group_id'], group['sha256']
        if not isinstance(gid, str) or not gid or Path(gid).name != gid or '/' in gid or '\\' in gid or ':' in gid:
            raise ValueError('Invalid feature-cache source group ID')
        if gid in hashes:
            raise ValueError('Duplicate source group in manifest')
        hashes[gid] = digest
        partition = membership.get(gid, 'external')
        if digest in by_source:
            if by_source[digest][1] != partition:
                raise ValueError('Source hashes leak across split partitions')
            raise ValueError('Duplicate source hash aliases must be grouped before training')
        by_source[digest] = (group, partition)
    if set(membership) - set(hashes):
        raise ValueError('Split contains unknown source groups')
    return by_source


def _load_annotations(annotations_path, manifest, split, feature_dir):
    trusted = _trusted_sources(manifest, split)
    excluded, accepted, seen = Counter(), [], set()
    path = Path(annotations_path)
    if not path.exists():
        raise InsufficientTypeData('No confirmed human visual type annotations: annotation file is absent')
    for line_number, line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f'Invalid annotation JSON at line {line_number}') from exc
        if not isinstance(row, dict):
            raise ValueError(f'Annotation at line {line_number} must be an object')
        source = trusted.get(row.get('source_sha256'))
        if source is None or source[1] not in ('train', 'validation'):
            excluded['withheld_or_external'] += 1
            continue
        label = row.get('type')
        if (row.get('confirmed') is not True or row.get('origin') != 'human'
                or any(not isinstance(row.get(k), str) or not row[k].strip() for k in ('reviewer', 'observed_fact'))
                or not is_specific_type(label)):
            excluded['unconfirmed_or_untyped'] += 1
            continue
        if row.get('visibility') != 'clear':
            excluded['insufficient_visibility'] += 1
            continue
        group, partition = source
        try:
            start, end = float(row['start_sec']), float(row['end_sec'])
            duration = float(group['duration_sec'])
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError(f'Invalid source interval at annotation line {line_number}') from exc
        if not np.isfinite([start, end, duration]).all() or not 0 <= start <= end <= duration:
            raise ValueError(f'Annotation line {line_number} is outside its source interval')
        key = (group['group_id'], start, end)
        if key in seen:
            previous = next(r for r in accepted if (r['group'], r['start'], r['end']) == key)
            if previous['type'] != label.strip():
                raise ValueError('Conflicting human types for the same source interval')
            excluded['duplicate_intervals'] += 1
            continue
        seen.add(key)
        accepted.append({'group': group['group_id'], 'partition': partition, 'type': label.strip(),
                         'start': start, 'end': end})
    types = sorted({row['type'] for row in accepted})
    if len(types) < 2:
        raise InsufficientTypeData('At least two confirmed human visual types with clear visibility are required; binary labels are not types')
    counts = {}
    for label in types:
        counts[label] = {f'{part}_groups': len({r['group'] for r in accepted if r['type'] == label and r['partition'] == part})
                         for part in ('train', 'validation')}
    if any(c['train_groups'] < MIN_TRAIN_GROUPS or c['validation_groups'] < MIN_VALIDATION_GROUPS for c in counts.values()):
        raise InsufficientTypeData('Each human type requires at least 3 independent training sources and 2 independent validation sources with clear visibility')
    # Counts and visibility are checked before opening any feature cache.
    sequences = {}
    for row in accepted:
        gid = row['group']
        if gid not in sequences:
            try:
                with np.load(Path(feature_dir) / f'{gid}.npz', allow_pickle=False) as cached:
                    sequences[gid] = (cached['times'], cached['values'])
            except (OSError, ValueError, KeyError) as exc:
                raise ValueError('Missing or invalid local type-training feature cache') from exc
        row['feature'] = interval_features(*sequences[gid], row['start'], row['end'])
    dimensions = {len(row['feature']) for row in accepted}
    if len(dimensions) != 1:
        raise ValueError('Feature cache dimensions differ across annotated sources')
    return accepted, types, counts, dict(excluded)


def _candidate(feature, model):
    z = (feature - model['mean']) / model['std']
    distances = np.sqrt(np.mean((model['centroids'] - z) ** 2, axis=1))
    order = np.argsort(distances)
    first, second = int(order[0]), int(order[1])
    margin = float((distances[second] - distances[first]) / max(float(distances[second]), 1e-12))
    if distances[first] > model['radii'][first] or margin < MIN_MARGIN:
        return None
    return first, margin


def _validation(rows, labels, model):
    # Every source/type contributes one unit, regardless of repeated intervals.
    group_counts = Counter((r['group'], r['type']) for r in rows)
    confusion = np.zeros((len(labels), len(labels) + 1))
    for row in rows:
        candidate = _candidate(row['feature'], model)
        predicted = len(labels) if candidate is None else candidate[0]
        confusion[labels.index(row['type']), predicted] += 1 / group_counts[(row['group'], row['type'])]
    per_class, f1s = {}, []
    for i, label in enumerate(labels):
        tp = float(confusion[i, i])
        precision = tp / max(float(confusion[:, i].sum()), 1e-12)
        recall = tp / max(float(confusion[i].sum()), 1e-12)
        f1 = 2 * precision * recall / max(precision + recall, 1e-12)
        per_class[label] = {'precision': precision, 'recall': recall, 'f1': f1}
        f1s.append(f1)
    return {'macro_f1': float(np.mean(f1s)), 'coverage': float(confusion[:, :-1].sum() / confusion.sum()),
            'per_class': per_class, 'source_weighted_confusion': confusion.tolist(),
            'source_groups': len({r['group'] for r in rows}), 'annotations': len(rows)}


def _passes_gate(metadata):
    validation, counts = metadata['validation'], metadata['counts']
    return (len(counts) >= 2 and set(counts) == set(metadata['labels'])
            and all(c['train_groups'] >= MIN_TRAIN_GROUPS and c['validation_groups'] >= MIN_VALIDATION_GROUPS for c in counts.values())
            and validation['macro_f1'] >= MIN_QUALITY and validation['coverage'] >= MIN_QUALITY
            and set(validation['per_class']) == set(counts)
            and all(c['precision'] >= MIN_QUALITY and c['recall'] >= MIN_QUALITY for c in validation['per_class'].values()))


def train_types(annotations_path, manifest_path, split_path, feature_dir, artifact_path):
    """Fit from local confirmed labels and trusted train/validation sources only.

    Missing labels never create or replace an artifact. A fitted artifact that
    fails validation remains explicitly inactive; it is a research result only.
    """
    rows, labels, counts, excluded = _load_annotations(annotations_path, read_json(manifest_path), read_json(split_path), feature_dir)
    encoder = read_json(Path(feature_dir) / 'metadata.json').get('encoder')
    if not isinstance(encoder, dict) or not encoder:
        raise ValueError('Feature cache requires exact encoder metadata')
    train = [r for r in rows if r['partition'] == 'train']
    grouped = defaultdict(list)
    for row in train:
        grouped[(row['group'], row['type'])].append(row['feature'])
    group_vectors = {key: np.mean(value, axis=0) for key, value in grouped.items()}
    x = np.stack(list(group_vectors.values()))
    mean, std = x.mean(0), np.maximum(x.std(0), .05)
    centroids = np.stack([np.mean([(v-mean)/std for (_, label), v in group_vectors.items() if label == target], axis=0) for target in labels])
    radii = []
    for i, label in enumerate(labels):
        within = [np.sqrt(np.mean(((r['feature']-mean)/std-centroids[i])**2)) for r in train if r['type'] == label]
        nearest = min(np.sqrt(np.mean((centroids[i]-centroids[j])**2)) for j in range(len(labels)) if j != i)
        radii.append(max(max(within), .01) + .1*nearest)
    model = {'mean': mean, 'std': std, 'centroids': centroids, 'radii': np.asarray(radii)}
    validation = _validation([r for r in rows if r['partition'] == 'validation'], labels, model)
    metadata = {'version': VERSION, 'feature_version': FEATURE_VERSION, 'created_at': datetime.now(timezone.utc).isoformat(),
                'encoder': encoder, 'encoder_sha256': _fingerprint(encoder), 'feature_dimension': len(mean),
                'labels': labels, 'counts': counts, 'excluded': excluded, 'validation': validation,
                'source_disjoint': True, 'selection_partitions': ['train', 'validation'],
                'gates': {'min_train_groups_per_type': MIN_TRAIN_GROUPS, 'min_validation_groups_per_type': MIN_VALIDATION_GROUPS,
                          'min_macro_f1': MIN_QUALITY, 'min_precision_per_type': MIN_QUALITY,
                          'min_recall_per_type': MIN_QUALITY, 'min_coverage': MIN_QUALITY, 'min_margin': MIN_MARGIN},
                'limitations': ['Small source-disjoint validation is not a production accuracy guarantee.',
                                'Camera/scene generalization is unverified.', 'Candidates require authorized human review; no legal mapping.']}
    metadata['active'] = bool(_passes_gate(metadata))
    target = Path(artifact_path)
    if target.suffix.lower() != '.npz':
        raise ValueError('Type artifact path must end in .npz')
    target.parent.mkdir(parents=True, exist_ok=True)
    # One archive avoids mismatched model/metadata sidecars; replacement is atomic.
    temporary = target.with_name(target.name + '.tmp')
    try:
        with temporary.open('wb') as stream:
            np.savez_compressed(stream, metadata=np.asarray(json.dumps(metadata, ensure_ascii=False, allow_nan=False)), **model)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return metadata


def _load_artifact(artifact_path, encoder_config):
    target = Path(artifact_path)
    if not target.exists():
        return None
    try:
        with np.load(target, allow_pickle=False) as archive:
            metadata = json.loads(str(archive['metadata'].item()))
            model = {key: np.asarray(archive[key], dtype=float) for key in ('mean', 'std', 'centroids', 'radii')}
        if metadata['version'] != VERSION or metadata['feature_version'] != FEATURE_VERSION:
            raise ValueError('Unsupported type artifact version')
        d, n = metadata['feature_dimension'], len(metadata['labels'])
        quality = [metadata['validation']['macro_f1'], metadata['validation']['coverage']]
        for metrics in metadata['validation']['per_class'].values():
            quality.extend(metrics[key] for key in ('precision', 'recall', 'f1'))
        if (n < 2 or len(set(metadata['labels'])) != n or model['mean'].shape != (d,)
                or model['std'].shape != (d,) or model['centroids'].shape != (n, d) or model['radii'].shape != (n,)
                or any(not np.isfinite(v).all() for v in model.values())
                or np.any(model['std'] <= 0) or np.any(model['radii'] <= 0)
                or metadata['encoder_sha256'] != _fingerprint(metadata['encoder'])
                or metadata['source_disjoint'] is not True or metadata['selection_partitions'] != ['train', 'validation']
                or not isinstance(metadata['active'], bool)
                or any(not isinstance(v, (int, float)) or isinstance(v, bool) or not np.isfinite(v) or not 0 <= v <= 1 for v in quality)
                or any(not is_specific_type(label) for label in metadata['labels'])
                or any(not isinstance(c[key], int) or isinstance(c[key], bool) or c[key] < 0
                       for c in metadata['counts'].values() for key in ('train_groups', 'validation_groups'))):
            raise ValueError('Invalid type artifact metadata or arrays')
        # Recheck the acceptance criteria rather than trusting an active flag alone.
        active = metadata['active'] is True and _passes_gate(metadata)
        if encoder_config is not None and _fingerprint(encoder_config) != metadata['encoder_sha256']:
            raise ValueError('Type artifact encoder compatibility mismatch')
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
        raise ValueError(f'Invalid type artifact: {exc}') from exc
    if encoder_config is None or not active:
        return None
    return model, metadata


def predict_type(artifact_path, event_feature, encoder_config=None):
    """Return an unconfirmed visual candidate, or None when unsupported.

    Existing malformed/incompatible artifacts raise ValueError. The caller must
    supply its actual encoder config; omitted compatibility evidence declines.
    """
    loaded = _load_artifact(artifact_path, encoder_config)
    if loaded is None:
        return None
    model, metadata = loaded
    feature = np.asarray(event_feature, dtype=float)
    if feature.shape != model['mean'].shape or not np.isfinite(feature).all():
        raise ValueError('Type event feature has invalid dimension or non-finite values')
    candidate = _candidate(feature, model)
    if candidate is None:
        return None
    index, margin = candidate
    return {'type': metadata['labels'][index], 'confidence': margin, 'origin': 'automatic',
            'score_kind': 'uncalibrated_margin',
            'confirmed': False, 'model_version': metadata['version'],
            'artifact_sha256': hashlib.sha256(Path(artifact_path).read_bytes()).hexdigest()}


def classify_interval(artifact_path, times, values, start, end, encoder_config=None):
    """Classify an event interval from already computed local frozen features."""
    if not Path(artifact_path).exists():
        return None
    return predict_type(artifact_path, interval_features(times, values, start, end), encoder_config)
