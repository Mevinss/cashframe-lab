"""Offline temporal candidate. Never reads organizer labels at inference time."""
from collections import defaultdict
from pathlib import Path
import csv
import json

import numpy as np

from .data import read_json
from .event_labels import is_specific_type
from .rules import resolve_rule


ANNOTATION_COLUMNS = ('source_sha256', 'source_file', 'organizer_label', 'organizer_point_sec',
                      'start_sec', 'end_sec', 'event_type', 'visibility',
                      'coverage_complete', 'reviewer', 'confirmed')


def write_review_table(manifest_path, output_path):
    """One row per organizer point; blanks require human review, never inferred labels."""
    manifest = read_json(manifest_path)
    output = Path(output_path)
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=ANNOTATION_COLUMNS)
        writer.writeheader()
        for group in manifest:
            for point in group.get('points_sec') or [None]:
                writer.writerow({'source_sha256': group['sha256'], 'source_file': group['file'],
                                 'organizer_label': group['label'], 'organizer_point_sec': point})
    return len(manifest)


def checked_split(manifest, split):
    by_id = {g['group_id']: g for g in manifest}
    if len(by_id) != len(manifest) or any(g['group_id'] != g['sha256'] for g in manifest):
        raise ValueError('Manifest must contain unique SHA-256 source groups')
    seen = set()
    duplicate_partitions = defaultdict(set)
    for partition in ('train', 'validation', 'test'):
        if partition not in split:
            raise ValueError('Incomplete fixed split')
        for gid in split[partition]:
            if gid not in by_id or gid in seen:
                raise ValueError('Unknown or overlapping source group')
            seen.add(gid)
            # Optional human-reviewed visual duplicate group must stay in one partition.
            duplicate_partitions[by_id[gid].get('duplicate_group', gid)].add(partition)
    if seen != set(by_id) or any(len(parts) > 1 for parts in duplicate_partitions.values()):
        raise ValueError('Missing source or duplicate group crosses partitions')
    return by_id


def read_review_table(path, manifest, split, partitions=('train', 'validation', 'test')):
    """Return only human-confirmed intervals and explicitly reviewed coverage."""
    by_id = checked_split(manifest, split)
    by_hash = {g['sha256']: g for g in manifest}
    permitted = set().union(*(set(split[name]) for name in partitions))
    result = {gid: {'events': [], 'coverage_complete': False} for gid in by_id}
    with Path(path).open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not set(ANNOTATION_COLUMNS) <= set(reader.fieldnames):
            raise ValueError('Review table columns are incomplete')
        for row in reader:
            group = by_hash.get(row['source_sha256'])
            if group is None:
                raise ValueError('Review row has unknown source hash')
            gid = group['group_id']
            if gid not in permitted:
                continue
            if row['organizer_label'] != str(group['label']):
                raise ValueError('Organizer label differs from manifest')
            if row['coverage_complete'] not in ('', 'yes', 'no') or row['confirmed'] not in ('', 'yes', 'no'):
                raise ValueError('Use yes/no for coverage and confirmation')
            if row['coverage_complete'] == 'yes':
                if not row['reviewer'].strip():
                    raise ValueError('Complete coverage requires a reviewer')
                result[gid]['coverage_complete'] = True
            if row['confirmed'] != 'yes':
                continue
            if not row['reviewer'].strip() or row['visibility'] != 'clear':
                raise ValueError('Confirmed event requires reviewer and clear visibility')
            try:
                start, end = float(row['start_sec']), float(row['end_sec'])
            except ValueError as exc:
                raise ValueError('Confirmed event needs start and end seconds') from exc
            if not np.isfinite([start, end]).all() or not 0 <= start < end <= float(group['duration_sec']):
                raise ValueError('Event interval is outside source video')
            category = row['event_type'].strip()
            if category and not is_specific_type(category):
                raise ValueError('Event type is a placeholder, not a specific human label')
            if group['label'] != 1:
                raise ValueError('Event interval conflicts with organizer binary label')
            result[gid]['events'].append({'start_sec': start, 'end_sec': end, 'type': category or None})
    for gid, entry in result.items():
        if gid not in permitted:
            continue
        entry['events'].sort(key=lambda event: event['start_sec'])
        for a, b in zip(entry['events'], entry['events'][1:]):
            if a['end_sec'] > b['start_sec'] and a['type'] != b['type']:
                raise ValueError('Overlapping events with conflicting types')
        if by_id[gid]['label'] == 1 and not entry['events']:
            raise ValueError('Positive source lacks confirmed event interval')
        if by_id[gid]['label'] == 0 and entry['events']:
            raise ValueError('Negative source has confirmed event')
    return result


