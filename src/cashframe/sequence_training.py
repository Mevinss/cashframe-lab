"""Local, opt-in TCN experiment. No service integration or network calls."""
from pathlib import Path
import csv
from collections import Counter, defaultdict
import hashlib
import json
import time

import numpy as np

from .data import read_json, sha256_file, write_json
from .learning import load_model, model_scores, video_score, window_features, metrics, select_threshold
from .events import propose_events
from .sequence_candidate import (categories_from_reviews, checked_split, evidence_times, explain_event,
                                 load_cached_sequence, merge_predictions, read_review_table,
                                 temporal_windows, window_targets)

WINDOW_SEC = 4.
STRIDE_SEC = 1.
SAMPLES = 16
SEED = 42


def make_model(classes):
    import torch
    from torch import nn
    class TinyTCN(nn.Module):
        def __init__(self):
            super().__init__()
            self.net = nn.Sequential(nn.Conv1d(384, 32, 1), nn.ReLU(),
                                     nn.Conv1d(32, 32, 3, padding=1, groups=32), nn.ReLU(),
                                     nn.Conv1d(32, 32, 3, padding=2, dilation=2, groups=32), nn.ReLU(),
                                     nn.AdaptiveAvgPool1d(1), nn.Flatten(), nn.Linear(32, classes))
        def forward(self, x):
            return self.net(x.transpose(1, 2))
    return TinyTCN()


def _fingerprint(path):
    return sha256_file(path)


def baseline_artifact_hashes(baseline_dir):
    directory = Path(baseline_dir)
    result = {path.relative_to(directory).as_posix(): _fingerprint(path)
              for path in sorted(directory.rglob('*'))
              if path.is_file() and path.suffix.lower() in ('.json', '.npz')}
    if 'config.json' not in result:
        raise ValueError('Baseline configuration is missing')
    return result


def _review_training_hash(review_path, manifest, split):
    allowed = set(split['train']+split['validation'])
    hashes = {g['sha256'] for g in manifest if g['group_id'] in allowed}
    with Path(review_path).open(newline='', encoding='utf-8-sig') as stream:
        rows = [row for row in csv.DictReader(stream) if row.get('source_sha256') in hashes]
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def _load_inputs(manifest_path, split_path, feature_dir, review_path, partitions=('train', 'validation', 'test'),
                 supervision='human_confirmed'):
    manifest, split = read_json(manifest_path), read_json(split_path)
    by_id = checked_split(manifest, split)
    if supervision == 'human_confirmed':
        reviews = read_review_table(review_path, manifest, split, partitions=partitions)
    elif supervision == 'weak_reviewed_boundaries':
        from .weak_review import load_weak_review
        reviews = load_weak_review(review_path, manifest, split, partitions=partitions)
    else:
        raise ValueError('Unsupported supervision mode')
    metadata = read_json(Path(feature_dir)/'metadata.json')
    if not isinstance(metadata.get('encoder'), dict):
        raise ValueError('Cached DINO encoder metadata is missing')
    return manifest, split, by_id, reviews, metadata


def _part(feature_dir, ids, by_id, reviews, categories):
    arrays, labels, owners, windows_by_source = [], [], [], {}
    for gid in ids:
        group = by_id[gid]
        times, values = load_cached_sequence(feature_dir, gid)
        if values.shape[1] != 384:
            raise ValueError('Expected 384-dimensional DINOv2 embeddings')
        starts, ends, windows = temporal_windows(times, values, group['duration_sec'], WINDOW_SEC, STRIDE_SEC, SAMPLES)
        targets = window_targets(starts, ends, reviews[gid], categories)
        windows_by_source[gid] = (starts, ends, windows, times, values)
        mask = targets >= 0
        if np.any(mask):
            arrays.append(windows[mask]); labels.append(targets[mask]); owners.extend([gid]*int(mask.sum()))
    if not arrays:
        raise ValueError('No human-reviewed windows available')
    return np.concatenate(arrays), np.concatenate(labels), owners, windows_by_source


