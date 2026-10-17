"""Local, opt-in TCN experiment. No service integration or network calls."""
from pathlib import Path
import csv
from collections import defaultdict
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


def _review_training_hash(review_path, manifest, split):
    allowed = set(split['train']+split['validation'])
    hashes = {g['sha256'] for g in manifest if g['group_id'] in allowed}
    with Path(review_path).open(newline='', encoding='utf-8-sig') as stream:
        rows = [row for row in csv.DictReader(stream) if row.get('source_sha256') in hashes]
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def _load_inputs(manifest_path, split_path, feature_dir, review_path, partitions=('train', 'validation', 'test')):
    manifest, split = read_json(manifest_path), read_json(split_path)
    by_id = checked_split(manifest, split)
    reviews = read_review_table(review_path, manifest, split, partitions=partitions)
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


def train(manifest_path, split_path, feature_dir, review_path, output_dir, baseline_dir):
    """Fit only train features; use validation for stopping/threshold; never open test features."""
    import torch
    from torch.nn import functional as F
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise FileExistsError('Candidate output exists; frozen experiments are never overwritten')
    manifest, split, by_id, reviews, feature_meta = _load_inputs(manifest_path, split_path, feature_dir, review_path,
                                                                 partitions=('train', 'validation'))
    base_config = read_json(Path(baseline_dir)/'config.json')
    if base_config['split'] != split or base_config['encoder'] != feature_meta['encoder']:
        raise ValueError('Candidate and frozen baseline must share split and encoder')
    categories = categories_from_reviews(reviews, split)
    _requirements(split, by_id, reviews, categories)
    x_train, y_train, owners, _ = _part(feature_dir, split['train'], by_id, reviews, categories)
    x_val, y_val, _, val_sources = _part(feature_dir, split['validation'], by_id, reviews, categories)
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
    class_sources = {label: len({owner for owner, target in zip(owners, y_train) if target == label}) for label in set(y_train)}
    source_windows = {(owner, int(label)): sum(o == owner and y == label for o, y in zip(owners, y_train))
                      for owner, label in zip(owners, y_train)}
    weights = torch.tensor([1/(class_sources[int(y)]*source_windows[(owner, int(y))])
                            for owner, y in zip(owners, y_train)], dtype=torch.float32)
    weights /= weights.sum()
    best_loss, best_state, patience, history = float('inf'), None, 0, []
    start = time.perf_counter()
    for epoch in range(60):
        model.train()
        for indices in torch.randperm(len(tx)).split(64):
            optimizer.zero_grad()
            loss = (F.cross_entropy(model(tx[indices]), ty[indices], reduction='none')*weights[indices]).sum()
            loss.backward(); optimizer.step()
        model.eval()
        with torch.no_grad():
            val_loss = float(F.cross_entropy(model(vx), vy))
        history.append(val_loss)
        if val_loss < best_loss-1e-4:
            best_loss, patience = val_loss, 0
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
    threshold, val_metrics = select_threshold(val_scores, [by_id[gid]['label'] for gid in split['validation']])
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
              'training_review_sha256': _review_training_hash(review_path, manifest, split),
              'encoder': feature_meta['encoder'],
              'baseline_config_sha256': _fingerprint(Path(baseline_dir)/'config.json'),
              'selection_partitions': ['train', 'validation'], 'test_opened_for_selection': False,
              'weights_sha256': _fingerprint(output_dir/'weights.pt'),
              'normalization_sha256': _fingerprint(output_dir/'normalization.npz'),
              'note': 'Validation is small; held-out test was previously inspected in older experiments.'}
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


def _timecode_error(events, truth):
    if not events or not truth:
        return None
    # Zero error means a peak lies inside a human-marked interval.
    errors = [min(max(start-event['timecode_sec'], 0, event['timecode_sec']-end)
                  for start, end in truth) for event in events]
    return float(min(errors))


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
    baseline, base_config, base_threshold = load_model(baseline_dir)
    if base_config['split'] != split or base_config['encoder'] != feature_meta['encoder']:
        raise ValueError('Baseline and candidate do not share source split and encoder')
    totals = {'baseline': {'truth': [], 'predicted': [], 'errors': [], 'event_fp': 0},
              'candidate': {'truth': [], 'predicted': [], 'errors': [], 'event_fp': 0}}
    coverage_count, type_correct, type_total = 0, 0, 0
    type_counts = defaultdict(lambda: {'tp': 0, 'fp': 0, 'fn': 0})
    start = time.perf_counter()
    for gid in split['test']:
        group = by_id[gid]
        times, values = load_cached_sequence(feature_dir, gid)
        duration = group['duration_sec']
        candidate_events, candidate_score = predict(model, mean, std, times, values, duration, frozen)
        centers, windows = window_features(times, values, duration)
        scores = model_scores(baseline, windows)
        base_positive = video_score(baseline, windows) >= base_threshold
        event_threshold = base_config.get('event_threshold', base_threshold) if base_config['selected_variant'] == 'trained' else base_threshold
        baseline_events = propose_events(centers, scores, event_threshold, duration, base_positive)
        true_intervals = [(e['start_sec'], e['end_sec']) for e in reviews[gid]['events']]
        if reviews[gid]['coverage_complete']:
            coverage_count += 1
        for name, events, positive in [('baseline', baseline_events, base_positive),
                                       ('candidate', candidate_events, candidate_score >= frozen['threshold'])]:
            row = totals[name]
            row['truth'].append(group['label']); row['predicted'].append(int(positive))
            if group['label'] and true_intervals and positive:
                localized = [event for event in events if event.get('localization_status') != 'unresolved']
                error = _timecode_error(localized, true_intervals)
                if error is not None: row['errors'].append(error)
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
              'localization_note': 'Cached-feature proxy; baseline runtime refinement and JPEG timestamp adjustment require raw-video evaluation.',
              'warning': 'No test feature was read during candidate selection; these test sources had been viewed in prior work.'}
    for name, row in totals.items():
        score = metrics(row['truth'], row['predicted'])
        report['comparison'][name] = {'misses': score['confusion_matrix'][1][0],
                                      'false_alarm_videos': score['confusion_matrix'][0][1],
                                      'false_alarm_events_on_fully_reviewed_negative_videos': row['event_fp'],
                                      'type_macro_f1': float(np.mean(type_f1)) if name == 'candidate' and type_f1 else None,
                                      'timecode_error_mean_sec_on_detected_annotated_videos': float(np.mean(row['errors'])) if row['errors'] else None,
                                      'timecode_evaluated_videos': len(row['errors'])}
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
