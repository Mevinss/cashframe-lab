"""Only synthetic timestamps; run this file's tests, never production tests."""

import contextlib
import copy
import importlib.util
import io
import itertools
import json
from pathlib import Path
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location('isolated_localization_evaluator', Path(__file__).with_name('evaluate.py'))
ev = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ev)


def truth(start=2, end=4, point=3):
    item = {'confirmed': True, 'origin': 'human', 'start_sec': start, 'end_sec': end}
    if point is not None:
        item['timecode_sec'] = point
    return item


def prediction(start=2, end=4, point=3, status='model_proposal', evidence=(3,)):
    return {'start_sec': start, 'end_sec': end, 'timecode_sec': point,
            'localization_status': status, 'evidence_frames': [{'timestamp_sec': p} for p in evidence]}


def dataset(truths=None, predictions=None, positive=True):
    return {'schema_version': 1, 'videos': [{'duration_sec': 20, 'independent_review': True,
            'events_complete': True, 'ground_truth': [truth()] if truths is None else truths,
            'predictions': [prediction()] if predictions is None else predictions, 'video_positive': positive}]}


def evaluate(data, iou=.5, tolerance=.5):
    return ev.evaluate(data, iou_threshold=iou, point_tolerance_sec=tolerance)


class LocalizationTests(unittest.TestCase):
    def test_perfect_and_no_mutation(self):
        data = dataset()
        before = copy.deepcopy(data)
        result = evaluate(data)
        self.assertEqual(data, before)
        self.assertEqual(result['event_localization'], dict(tp=1, fp=0, fn=0, precision=1, recall=1, f1=1))
        self.assertEqual(result['temporal_iou']['mean_on_matches'], 1)
        self.assertEqual(result['timestamp_error']['mae_sec'], 0)
        self.assertEqual(result['evidence_timing']['inside_fraction'], 1)

    def test_small_timing_error(self):
        result = evaluate(dataset(predictions=[prediction(2.25, 4.25, 3.25)]))
        self.assertAlmostEqual(result['temporal_iou']['mean_on_matches'], 1.75 / 2.25)
        self.assertEqual(result['timestamp_error']['mae_sec'], .25)

    def test_missed_event(self):
        result = evaluate(dataset(predictions=[], positive=False))
        self.assertEqual(result['event_localization']['fn'], 1)
        self.assertEqual(result['event_localization']['recall'], 0)
        self.assertIsNone(result['event_localization']['precision'])
        self.assertEqual(result['video_detection']['fn'], 1)

    def test_false_event(self):
        result = evaluate(dataset(predictions=[prediction(8, 10, 9)]))
        self.assertEqual(result['event_localization']['fp'], 1)
        self.assertEqual(result['event_localization']['fn'], 1)
        self.assertIsNone(result['timestamp_error']['mae_sec'])
        self.assertEqual(result['evidence_timing']['inside_fraction'], 0)

    def test_duplicate_predictions_cannot_reuse_truth_or_evidence_credit(self):
        result = evaluate(dataset(predictions=[prediction(), prediction()]))
        self.assertEqual(result['event_localization']['tp'], 1)
        self.assertEqual(result['event_localization']['fp'], 1)
        self.assertEqual(result['event_localization']['precision'], .5)
        self.assertEqual(result['evidence_timing']['inside_fraction'], .5)

    def test_multiple_events_augmenting_path_beats_greedy(self):
        result = evaluate(dataset(truths=[truth(0, 4, 2), truth(4, 8, 6)],
                                  predictions=[prediction(0, 8, 4), prediction(0, 4, 2)]))
        self.assertEqual(result['event_localization']['tp'], 2)
        self.assertEqual(result['timestamp_error']['mae_sec'], 1)

    def test_matching_exhaustively_has_maximum_cardinality_for_small_graphs(self):
        for bits in itertools.product((False, True), repeat=9):
            edges = [[t for t in range(3) if bits[p * 3 + t]] for p in range(3)]
            best = 0
            for assignment in itertools.product((-1, 0, 1, 2), repeat=3):
                chosen = [t for t in assignment if t != -1]
                if len(chosen) == len(set(chosen)) and all(t == -1 or t in edges[p] for p, t in enumerate(assignment)):
                    best = max(best, len(chosen))
            pairs = ev._matching(edges)
            self.assertEqual(len(pairs), best)
            self.assertEqual(len(set(pairs.values())), len(pairs))
            self.assertEqual(pairs, ev._matching(edges))

    def test_negative_videos_reported_separately(self):
        data = dataset([], [prediction()], True)
        data['videos'] += dataset([], [], False)['videos']
        result = evaluate(data)
        self.assertEqual(result['negative_videos'], dict(count=2, false_event_predictions=1, fraction_with_predictions=.5))
        self.assertEqual(result['video_detection']['fp'], 1)
        self.assertEqual(result['video_detection']['tn'], 1)
        self.assertEqual(result['evidence_timing']['inside_fraction'], 0)

    def test_unresolved_zero_is_not_localized(self):
        result = evaluate(dataset([truth(0, 4, 0)], [prediction(0, 20, 0, 'unresolved')]))
        self.assertEqual(result['unresolved_fraction'], 1)
        self.assertEqual(result['event_localization']['tp'], 0)
        self.assertEqual(result['event_localization']['fp'], 1)
        self.assertEqual(result['event_localization']['fn'], 1)
        self.assertEqual(result['video_detection']['tp'], 1)

    def test_missing_and_uncertain_statuses_are_unresolved(self):
        for status in (None, 'uncertain', 'unknown', ''):
            result = evaluate(dataset(predictions=[prediction(status=status)]))
            self.assertEqual(result['event_localization']['tp'], 0)
            self.assertEqual(result['unresolved_fraction'], 1)

    def test_point_truth_has_no_iou_or_interval_evidence_metric(self):
        point = {'confirmed': True, 'origin': 'human', 'timecode_sec': 3}
        pred = {'timecode_sec': 3.25, 'localization_status': 'model_proposal', 'evidence_frames': [{'timestamp_sec': 3}]}
        result = evaluate(dataset([point], [pred]))
        self.assertEqual(result['event_localization']['tp'], 1)
        self.assertEqual(result['timestamp_error']['mae_sec'], .25)
        self.assertIsNone(result['temporal_iou']['mean_on_matches'])
        self.assertEqual(result['temporal_iou']['point_annotations_unsupported'], 1)
        self.assertIsNone(result['evidence_timing']['inside_fraction'])
        self.assertEqual(result['counts']['evidence_point_unsupported'], 1)
        self.assertIn('temporal_iou_for_point_truth', result['unsupported_metrics'])
        self.assertEqual(evaluate(dataset([point], [pred]), tolerance=.1)['event_localization']['tp'], 0)

    def test_mixed_point_and_interval_inventory(self):
        point = {'confirmed': True, 'origin': 'human', 'timecode_sec': 10}
        result = evaluate(dataset([truth(), point], [prediction(), prediction(9, 11, 10)]))
        self.assertEqual(result['event_localization']['tp'], 2)
        self.assertEqual(result['counts']['matched_intervals'], 1)
        self.assertEqual(result['counts']['matched_points'], 1)
        self.assertEqual(result['temporal_iou']['matched_interval_count'], 1)

    def test_interval_without_point_does_not_invent_reference(self):
        result = evaluate(dataset([truth(point=None)]))
        self.assertEqual(result['event_localization']['tp'], 1)
        self.assertIsNone(result['timestamp_error']['mae_sec'])
        self.assertEqual(result['counts']['matched_without_timestamp_reference'], 1)

    def test_missing_evidence_and_boundaries(self):
        missing = evaluate(dataset(predictions=[prediction(evidence=())]))
        self.assertEqual(missing['counts']['missing_evidence_predictions'], 1)
        self.assertIsNone(missing['evidence_timing']['inside_fraction'])
        self.assertEqual(missing['evidence_timing']['matched_interval_coverage'], 0)
        result = evaluate(dataset(predictions=[prediction(evidence=(2, 4, 1, None, float('nan')))]))
        self.assertEqual(result['counts']['evidence_invalid'], 2)
        self.assertEqual(result['evidence_timing']['inside_fraction'], 2 / 5)
        self.assertEqual(result['evidence_timing']['matched_interval_coverage'], 1)

    def test_invalid_predictions_stay_in_failure_denominator(self):
        for update in ({'start_sec': 4, 'end_sec': 2}, {'start_sec': 2, 'end_sec': 2},
                       {'start_sec': -1}, {'end_sec': 21}, {'timecode_sec': None},
                       {'timecode_sec': float('nan')}, {'timecode_sec': float('inf')},
                       {'timecode_sec': True}, {'timecode_sec': '3'}, {'timecode_sec': 9},
                       {'timecode_sec': 10 ** 400}):
            pred = prediction()
            pred.update(update)
            result = evaluate(dataset(predictions=[pred]))
            self.assertEqual(result['event_localization']['tp'], 0)
            self.assertEqual(result['counts']['invalid_temporal_predictions'], 1)
            self.assertEqual(result['event_localization']['fp'], 1)
        pred = prediction()
        del pred['timecode_sec']
        self.assertEqual(evaluate(dataset(predictions=[pred]))['event_localization']['tp'], 0)

    def test_invalid_truth_rejected_instead_of_silently_excluded(self):
        for item in (truth(4, 2), truth(2, 2), truth(-1, 4), truth(2, 21), truth(point=9),
                     {'confirmed': True, 'origin': 'human'},
                     {**truth(), 'confirmed': False}, {**truth(), 'origin': 'automatic'}):
            with self.assertRaises(ev.InputError):
                evaluate(dataset([item]))
        with self.assertRaises(ev.InputError):
            evaluate(dataset([truth(), truth()]))
        for field in ('events_complete', 'independent_review'):
            data = dataset()
            data['videos'][0][field] = False
            with self.assertRaises(ev.InputError):
                evaluate(data)

    def test_empty_dataset_and_zero_denominators(self):
        result = evaluate({'schema_version': 1, 'videos': []})
        self.assertEqual(result['counts']['videos'], 0)
        self.assertIsNone(result['event_localization']['f1'])
        self.assertIsNone(result['timestamp_error']['mae_sec'])
        self.assertIsNone(result['unresolved_fraction'])
        json.dumps(result, allow_nan=False)

    def test_explicit_settings_and_iou_validation(self):
        for threshold in (0, -1, 1.1, True, float('nan')):
            with self.assertRaises(ev.InputError):
                evaluate(dataset(), iou=threshold)
        for tolerance in (-1, True, float('inf')):
            with self.assertRaises(ev.InputError):
                evaluate(dataset(), tolerance=tolerance)
        with self.assertRaises(TypeError):
            ev.evaluate(dataset())
        for pair in ((2, 2), (4, 2), (None, 4)):
            with self.assertRaises(ev.InputError):
                ev.temporal_iou(pair, (2, 4))
        self.assertEqual(ev.temporal_iou((0, 1), (1, 2)), 0)
        self.assertEqual(ev.temporal_iou((0, 1), (4, 5)), 0)

    def test_cli_outputs_only_aggregates_and_redacts_failures(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-localization-') as folder:
            path = Path(folder) / 'synthetic.json'
            path.write_text(json.dumps(dataset()), encoding='utf-8')
            args = [str(path), '--iou-threshold', '.5', '--point-tolerance-sec', '.5']
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(ev.main(args), 0)
            self.assertEqual(json.loads(out.getvalue())['event_localization']['tp'], 1)
            self.assertNotIn('ground_truth', out.getvalue())
            data = dataset()
            data['videos'][0]['source_filename'] = 'SYNTHETIC_DO_NOT_ECHO'
            path.write_text(json.dumps(data), encoding='utf-8')
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(ev.main(args), 2)
            self.assertEqual(out.getvalue(), '')
            self.assertNotIn('SYNTHETIC_DO_NOT_ECHO', err.getvalue())

    def test_private_fields_and_frame_payloads_rejected(self):
        for target, field in (('truth', 'observed_fact'), ('prediction', 'description'), ('frame', 'image_base64')):
            data = dataset()
            video = data['videos'][0]
            item = video['ground_truth'][0] if target == 'truth' else video['predictions'][0]
            if target == 'frame':
                item = item['evidence_frames'][0]
            item[field] = 'SYNTHETIC_FORBIDDEN_FIELD'
            with self.assertRaises(ev.InputError):
                evaluate(data)


class OptimalAssignmentTests(unittest.TestCase):
    def test_global_quality_beats_local_greedy(self):
        from fractions import Fraction as F
        edges = [[0, 1], [0, 1]]
        quality = {(0, 0): F(9, 10), (0, 1): F(8, 10),
                   (1, 0): F(85, 100), (1, 1): F(1, 10)}
        self.assertEqual(ev._matching(edges, quality), {0: 1, 1: 0})

    def test_cardinality_precedes_even_zero_quality(self):
        from fractions import Fraction as F
        self.assertEqual(len(ev._matching([[0, 1], [0]],
                         {(0, 0): F(1), (0, 1): F(0), (1, 0): F(0)})), 2)

    def test_exhaustive_weighted_optimum_against_brute_force(self):
        from fractions import Fraction as F
        for weights in itertools.product((None, F(0), F(1, 2), F(1)), repeat=4):
            edges = [[t for t in range(2) if weights[p * 2 + t] is not None] for p in range(2)]
            quality = {(p, t): weights[p * 2 + t] for p in range(2) for t in edges[p]}
            best = (0, F(0))
            for assignment in itertools.product((-1, 0, 1), repeat=2):
                chosen = [t for t in assignment if t != -1]
                if len(chosen) == len(set(chosen)) and all(t == -1 or t in edges[p] for p, t in enumerate(assignment)):
                    best = max(best, (len(chosen), sum((quality[p, t] for p, t in enumerate(assignment) if t != -1), F(0))))
            pairs = ev._matching(edges, quality)
            self.assertEqual((len(pairs), sum((quality[p, t] for p, t in pairs.items()), F(0))), best)

    def test_interval_competition_and_permutation_invariance(self):
        truths = [truth(0, 4, 2), truth(3, 7, 5)]
        preds = [prediction(0, 4, 2, evidence=(2,)), prediction(2, 6, 4, evidence=(5,)),
                 prediction(10, 12, 11, evidence=())]
        baseline = evaluate(dataset(truths, preds), iou=.1)
        self.assertAlmostEqual(baseline['temporal_iou']['mean_on_matches'], .8)
        for ts in itertools.permutations(truths):
            for ps in itertools.permutations(preds):
                self.assertEqual(evaluate(dataset(list(ts), list(ps)), iou=.1), baseline)

    def test_mixed_quality_uses_normalized_point_error(self):
        interval = truth(0, 4, None)
        point = {'confirmed': True, 'origin': 'human', 'timecode_sec': 2}
        # Both count-one solutions are valid; exact point quality 1 beats IoU .5.
        result = evaluate(dataset([interval, point], [prediction(0, 8, 2)]), tolerance=1)
        self.assertEqual(result['counts']['matched_points'], 1)
        self.assertEqual(result['counts']['matched_intervals'], 0)

    def test_zero_tolerance_and_equal_optima(self):
        point = {'confirmed': True, 'origin': 'human', 'timecode_sec': 3}
        preds = [prediction(evidence=(3,)), prediction(evidence=())]
        reference = evaluate(dataset([point], preds), tolerance=0)
        self.assertEqual(reference['timestamp_error']['mae_sec'], 0)
        self.assertEqual(evaluate(dataset([point], list(reversed(preds))), tolerance=0), reference)

    def test_video_order_invariance(self):
        data = dataset()
        data['videos'] += dataset([], [], False)['videos']
        result = evaluate(data)
        data['videos'].reverse()
        self.assertEqual(evaluate(data), result)


class BoundaryAndMixedTests(unittest.TestCase):
    def test_decimal_iou_and_point_tolerance_boundaries_are_inclusive(self):
        self.assertEqual(evaluate(dataset([truth(0, 1, None)], [prediction(0, 5, 1)]), iou=.2)['event_localization']['tp'], 1)
        point={'confirmed': True, 'origin': 'human', 'timecode_sec': .1}
        pred={'timecode_sec': .3, 'localization_status': 'localized'}
        self.assertEqual(evaluate(dataset([point], [pred]), tolerance=.2)['event_localization']['tp'], 1)

    def test_competing_mixed_matches_are_permutation_invariant(self):
        ts=[truth(0,4,2), {'confirmed':True,'origin':'human','timecode_sec':3}]
        ps=[prediction(0,4,3,evidence=(2,)), prediction(0,8,3,evidence=(5,)), prediction(0,4,2,evidence=())]
        baseline=evaluate(dataset(ts,ps),tolerance=1)
        for truths in itertools.permutations(ts):
            for predictions in itertools.permutations(ps):
                self.assertEqual(evaluate(dataset(list(truths),list(predictions)),tolerance=1),baseline)


if __name__ == '__main__':
    unittest.main()