def _requirements(split, by_id, reviews, categories):
    for partition in ('train', 'validation'):
        if {by_id[gid]['label'] for gid in split[partition]} != {0, 1}:
            raise ValueError(f'{partition} needs positive and negative source groups')
        for gid in split[partition]:
            if by_id[gid]['label'] == 0 and not reviews[gid]['coverage_complete']:
                raise ValueError(f'{partition} negative video needs full human coverage review')
    if categories:
        for category in categories:
            counts = [len({gid for gid in split[partition] if any(e['type'] == category for e in reviews[gid]['events'])})
                      for partition in ('train', 'validation')]
            if counts[0] < 3 or counts[1] < 2:
                raise ValueError('Each event type needs 3 train and 2 validation source groups')
        for partition in ('train', 'validation'):
            if any(e['type'] not in categories for gid in split[partition] for e in reviews[gid]['events']):
                raise ValueError('Validation has event type absent from training')


def source_balanced_weights(owners, labels):
    """Equal class mass, then equal source mass within each class."""
    labels = np.asarray(labels)
    if labels.ndim != 1 or not len(labels) or len(owners) != len(labels):
        raise ValueError('Aligned nonempty source owners and class labels required')
    if any(not isinstance(owner, str) or not owner for owner in owners):
        raise ValueError('Nonempty source identifiers required')
    pairs = Counter(zip(owners, labels.tolist()))
    class_sources = Counter(label for owner, label in pairs)
    weights = np.array([1./(class_sources[label]*pairs[(owner, label)])
                        for owner, label in zip(owners, labels.tolist())], dtype=np.float64)
    return weights/weights.sum()


def _recall_guarded_threshold(scores, labels, minimum_recall):
    from .research import binary_metrics
    scores = np.asarray(scores, dtype=float)
    if scores.ndim != 1 or not len(scores) or not np.isfinite(scores).all():
        raise ValueError('Finite validation scores required')
    eligible = []
    for threshold in np.unique(scores):
        result = binary_metrics(labels, scores >= threshold)
        if result['recall'] >= minimum_recall:
            eligible.append(((result['balanced_accuracy'], result['f1'], result['recall'], float(threshold)),
                             float(threshold), result))
    if not eligible:
        raise ValueError('No validation threshold preserves baseline recall')
    _, threshold, result = max(eligible, key=lambda item: item[0])
    return threshold, result


