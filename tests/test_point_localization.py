import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np


class PointLocalizationTest(unittest.TestCase):
    def module(self):
        from cashframe import point_localization
        return point_localization

    def test_weak_targets_leave_other_positive_moments_unknown(self):
        p = self.module()
        self.assertEqual(p.point_targets(np.arange(7.), 1, [3.]).tolist(), [-1, -1, -1, 1, -1, -1, -1])
        self.assertEqual(p.point_targets(np.arange(3.), 0, []).tolist(), [0, 0, 0])

    def test_residual_features_remove_constant_scene_offset(self):
        p = self.module()
        times = np.arange(0, 8, .25)
        values = np.c_[np.sin(times), np.cos(times)]
        a, x = p.point_features(times, values, 8., 'combined', radius=1.)
        b, y = p.point_features(times, values + [200., -700.], 8., 'combined', radius=1.)
        np.testing.assert_allclose(x, y, atol=1e-10)
        self.assertEqual(a.tolist(), b.tolist())

    def test_guard_rejects_flat_broad_and_below_confidence_signals(self):
        p = self.module()
        t = np.arange(0, 20, .5)
        guard = {'min_score': .5, 'min_range': .05, 'min_prominence': .05, 'tail_fraction': .15,
                 'max_coverage_fraction': .5, 'max_events': 5}
        for scores in [np.full(len(t), .9), np.linspace(.1, .2, len(t))]:
            event = p.events_from_point_scores(t, scores, 20., True, guard)[0]
            self.assertEqual(event['localization_status'], 'unresolved')
            self.assertEqual(event['timecode_sec'], 0.)
        self.assertEqual(p.events_from_point_scores(t, np.ones(len(t)), 20., False, guard), [])

    def test_localized_peak_is_action_proposal_without_invented_type(self):
        p = self.module()
        t = np.arange(0, 20, .5)
        s = .1 + .8*np.exp(-((t-12.)/.6)**2)
        guard = {'min_score': .5, 'min_range': .05, 'min_prominence': .05, 'tail_fraction': .15,
                 'max_coverage_fraction': .5, 'max_events': 5}
        events = p.events_from_point_scores(t, s, 20., True, guard)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['timecode_sec'], 12.)
        self.assertEqual(events[0]['localization_status'], 'model_proposal')
        self.assertFalse(events[0]['confirmed'])
        self.assertIsNone(events[0]['rule_id'])

    def test_artifact_checks_encoder_hash_and_finite_parameters(self):
        p = self.module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            model = {'mean': np.zeros(2), 'std': np.ones(2), 'coef': np.ones(2), 'bias': np.array(0.)}
            metadata = {'schema_version': 1, 'encoder': {'model': 'unit'}, 'feature_mode': 'residual',
                        'feature_dim': 2, 'radius_sec': 1., 'guard': {'min_score': .5, 'min_range': .05,
                        'min_prominence': .05, 'tail_fraction': .15, 'max_coverage_fraction': .5, 'max_events': 5}}
            p.save_point_localizer(root, model, metadata)
            loaded, config = p.load_point_localizer(root, {'model': 'unit'})
            self.assertEqual(loaded['coef'].tolist(), [1., 1.])
            with self.assertRaisesRegex(ValueError, 'encoder'):
                p.load_point_localizer(root, {'model': 'other'})
            with (root/'point-localizer.npz').open('ab') as stream:
                stream.write(b'tamper')
            with self.assertRaisesRegex(ValueError, 'hash'):
                p.load_point_localizer(root, {'model': 'unit'})
            model['coef'][0] = np.nan
            with self.assertRaisesRegex(ValueError, 'finite'):
                p.save_point_localizer(root, model, metadata)

    def test_invalid_sequence_rejected(self):
        p = self.module()
        for t, x in [([1., 0.], [[1.], [2.]]), ([0., 1.], [[np.nan], [2.]])]:
            with self.assertRaises(ValueError):
                p.point_features(t, x, 2., 'residual')


if __name__ == '__main__':
    unittest.main()

