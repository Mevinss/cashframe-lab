"""Standalone temporal evaluation; standard library only, no production imports."""

import argparse
from fractions import Fraction
import json
import math
from pathlib import Path
import sys


class InputError(ValueError):
    """A deliberately value-free validation error, safe for CLI diagnostics."""


def _object(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or not set(required) <= set(value):
        raise InputError('Invalid structure or unsupported fields; use the sanitized contract.')


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _time(value, duration):
    return _number(value) and 0 <= value <= duration


def _interval(item, duration):
    a, b = item.get('start_sec'), item.get('end_sec')
    return (a, b) if _time(a, duration) and _time(b, duration) and a < b else None


def temporal_iou(first, second):
    """IoU of two finite, positive-duration intervals; points are rejected."""
    for interval in (first, second):
        if len(interval) != 2 or not all(_number(x) for x in interval) or not 0 <= interval[0] < interval[1]:
            raise InputError('IoU requires two valid positive-duration intervals.')
    intersection = max(0, min(first[1], second[1]) - max(first[0], second[0]))
    # Span equals union for overlapping intervals; disjoint intervals have IoU 0.
    return intersection / (max(first[1], second[1]) - min(first[0], second[0]))


def _canonical(item):
    """Content-based ordering, including evidence, never input list indices."""
    return json.dumps(item, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def _matching(edges, qualities=None):
    """Exact min-cost max-flow: cardinality first, total quality second.

    Fraction costs avoid epsilon tie rules. Canonically ordered caller input and
    strict shortest-path updates resolve genuinely equal optima reproducibly.
    """
    truth_count = 1 + max((t for row in edges for t in row), default=-1)
    source, sink = len(edges) + truth_count, len(edges) + truth_count + 1
    graph = [[] for _ in range(sink + 1)]
    links = {}

    def add(start, end, cost):
        forward = [end, len(graph[end]), 1, cost]
        backward = [start, len(graph[start]), 0, -cost]
        graph[start].append(forward)
        graph[end].append(backward)
        return forward

    for p, row in enumerate(edges):
        add(source, p, Fraction(0))
        for t in sorted(row):
            quality = qualities[p, t] if qualities is not None else Fraction(0)
            links[p, t] = add(p, len(edges) + t, -quality)
    for t in range(truth_count):
        add(len(edges) + t, sink, Fraction(0))
    while True:
        distance, previous = [None] * len(graph), [None] * len(graph)
        distance[source] = Fraction(0)
        # Bellman-Ford permits negative forward and positive residual costs.
        for _ in range(len(graph) - 1):
            changed = False
            for node, row in enumerate(graph):
                if distance[node] is None:
                    continue
                for index, (target, _, capacity, cost) in enumerate(row):
                    candidate = distance[node] + cost
                    if capacity and (distance[target] is None or candidate < distance[target]):
                        distance[target] = candidate
                        previous[target] = node, index
                        changed = True
            if not changed:
                break
        if previous[sink] is None:
            break
        node = sink
        while node != source:
            parent, index = previous[node]
            edge = graph[parent][index]
            edge[2] = 0
            graph[node][edge[1]][2] = 1
            node = parent
    return {p: t for (p, t), edge in links.items() if edge[2] == 0}


def _fraction(value):
    # Decimal spelling avoids surprising boundary failures such as 1/5 < float(.2).
    return Fraction(str(value))


def _exact_iou(first, second):
    a, b = map(_fraction, first)
    c, d = map(_fraction, second)
    return max(Fraction(0), min(b, d) - max(a, c)) / (max(b, d) - min(a, c))


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def _scores(tp, fp, fn):
    return {'tp': tp, 'fp': fp, 'fn': fn,
            'precision': _ratio(tp, tp + fp), 'recall': _ratio(tp, tp + fn),
            'f1': _ratio(2 * tp, 2 * tp + fp + fn)}


def evaluate(dataset, *, iou_threshold, point_tolerance_sec):
    """Return aggregate-only metrics without mutating input or opening other files.

    Thresholds are mandatory experimental settings, never bank criteria.
    Invalid truth aborts; malformed temporal predictions remain counted failures.
    """
    if not _number(iou_threshold) or not 0 < iou_threshold <= 1:
        raise InputError('Experimental IoU threshold must be in (0, 1].')
    if not _number(point_tolerance_sec) or point_tolerance_sec < 0:
        raise InputError('Experimental point tolerance must be finite and nonnegative.')
    _object(dataset, ('schema_version', 'videos'), ('schema_version', 'videos'))
    if type(dataset['schema_version']) is not int or dataset['schema_version'] != 1 or not isinstance(dataset['videos'], list):
        raise InputError('Expected schema version 1 and a video list.')
    totals = dict(videos=0, truth_events=0, predictions=0, matched=0,
                  interval_truth=0, point_truth=0, matched_intervals=0, matched_points=0,
                  unresolved=0, invalid_temporal_predictions=0, missing_evidence_predictions=0,
                  evidence_submitted=0, evidence_invalid=0, evidence_inside=0,
                  evidence_point_unsupported=0, matched_intervals_with_evidence_inside=0,
                  negative_videos=0, negative_predictions=0, negative_videos_with_predictions=0,
                  matched_without_timestamp_reference=0)
    detection = dict(tp=0, fp=0, fn=0, tn=0)
    ious, errors = [], []
    for video in sorted(dataset['videos'], key=_canonical):
        _object(video, ('duration_sec', 'independent_review', 'events_complete', 'ground_truth',
                        'video_positive', 'predictions'),
                ('duration_sec', 'independent_review', 'events_complete', 'ground_truth', 'video_positive', 'predictions'))
        duration = video['duration_sec']
        if not _number(duration) or duration <= 0 or video['independent_review'] is not True or video['events_complete'] is not True:
            raise InputError('Require positive duration, independent review and complete event inventory.')
        if type(video['video_positive']) is not bool or not isinstance(video['ground_truth'], list) or not isinstance(video['predictions'], list):
            raise InputError('Require boolean video decision and event lists.')
        truths = sorted(video['ground_truth'], key=_canonical)
        predictions = sorted(video['predictions'], key=_canonical)
        intervals, seen = [], set()
        for truth in truths:
            _object(truth, ('start_sec', 'end_sec', 'timecode_sec', 'confirmed', 'origin'), ('confirmed', 'origin'))
            if truth['confirmed'] is not True or truth['origin'] != 'human':
                raise InputError('Ground truth must be explicitly human-confirmed.')
            interval = _interval(truth, duration)
            has_interval = 'start_sec' in truth or 'end_sec' in truth
            if (has_interval and interval is None) or (not has_interval and not _time(truth.get('timecode_sec'), duration)):
                raise InputError('Invalid ground-truth interval or point.')
            if 'timecode_sec' in truth and (not _time(truth['timecode_sec'], duration) or
                                           (interval and not interval[0] <= truth['timecode_sec'] <= interval[1])):
                raise InputError('Invalid human timestamp reference.')
            signature = (interval, truth.get('timecode_sec'))
            if signature in seen:
                raise InputError('Duplicate ground-truth event.')
            seen.add(signature)
            intervals.append(interval)
        edges, qualities = [], {}
        for prediction in predictions:
            _object(prediction, ('start_sec', 'end_sec', 'timecode_sec', 'localization_status', 'evidence_frames'))
            frames = prediction.get('evidence_frames', [])
            if not isinstance(frames, list):
                raise InputError('Evidence must be a timestamp-only list.')
            for frame in frames:
                _object(frame, ('timestamp_sec',))
            totals['missing_evidence_predictions'] += not frames
            status = prediction.get('localization_status')
            resolved = status in ('model_proposal', 'localized')
            totals['unresolved'] += not resolved
            interval = _interval(prediction, duration)
            valid = _time(prediction.get('timecode_sec'), duration)
            if 'start_sec' in prediction or 'end_sec' in prediction:
                valid = valid and interval is not None
            if valid and interval:
                valid = interval[0] <= prediction['timecode_sec'] <= interval[1]
            totals['invalid_temporal_predictions'] += not valid
            candidates = []
            if resolved and valid:
                for index, (truth, target) in enumerate(zip(truths, intervals)):
                    if target is not None:
                        if interval is not None:
                            quality = _exact_iou(interval, target)
                            if quality >= _fraction(iou_threshold):
                                candidates.append(index)
                                qualities[len(edges), index] = quality
                    else:
                        error = abs(_fraction(prediction['timecode_sec']) - _fraction(truth['timecode_sec']))
                        if error <= _fraction(point_tolerance_sec):
                            candidates.append(index)
                            qualities[len(edges), index] = (1 - error / _fraction(point_tolerance_sec)
                                                           if point_tolerance_sec else Fraction(1))
            edges.append(candidates)
        pairs = _matching(edges, qualities)
        totals['videos'] += 1
        totals['truth_events'] += len(truths)
        totals['predictions'] += len(predictions)
        totals['matched'] += len(pairs)
        totals['interval_truth'] += sum(x is not None for x in intervals)
        totals['point_truth'] += sum(x is None for x in intervals)
        truth_positive = bool(truths)
        decision = video['video_positive']
        detection['tp' if truth_positive and decision else 'fn' if truth_positive else 'fp' if decision else 'tn'] += 1
        if not truths:
            totals['negative_videos'] += 1
            totals['negative_predictions'] += len(predictions)
            totals['negative_videos_with_predictions'] += bool(predictions)
        for p, t in pairs.items():
            truth, prediction, target = truths[t], predictions[p], intervals[t]
            if target is not None:
                totals['matched_intervals'] += 1
                ious.append(temporal_iou(_interval(prediction, duration), target))
            else:
                totals['matched_points'] += 1
            if 'timecode_sec' in truth:
                errors.append(abs(prediction['timecode_sec'] - truth['timecode_sec']))
            else:
                totals['matched_without_timestamp_reference'] += 1
        for p, prediction in enumerate(predictions):
            frames = prediction.get('evidence_frames', [])
            t = pairs.get(p)
            # Point-only videos and predictions matched to a point have no interval
            # reference for evidence correspondence; never invent a tolerance band.
            if (truths and not any(x is not None for x in intervals)) or (t is not None and intervals[t] is None):
                totals['evidence_point_unsupported'] += len(frames)
                continue
            inside = 0
            for frame in frames:
                value = frame.get('timestamp_sec')
                totals['evidence_submitted'] += 1
                if not _time(value, duration):
                    totals['evidence_invalid'] += 1
                elif t is not None and intervals[t][0] <= value <= intervals[t][1]:
                    inside += 1
            totals['evidence_inside'] += inside
            totals['matched_intervals_with_evidence_inside'] += bool(inside)
    tp = totals['matched']
    result = {
        'schema_version': 1,
        'experimental_settings': {'iou_threshold': iou_threshold, 'point_tolerance_sec': point_tolerance_sec,
                                  'official_bank_criterion': False, 'matching': 'maximum_cardinality_then_maximum_total_quality',
                                  'interval_quality': 'temporal_iou',
                                  'point_quality': '1 - absolute_error / tolerance; exact match = 1 when tolerance = 0',
                                  'tie_break': 'canonical_sanitized_content_then_strict_residual_path_order'},
        'counts': totals,
        'video_detection': {**_scores(detection['tp'], detection['fp'], detection['fn']), 'tn': detection['tn']},
        'event_localization': _scores(tp, totals['predictions'] - tp, totals['truth_events'] - tp),
        'temporal_iou': {'matched_interval_count': len(ious), 'mean_on_matches': _ratio(math.fsum(ious), len(ious)),
                         'point_annotations_unsupported': totals['point_truth']},
        'timestamp_error': {'matched_reference_count': len(errors), 'absolute_error_sum_sec': math.fsum(errors),
                            'mae_sec': _ratio(math.fsum(errors), len(errors)), 'scope': 'matched_events_with_explicit_human_point'},
        'unresolved_fraction': _ratio(totals['unresolved'], totals['predictions']),
        'evidence_timing': {'inside_fraction': _ratio(totals['evidence_inside'], totals['evidence_submitted']),
                            'matched_interval_coverage': _ratio(totals['matched_intervals_with_evidence_inside'], totals['matched_intervals'])},
        'negative_videos': {'count': totals['negative_videos'], 'false_event_predictions': totals['negative_predictions'],
                             'fraction_with_predictions': _ratio(totals['negative_videos_with_predictions'], totals['negative_videos'])},
        'unsupported_metrics': ['event_type_classification', 'visual_evidence_relevance', 'legal_rule_applicability',
                                'temporal_iou_for_point_truth', 'evidence_interval_correspondence_for_point_truth',
                                'timestamp_error_without_explicit_human_point'],
    }
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, help='Sanitized local evaluation JSON only')
    parser.add_argument('--iou-threshold', type=float, required=True, help='Experimental, not an official bank criterion')
    parser.add_argument('--point-tolerance-sec', type=float, required=True, help='Experimental, not an official bank criterion')
    args = parser.parse_args(argv)
    try:
        dataset = json.loads(args.input.read_text(encoding='utf-8'))
        result = evaluate(dataset, iou_threshold=args.iou_threshold, point_tolerance_sec=args.point_tolerance_sec)
    except (ValueError, OSError, OverflowError, RecursionError):
        print('Evaluation rejected: invalid/unreadable sanitized input or settings; no input values shown.', file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