def train(manifest_path, split_path, feature_dir, review_path, output_dir, baseline_dir, *,
          supervision='human_confirmed'):
    """Fit only train features; use validation for stopping/threshold; never open test features."""
    if supervision not in ('human_confirmed', 'weak_reviewed_boundaries'):
        raise ValueError('Unsupported supervision mode')
    import torch
    from torch.nn import functional as F
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise FileExistsError('Candidate output exists; frozen experiments are never overwritten')
    manifest, split, by_id, reviews, feature_meta = _load_inputs(manifest_path, split_path, feature_dir, review_path,
                                                                 partitions=('train', 'validation'),
                                                                 supervision=supervision)
    baseline_hashes = baseline_artifact_hashes(baseline_dir)
    base_config = read_json(Path(baseline_dir)/'config.json')
    if base_config['split'] != split or base_config['encoder'] != feature_meta['encoder']:
        raise ValueError('Candidate and frozen baseline must share split and encoder')
    # Source-level positive labels plus reviewed action boundaries do not supply
    # legal event types or human confirmation of each target event.
    weak = supervision == 'weak_reviewed_boundaries'
    categories = [] if weak else categories_from_reviews(reviews, split)
    feature_hashes = {gid: _fingerprint(Path(feature_dir)/(gid+'.npz'))
                      for partition in ('train', 'validation') for gid in split[partition]}
    input_paths = [Path(manifest_path), Path(split_path), Path(feature_dir)/'metadata.json',
                   Path(baseline_dir)/'config.json']
    input_paths += [Path(baseline_dir)/name for name in baseline_hashes]
    if weak:
        input_paths += [Path(review_path)/name for name in
                        ('metadata.json', 'manifest.json', 'split.json', 'events-review.csv', 'review-coverage.csv')]
    else:
        input_paths.append(Path(review_path))
    input_hashes = {str(path.resolve()): _fingerprint(path) for path in input_paths}
    _requirements(split, by_id, reviews, categories)
    x_train, y_train, owners, _ = _part(feature_dir, split['train'], by_id, reviews, categories)
    x_val, y_val, validation_owners, val_sources = _part(feature_dir, split['validation'], by_id, reviews, categories)
    if len(set(y_train)) < 2 or len(set(y_val)) < 2:
        raise ValueError('Insufficient reviewed windows in both classes')
    mean = x_train.reshape(-1, 384).mean(0)
    std = np.maximum(x_train.reshape(-1, 384).std(0), .05)
    x_train = np.clip((x_train-mean)/std, -8, 8).astype(np.float32)
    x_val = np.clip((x_val-mean)/std, -8, 8).astype(np.float32)
    torch.manual_seed(SEED); torch.set_num_threads(2)
    model = make_model(max(2, len(categories)+1))
    optimizer = torch.optim.AdamW(model.parameters(), lr=.001, weight_decay=.01)
    tx, ty = torch.from_numpy(x_train), torch.from_numpy(y_train)
    vx, vy = torch.from_numpy(x_val), torch.from_numpy(y_val)
    weights = torch.from_numpy(source_balanced_weights(owners, y_train).astype(np.float32))
    validation_weights = torch.from_numpy(source_balanced_weights(validation_owners, y_val).astype(np.float32))
    best_loss, best_state, patience, history = float('inf'), None, 0, []
    best_epoch, train_history, unweighted_history = 0, [], []
    start = time.perf_counter()
    for epoch in range(60):
        model.train()
        for indices in torch.randperm(len(tx)).split(64):
            optimizer.zero_grad()
            loss = (F.cross_entropy(model(tx[indices]), ty[indices], reduction='none')*weights[indices]).sum()
            loss.backward(); optimizer.step()
        model.eval()
        with torch.no_grad():
            validation_losses = F.cross_entropy(model(vx), vy, reduction='none')
            val_loss = float((validation_losses*validation_weights).sum())
            unweighted_history.append(float(validation_losses.mean()))
            train_history.append(float((F.cross_entropy(model(tx), ty, reduction='none')*weights).sum()))
        history.append(val_loss)
        if val_loss < best_loss-1e-4:
            best_loss, patience = val_loss, 0
            best_epoch = epoch+1
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
        else:
            patience += 1
            if patience >= 8:
                break
    model.load_state_dict(best_state); model.eval()
    val_scores = []
    with torch.no_grad():
        for gid in split['validation']:
            windows = val_sources[gid][2]
            normalized = np.clip((windows-mean)/std, -8, 8).astype(np.float32)
            probability = model(torch.from_numpy(normalized)).softmax(1).numpy()
            val_scores.append(float(probability[:, 1:].max()))
    labels = [by_id[gid]['label'] for gid in split['validation']]
    baseline_validation = None
    if weak:
        from .research import binary_metrics
        base_predictions = [baseline_cached_events(baseline_dir, val_sources[gid][3], val_sources[gid][4],
                                                   by_id[gid]['duration_sec'], feature_meta['encoder'])[1]
                            for gid in split['validation']]
        baseline_validation = binary_metrics(labels, base_predictions)
        threshold, val_metrics = _recall_guarded_threshold(val_scores, labels, baseline_validation['recall'])
    else:
        threshold, val_metrics = select_threshold(val_scores, labels)
    if any(_fingerprint(Path(feature_dir)/(gid+'.npz')) != digest for gid, digest in feature_hashes.items()):
        raise ValueError('Feature cache changed while training')
    if any(_fingerprint(path) != digest for path, digest in input_hashes.items()):
        raise ValueError('Training inputs changed while fitting')
    if baseline_artifact_hashes(baseline_dir) != baseline_hashes:
        raise ValueError('Baseline artifacts changed while fitting')
    output_dir.mkdir(parents=True)
    torch.save(best_state, output_dir/'weights.pt')
    np.savez_compressed(output_dir/'normalization.npz', mean=mean, std=std)
    record = {'version': 1, 'architecture': 'tiny-tcn-ordered-dino', 'classes': categories,
              'type_status': 'trained' if categories else 'binary_only_insufficient_confirmed_types',
              'threshold': threshold, 'window_sec': WINDOW_SEC, 'stride_sec': STRIDE_SEC,
              'samples': SAMPLES, 'seed': SEED, 'epochs': len(history),
              'validation': val_metrics, 'validation_loss': best_loss,
              'fit_wall_sec': time.perf_counter()-start,
              'split_sha256': _fingerprint(split_path), 'manifest_sha256': _fingerprint(manifest_path),
              'training_review_sha256': (_fingerprint(Path(review_path)/'metadata.json') if weak else
                                        _review_training_hash(review_path, manifest, split)),
              'encoder': feature_meta['encoder'],
              'baseline_config_sha256': _fingerprint(Path(baseline_dir)/'config.json'),
              'baseline_artifact_sha256': baseline_hashes,
              'selection_partitions': ['train', 'validation'], 'test_opened_for_selection': False,
              'weights_sha256': _fingerprint(output_dir/'weights.pt'),
              'normalization_sha256': _fingerprint(output_dir/'normalization.npz'),
              'supervision': supervision, 'human_target_confirmation': not weak,
              'legal_applicability': False, 'promotion_performed': False,
              'baseline_validation': baseline_validation,
              'early_stopping_objective': 'source_class_balanced_cross_entropy',
              'best_epoch': best_epoch, 'validation_loss_history': history,
              'validation_unweighted_loss_history': unweighted_history, 'train_loss_history': train_history,
              'binary_gate_passed': bool(weak and val_metrics['balanced_accuracy'] > baseline_validation['balanced_accuracy']
                                        and val_metrics['recall'] >= baseline_validation['recall']),
              'train_validation_feature_sha256': feature_hashes,
              'input_sha256': input_hashes,
              'note': ('Weak event proposals use organizer video labels and human action boundaries; '
                       'they are not human-confirmed legal violations. ' if weak else '') +
                      'Validation is small; held-out test was previously inspected in older experiments.'}
    write_json(output_dir/'frozen.json', record)
    return {'status': 'frozen', 'classes': len(categories), 'validation': val_metrics,
            'epochs': len(history), 'fit_wall_sec': record['fit_wall_sec']}