class PointTrainingTest(unittest.TestCase):
    def test_unknown_positive_windows_cannot_influence_fitted_statistics(self):
        from cashframe import point_localization as p
        self.assertTrue(hasattr(p, 'fit_point_model'), 'point fitting must exclude unknown windows')
        rows = [np.array([[2.], [100000.]]), np.array([[-2.], [-4.]])]
        targets = [np.array([1, -1]), np.array([0, 0])]
        model, details = p.fit_point_model(rows, targets, .1, epochs=20)
        self.assertEqual(details['training_windows'], 3)
        self.assertEqual(details['training_positive_windows'], 1)
        self.assertAlmostEqual(float(model['mean'][0]), -.5)

class PointExperimentMetricsTest(unittest.TestCase):
    def test_unresolved_points_are_misses_and_negative_proposals_are_counted(self):
        import importlib.util
        script = Path(__file__).resolve().parents[1]/'scripts'/'train_point_localizer.py'
        self.assertTrue(script.exists(), 'experiment runner is required')
        spec = importlib.util.spec_from_file_location('point_runner', script)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        rows = [{'truth': 1, 'points_sec': [4., 12.], 'duration_sec': 20.,
                 'events': [{'timecode_sec': 5., 'start_sec': 4., 'end_sec': 6., 'localization_status': 'model_proposal'}]},
                {'truth': 1, 'points_sec': [4.], 'duration_sec': 10.,
                 'events': [{'timecode_sec': 0., 'start_sec': 0., 'end_sec': 10., 'localization_status': 'unresolved'}]},
                {'truth': 0, 'points_sec': [], 'duration_sec': 10.,
                 'events': [{'timecode_sec': 3., 'start_sec': 2., 'end_sec': 4., 'localization_status': 'model_proposal'}]}]
        result = runner.summarize(rows)
        self.assertEqual(result['point_count'], 3)
        self.assertEqual(result['recall_within_2_sec'], 1/3)
        self.assertEqual(result['recall_within_5_sec'], 1/3)
        self.assertEqual(result['unresolved_positive_videos'], 1)
        self.assertEqual(result['negative_video_event_count'], 1)
        self.assertEqual(result['point_mae_resolved_sec'], 4.)

class PointFragmentationTest(unittest.TestCase):
    def test_six_separated_compact_peaks_are_not_discarded_in_long_recording(self):
        from cashframe import point_localization as p
        t = np.arange(0, 70, .5)
        s = np.full(len(t), .1)
        for center in (5., 15., 25., 35., 45., 55.):
            s += .8*np.exp(-((t-center)/.3)**2)
        events = p.events_from_point_scores(t, s, 70., True)
        self.assertEqual(len(events), 6)
        self.assertTrue(all(e['localization_status'] == 'model_proposal' for e in events))

    def test_broad_high_plateau_remains_unresolved(self):
        from cashframe import point_localization as p
        t = np.arange(0, 30, .5)
        s = np.where((t >= 4.) & (t <= 26.), .9, .1)
        events = p.events_from_point_scores(t, s, 30., True)
        self.assertEqual(events[0]['localization_status'], 'unresolved')

class FrozenPointGuardTest(unittest.TestCase):
    def test_explicit_frozen_guard_survives_default_guard_changes(self):
        import importlib.util
        import inspect
        script = Path(__file__).resolve().parents[1]/'scripts'/'train_point_localizer.py'
        spec = importlib.util.spec_from_file_location('point_runner_guard', script)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        self.assertIn('guard', inspect.signature(runner.evaluate).parameters)
        times = np.arange(0, 70, .5)
        scores = np.full(len(times), .1)
        for center in (5., 15., 25., 35., 45., 55.):
            scores += .8*np.exp(-((times-center)/.3)**2)
        x = np.log(scores/(1-scores))[:, None]
        model = {'mean': np.zeros(1), 'std': np.ones(1), 'coef': np.ones(1), 'bias': 0.}
        guard = dict(runner.DEFAULT_GUARD, max_events=5)
        rows = runner.evaluate(['unit'], {'unit': (times, x)}, model,
                               {'unit': {'group_id': 'unit', 'label': 1, 'points_sec': [45.], 'duration_sec': 70.}}, guard=guard)
        self.assertEqual(rows[0]['events'][0]['localization_status'], 'unresolved')

if __name__ == '__main__':
    unittest.main()