def temporal_windows(times, values, duration_sec, window_sec=4., stride_sec=1., samples=16):
    """Overlapping ordered DINO sequences spanning the entire source recording."""
    times, values = np.asarray(times, dtype=float), np.asarray(values, dtype=np.float32)
    if (times.ndim != 1 or values.ndim != 2 or len(times) != len(values) or not len(times)
            or np.any(~np.isfinite(times)) or np.any(~np.isfinite(values))
            or np.any(np.diff(times) <= 0) or duration_sec <= 0 or window_sec <= 0
            or stride_sec <= 0 or samples < 2):
        raise ValueError('Invalid ordered feature sequence or window parameters')
    last_start = max(0., duration_sec-window_sec)
    starts = np.unique(np.r_[np.arange(0, last_start+1e-9, stride_sec), last_start])
    ends = np.minimum(starts + window_sec, duration_sec)
    rows = []
    for start, end in zip(starts, ends):
        grid = np.linspace(start, end, samples)
        rows.append(np.stack([np.interp(grid, times, values[:, i]) for i in range(values.shape[1])], axis=1))
    return starts, ends, np.asarray(rows, dtype=np.float32)


def window_targets(starts, ends, review, categories):
    """Unverified positive-video background stays unlabeled (-1)."""
    targets = np.full(len(starts), -1, dtype=np.int64)
    for index, (start, end) in enumerate(zip(starts, ends)):
        inside = [event for event in review['events'] if event['start_sec'] <= (start+end)/2 <= event['end_sec']]
        if len(inside) == 1:
            category = inside[0]['type']
            if categories and category in categories:
                targets[index] = categories.index(category)+1
            elif not categories:
                targets[index] = 1
        elif not inside and review['coverage_complete'] and all(
                end <= event['start_sec']-1 or start >= event['end_sec']+1 for event in review['events']):
            targets[index] = 0
    return targets


def categories_from_reviews(reviews, split):
    all_events = {part: [(gid, event) for gid in split[part] for event in reviews[gid]['events']]
                  for part in ('train', 'validation')}
    train_types = {event['type'] for _, event in all_events['train']}
    if None in train_types or len(train_types) < 2 or any(
            event['type'] not in train_types for _, event in all_events['validation']):
        return []  # Binary event detection only; no invented type taxonomy.
    if any(len({gid for gid, event in all_events[part] if event['type'] == category}) < minimum
           for category in train_types for part, minimum in [('train', 3), ('validation', 2)]):
        return []
    return sorted(train_types)


def merge_predictions(starts, ends, probabilities, threshold, categories, max_gap_sec=1.):
    """Merge adjacent same-category window hits and retain their strongest center."""
    probabilities = np.asarray(probabilities, dtype=float)
    if probabilities.ndim != 2 or probabilities.shape[0] != len(starts) or probabilities.shape[1] != max(2, len(categories)+1):
        raise ValueError('Prediction shape differs from category count')
    active = []
    for i, row in enumerate(probabilities):
        category = int(np.argmax(row[1:]))+1
        if row[category] >= threshold:
            active.append((i, category))
    merged = []
    for index, category in active:
        if merged and merged[-1]['category_index'] == category and starts[index] <= merged[-1]['end_sec']+max_gap_sec:
            event = merged[-1]
            event['end_sec'] = float(ends[index])
            if probabilities[index, category] > event['score']:
                event['score'] = float(probabilities[index, category])
                event['timecode_sec'] = float((starts[index]+ends[index])/2)
        else:
            merged.append({'start_sec': float(starts[index]), 'end_sec': float(ends[index]),
                           'timecode_sec': float((starts[index]+ends[index])/2),
                           'score': float(probabilities[index, category]),
                           'localization_status': 'model_proposal',
                           'category_index': category,
                           'type': categories[category-1] if categories else None})
    return merged


def evidence_times(event, source_times, duration_sec):
    """Choose original timestamps near start, peak, end; JPEG extraction remains local."""
    source = np.asarray(source_times, dtype=float)
    source = source[(source >= 0) & (source < duration_sec)]
    if not len(source):
        return []
    chosen = []
    for target in (event['start_sec'], event['timecode_sec'], event['end_sec']):
        point = float(source[np.argmin(np.abs(source-target))])
        if point not in chosen:
            chosen.append(point)
    return chosen


def explain_event(event, rules, category_rule_ids=None):
    """Only a confirmed category mapping to an enabled verified rule may cite a rule."""
    category = event.get('type')
    mapped = (category_rule_ids or {}).get(category)
    rule = resolve_rule(mapped, rules) if mapped else None
    if rule:
        return {'type': category, 'rule_id': rule['id'],
                'description': f'Модель предполагает событие «{category}». Проверьте применимость правила «{rule["title"]}» и условия видимости вручную.'}
    if category:
        return {'type': category, 'rule_id': None,
                'description': f'Модель предполагает событие «{category}». Тип и применимость правил требуют проверки человеком.'}
    return {'type': 'Визуальный сигнал — тип не подтверждён', 'rule_id': None,
            'description': 'Модель выделила фрагмент для проверки. Тип действия и применимость правил не установлены.'}


def load_cached_sequence(feature_dir, gid):
    with np.load(Path(feature_dir) / (gid+'.npz'), allow_pickle=False) as archive:
        return archive['times'], archive['values']