def load_candidate(directory, encoder=None):
    import torch
    directory = Path(directory)
    record = read_json(directory/'frozen.json')
    if record['version'] != 1 or record['selection_partitions'] != ['train', 'validation']:
        raise ValueError('Unsupported or unfrozen candidate')
    if encoder is not None and record['encoder'] != encoder:
        raise ValueError('Candidate encoder mismatch')
    for filename, key in [('weights.pt', 'weights_sha256'), ('normalization.npz', 'normalization_sha256')]:
        if _fingerprint(directory/filename) != record[key]:
            raise ValueError('Candidate artifact hash mismatch')
    with np.load(directory/'normalization.npz', allow_pickle=False) as data:
        mean, std = data['mean'], data['std']
    if mean.shape != (384,) or std.shape != (384,) or np.any(std <= 0):
        raise ValueError('Invalid candidate normalization')
    model = make_model(max(2, len(record['classes'])+1))
    model.load_state_dict(torch.load(directory/'weights.pt', map_location='cpu', weights_only=True))
    model.eval()
    return model, mean, std, record


def predict(model, mean, std, times, values, duration_sec, record, source_times=None,
            category_rule_ids=None):
    import torch
    starts, ends, windows = temporal_windows(times, values, duration_sec,
                                            record['window_sec'], record['stride_sec'], record['samples'])
    normalized = np.clip((windows-mean)/std, -8, 8).astype(np.float32)
    with torch.no_grad():
        probabilities = model(torch.from_numpy(normalized)).softmax(1).numpy()
    events = merge_predictions(starts, ends, probabilities, record['threshold'], record['classes'])
    covered = sum(event['end_sec']-event['start_sec'] for event in events)
    if events and (covered >= .8*duration_sec or len(events) > 12 or
                   np.ptp(probabilities[:, 1:].max(axis=1)) <= 1e-6):
        events = [{'start_sec': 0., 'end_sec': float(duration_sec), 'timecode_sec': 0.,
                   'score': float(probabilities[:, 1:].max()), 'type': None,
                   'localization_status': 'unresolved'}]
    registry = read_json(Path(__file__).resolve().parents[2]/'config'/'rules.json')['rules']
    for event in events:
        clock = source_times if source_times is not None else times
        if event['localization_status'] == 'unresolved':
            context = {'start_sec': 0., 'timecode_sec': duration_sec/2, 'end_sec': duration_sec}
            event['evidence_times_sec'] = evidence_times(context, clock, duration_sec)
        else:
            event['evidence_times_sec'] = evidence_times(event, clock, duration_sec)
        event.update(explain_event(event, registry, category_rule_ids))
    return events, float(probabilities[:, 1:].max())


def baseline_cached_events(baseline_dir, times, values, duration_sec, encoder_config=None):
    """Reproduce the configured baseline on cached features, without raw refinement."""
    from .learning import inference_window_parameters
    baseline, config, threshold = load_model(baseline_dir)
    if encoder_config is not None and config['encoder'] != encoder_config:
        raise ValueError('Baseline encoder differs from cached features')
    centers, windows = window_features(times, values, duration_sec, **inference_window_parameters(config))
    scores = model_scores(baseline, windows)
    if config.get('video_classifier'):
        if config['video_classifier'] != 'selected.npz':
            raise ValueError('Unsupported baseline video classifier artifact')
        from .research import load_frozen, score_videos, meets_threshold
        classifier, video_threshold, _ = load_frozen(baseline_dir, expected_encoder=config['encoder'])
        positive = bool(meets_threshold(score_videos(classifier, [windows])[0], video_threshold))
    else:
        positive = bool(video_score(baseline, windows) >= threshold)
    if config.get('point_localizer'):
        if config['point_localizer'] != 'point-localizer.npz':
            raise ValueError('Unsupported baseline point localizer artifact')
        from .point_localization import localized_events
        events = localized_events(baseline_dir, times, values, duration_sec, positive, config['encoder'])
    else:
        event_threshold = config.get('event_threshold', threshold) if config['selected_variant'] == 'trained' else threshold
        events = propose_events(centers, scores, event_threshold, duration_sec, positive)
    return events, positive


def _point_metric_summary(counts):
    result = dict(counts)
    matched = result['matched']
    result['precision'] = matched / max(1, result['predictions'])
    result['recall'] = matched / max(1, result['reference_points'])
    result['f1'] = 2 * matched / max(1, result['predictions'] + result['reference_points'])
    result['mean_absolute_error_sec'] = result['absolute_error_sum_sec'] / matched if matched else None
    return result


def temporal_point_metrics(events, points, tolerance_sec):
    """One-to-one weak-point diagnosis; unmatched proposals are not verified violations.

    Sorted dynamic programming maximizes the match count, then minimizes summed
    absolute error. An unresolved whole-video signal cannot match any point.
    """
    tolerance_sec = float(tolerance_sec)
    truth = np.asarray(points, dtype=float)
    if (not np.isfinite(tolerance_sec) or tolerance_sec < 0 or truth.ndim != 1
            or not np.isfinite(truth).all() or np.any(truth < 0)):
        raise ValueError('Finite nonnegative reference points and tolerance required')
    predicted = []
    for event in events:
        if event.get('localization_status') == 'unresolved':
            continue
        point = float(event['timecode_sec'])
        if not np.isfinite(point) or point < 0:
            raise ValueError('Finite nonnegative predicted timestamps required')
        predicted.append(point)
    predicted.sort()
    previous = [(0, 0.)] * (len(predicted) + 1)
    for point in np.sort(truth):
        current = [(0, 0.)]
        for index, prediction in enumerate(predicted, 1):
            alternatives = [previous[index], current[index - 1]]
            error = abs(float(point) - prediction)
            if error <= tolerance_sec:
                count, distance = previous[index - 1]
                alternatives.append((count + 1, distance + error))
            current.append(max(alternatives, key=lambda row: (row[0], -row[1])))
        previous = current
    matched, error_sum = previous[-1]
    return _point_metric_summary({'tolerance_sec': tolerance_sec, 'reference_points': len(truth),
                                 'predictions': len(events), 'localized_predictions': len(predicted),
                                 'unresolved_predictions': len(events) - len(predicted),
                                 'matched': matched, 'missed': len(truth) - matched,
                                 'extra': len(events) - matched, 'absolute_error_sum_sec': error_sum})


def evaluate(manifest_path, split_path, feature_dir, review_path, candidate_dir, baseline_dir, output_path):
    """One frozen comparison on the same source groups. Reports aggregates only."""
    output_path = Path(output_path)
    if output_path.exists():
        raise FileExistsError(output_path)
    manifest, split, by_id, reviews, feature_meta = _load_inputs(manifest_path, split_path, feature_dir, review_path)
    model, mean, std, frozen = load_candidate(candidate_dir, feature_meta['encoder'])
    if (_fingerprint(manifest_path) != frozen['manifest_sha256'] or
            _fingerprint(split_path) != frozen['split_sha256'] or
            _review_training_hash(review_path, manifest, split) != frozen['training_review_sha256']):
        raise ValueError('Evaluation data differs from frozen selection')
    _, base_config, _ = load_model(baseline_dir)
    if base_config['split'] != split or base_config['encoder'] != feature_meta['encoder']:
        raise ValueError('Baseline and candidate do not share source split and encoder')
    totals = {name: {'truth': [], 'predicted': [], 'event_fp': 0,
                     'point_localization': {tolerance: temporal_point_metrics([], [], tolerance)
                                            for tolerance in (2., 5.)}}
              for name in ('baseline', 'candidate')}
    coverage_count, type_correct, type_total = 0, 0, 0
    type_counts = defaultdict(lambda: {'tp': 0, 'fp': 0, 'fn': 0})
    start = time.perf_counter()
    for gid in split['test']:
        group = by_id[gid]
        times, values = load_cached_sequence(feature_dir, gid)
        duration = group['duration_sec']
        candidate_events, candidate_score = predict(model, mean, std, times, values, duration, frozen)
        baseline_events, base_positive = baseline_cached_events(baseline_dir, times, values, duration, feature_meta['encoder'])
        original_points = group.get('points_sec') or ([group['point_sec']] if group.get('point_sec') is not None else [])
        if reviews[gid]['coverage_complete']:
            coverage_count += 1
        for name, events, positive in [('baseline', baseline_events, base_positive),
                                       ('candidate', candidate_events, candidate_score >= frozen['threshold'])]:
            row = totals[name]
            row['truth'].append(group['label']); row['predicted'].append(int(positive))
            for tolerance, counts in row['point_localization'].items():
                measured = temporal_point_metrics(events, original_points, tolerance)
                for key in ('reference_points', 'predictions', 'localized_predictions', 'unresolved_predictions',
                            'matched', 'missed', 'extra', 'absolute_error_sum_sec'):
                    counts[key] += measured[key]
            if not group['label'] and reviews[gid]['coverage_complete']:
                row['event_fp'] += len(events)
        if frozen['classes']:
            used = set()
            for truth in reviews[gid]['events']:
                if truth['type'] not in frozen['classes']:
                    continue
                matches = [i for i, event in enumerate(candidate_events) if i not in used and
                           event.get('localization_status') != 'unresolved' and
                           truth['start_sec'] <= event['timecode_sec'] <= truth['end_sec']]
                if not matches:
                    type_counts[truth['type']]['fn'] += 1
                    continue
                index = max(matches, key=lambda i: candidate_events[i]['score'])
                used.add(index)
                predicted_type = candidate_events[index]['type']
                type_total += 1
                if predicted_type == truth['type']:
                    type_correct += 1
                    type_counts[truth['type']]['tp'] += 1
                else:
                    type_counts[truth['type']]['fn'] += 1
                    type_counts[predicted_type]['fp'] += 1
            if reviews[gid]['coverage_complete']:
                for i, event in enumerate(candidate_events):
                    if i not in used and event['type'] in frozen['classes']:
                        type_counts[event['type']]['fp'] += 1
    type_f1 = []
    for category in frozen['classes']:
        counts = type_counts[category]
        precision = counts['tp']/max(1, counts['tp']+counts['fp'])
        recall = counts['tp']/max(1, counts['tp']+counts['fn'])
        type_f1.append(2*precision*recall/max(1e-12, precision+recall))
    report = {'partition': 'test_previously_inspected_in_prior_experiments',
              'source_groups': len(split['test']), 'fully_reviewed_groups': coverage_count,
              'comparison': {}, 'candidate_type_accuracy_on_matched': type_correct/type_total if type_total else None,
              'candidate_type_matched_count': type_total,
              'candidate_type_macro_f1': float(np.mean(type_f1)) if type_f1 else None,
              'cached_scoring_wall_sec': time.perf_counter()-start,
              'full_analysis_sec': None,
              'full_analysis_note': 'Requires original videos and local end-to-end timing of both pipelines.',
              'localization_note': 'One-to-one matching to original organizer points at +/-2 and +/-5 seconds; these are weak point references. Extra predictions are relative to those points, not verified false events. Cached features omit runtime refinement and JPEG timestamp adjustment.',
              'warning': 'No test feature was read during candidate selection; these test sources had been viewed in prior work.'}
    for name, row in totals.items():
        score = metrics(row['truth'], row['predicted'])
        report['comparison'][name] = {'misses': score['confusion_matrix'][1][0],
                                      'false_alarm_videos': score['confusion_matrix'][0][1],
                                      'false_alarm_events_on_fully_reviewed_negative_videos': row['event_fp'],
                                      'type_macro_f1': float(np.mean(type_f1)) if name == 'candidate' and type_f1 else None,
                                      'point_localization': {'reference': 'original_organizer_points_weak_labels',
                                          **{f'{int(tolerance)}_sec': _point_metric_summary(counts)
                                             for tolerance, counts in row['point_localization'].items()}}}
    write_json(output_path, report)
    return report


def benchmark_raw(manifest_path, split_path, candidate_dir, baseline_dir, encoder_dir, output_path):
    """Local full-video timing, including decode, DINO, inference and evidence."""
    from .features import Encoder, extract_features
    from .pipeline import analyze_video, block_outgoing_network
    from .video import evidence_frames, probe_video, validate_decode
    output_path = Path(output_path)
    if output_path.exists():
        raise FileExistsError(output_path)
    manifest, split = read_json(manifest_path), read_json(split_path)
    by_id = checked_split(manifest, split)
    frozen = read_json(Path(candidate_dir)/'frozen.json')
    if _fingerprint(manifest_path) != frozen['manifest_sha256'] or _fingerprint(split_path) != frozen['split_sha256']:
        raise ValueError('Raw benchmark sources differ from frozen selection')
    block_outgoing_network()
    times = {'baseline': [], 'candidate': []}
    for gid in split['test']:
        group = by_id[gid]
        path = Path(group['path'])
        if not path.is_file() or sha256_file(path) != group['sha256']:
            raise ValueError('Original video missing or changed')
        start = time.perf_counter()
        analyze_video(path, baseline_dir, Path(candidate_dir)/'benchmark-work', encoder_dir=encoder_dir)
        times['baseline'].append(time.perf_counter()-start)
        start = time.perf_counter()
        info = probe_video(path)
        validate_decode(path)
        model, mean, std, record = load_candidate(candidate_dir)
        encoder = Encoder(encoder_dir)
        if record['encoder'] != encoder.config:
            raise ValueError('Benchmark encoder mismatch')
        sequence = extract_features(path, encoder)
        original_times = np.asarray(sequence.source_times, dtype=float)
        events, _ = predict(model, mean, std, sequence.times, sequence.values, info.duration_sec, record,
                            source_times=original_times-original_times[0])
        for event in events:
            points = event['evidence_times_sec']
            if points:
                evidence_frames(path, points, times=sequence.source_times)
        times['candidate'].append(time.perf_counter()-start)
    report = {'partition': 'test_previously_inspected_in_prior_experiments',
              'source_groups': len(split['test']),
              'full_analysis_sec': {name: {'mean': float(np.mean(values)), 'max': float(np.max(values)),
                                           'total': float(np.sum(values))} for name, values in times.items()},
              'measurements_include': ['full decode', 'DINO extraction', 'classifier', 'JPEG evidence'],
              'note': 'Sequential local CPU timing; baseline may perform additional interval refinement.'}
    write_json(output_path, report)
    return report
