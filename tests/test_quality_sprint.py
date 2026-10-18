"""Numerical quality sprint boundaries, exercised without private video data."""
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from cashframe.data import read_json, sha256_file, write_json
from cashframe.research import describe_video, fit_video_model, load_frozen, save_frozen


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/quality_sprint.py'
SPEC = importlib.util.spec_from_file_location('quality_sprint', SCRIPT)
sprint = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sprint)


class QualitySprintTests(unittest.TestCase):
    def test_activity_descriptor_ignores_static_mean_but_retains_change(self):
        # Two embedding coordinates per mean/std/motion block.
        windows = np.array([[1., 2., 2., 4., 6., 8.],
                            [3., 6., 4., 8., 10., 12.]])
        expected = [3., 6., 8., 10., 2., 4.]
        np.testing.assert_allclose(describe_video(windows, 'activity_only'), expected)
        shifted = windows.copy()
        shifted[:, :2] += [1000., -500.]
        np.testing.assert_allclose(describe_video(shifted, 'activity_only'), expected)
        changed = windows.copy()
        changed[-1, :2] += [2., 4.]
        np.testing.assert_allclose(describe_video(changed, 'activity_only'), [3., 6., 8., 10., 4., 8.])

    def test_activity_descriptor_single_window_and_invalid_layout(self):
        np.testing.assert_allclose(describe_video([[1., 2., 3., 4., 5., 6.]], 'activity_only'),
                                   [3., 4., 5., 6., 0., 0.])
        for invalid in (np.zeros((2, 5)), np.zeros((2, 0)), np.full((2, 6), np.nan)):
            with self.assertRaises(ValueError):
                describe_video(invalid, 'activity_only')

    def test_activity_selection_has_no_access_to_static_offset_or_test(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_fixture(root)
            # Classes differ only by a static embedding offset, never activity.
            result = sprint.run_selection(root / 'manifest.json', root / 'split.json', root / 'features',
                                          root / 'baseline', root / 'activity', root / 'audit',
                                          candidate_kind='activity')
            self.assertEqual(result['candidate']['validation']['metrics']['confusion_matrix'], [[0, 2], [0, 2]])
            self.assertEqual(result['candidate_count'], 1)
            self.assertFalse(result['test_evaluated'])
            self.assertFalse(result['binary_gate_passed'])
            model, _, metadata = load_frozen(root / 'activity')
            np.testing.assert_array_equal(model['mean'], np.zeros(6))
            self.assertEqual(metadata['selected_spec']['descriptor'], 'activity_only')
            self.assertEqual(metadata['sprint_candidate_number'], 2)
            self.assertNotIn('locked', result['feature_sha256'])
            self.assertTrue((root / 'audit/quality-80-activity-binary.json').is_file())

    def test_snapshot_preserves_bytes_and_refuses_overwrite(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'active'
            source.mkdir()
            (source / 'selected.npz').write_bytes(b'original-model')
            write_json(source / 'config.json', {'threshold': .5})
            target = root / 'snapshot'
            result = sprint.snapshot_baseline(source, target)
            (source / 'selected.npz').write_bytes(b'changed-active-model')
            self.assertEqual((target / 'selected.npz').read_bytes(), b'original-model')
            self.assertEqual(result['files']['selected.npz'], sha256_file(target / 'selected.npz'))
            with self.assertRaises(FileExistsError):
                sprint.snapshot_baseline(source, target)

    def test_threshold_cannot_exchange_recall_for_specificity(self):
        # The unconstrained BA winner at .8 would miss the positive at .2.
        threshold, result = sprint.select_recall_preserving_threshold(
            [.1, .3, .4, .5, .2, .8], [0, 0, 0, 0, 1, 1],
            min_recall=1., partition='validation')
        self.assertAlmostEqual(threshold, .2)
        self.assertEqual(result['recall'], 1.)
        self.assertEqual(result['confusion_matrix'], [[1, 3], [0, 2]])
        with self.assertRaises(ValueError):
            sprint.select_recall_preserving_threshold([.1, .9], [0, 1], 1., partition='test')

    def test_source_audit_counts_each_group_once_and_reports_errors(self):
        result = sprint.score_audit(
            ['a', 'b', 'c', 'd'],
            {'a': {'label': 0}, 'b': {'label': 0}, 'c': {'label': 1}, 'd': {'label': 1}},
            [-.8, .4, .2, -.1], .0)
        self.assertEqual(result['metrics']['confusion_matrix'], [[1, 1], [1, 1]])
        self.assertEqual(result['false_positive_groups'], ['b'])
        self.assertEqual(result['false_negative_groups'], ['d'])
        self.assertEqual(result['score_distribution']['negative']['n'], 2)
        with self.assertRaises(ValueError):
            sprint.score_audit(['a', 'a'], {'a': {'label': 0}}, [.1, .2], .0)

    def test_selection_fits_real_model_without_test_features_or_labels(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_fixture(root)
            result = sprint.run_selection(root / 'manifest.json', root / 'split.json', root / 'features',
                                          root / 'baseline', root / 'binary', root / 'audit')
            self.assertFalse(result['test_evaluated'])
            self.assertEqual(result['candidate']['validation']['metrics']['confusion_matrix'], [[2, 0], [0, 2]])
            self.assertEqual(result['candidate_count'], 1)
            self.assertNotIn('locked', result['feature_sha256'])
            model, threshold, metadata = load_frozen(root / 'binary')
            self.assertTrue(np.isfinite(model['alpha']).all())
            self.assertEqual(metadata['selection_partitions'], ['train', 'validation'])
            with self.assertRaises(FileExistsError):
                sprint.run_selection(root / 'manifest.json', root / 'split.json', root / 'features',
                                     root / 'baseline', root / 'binary', root / 'audit')

    def test_legacy_evaluation_requires_frozen_inputs_and_is_separate(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_fixture(root)
            with self.assertRaises(FileNotFoundError):
                sprint.run_legacy_regression(root / 'manifest.json', root / 'split.json', root / 'features',
                                             root / 'baseline', root / 'binary', root / 'legacy.json')
            sprint.run_selection(root / 'manifest.json', root / 'split.json', root / 'features',
                                 root / 'baseline', root / 'binary', root / 'audit')
            # Adding previously unavailable test data does not change selection.
            rows = read_json(root / 'manifest.json')
            rows[-1].update(label=1, duration_sec=2.)
            write_json(root / 'manifest.json', rows)
            np.savez(root / 'features/locked.npz', times=np.arange(0, 2, .25), values=np.full((8, 2), 3.))
            with self.assertRaisesRegex(ValueError, 'changed'):
                sprint.run_legacy_regression(root / 'manifest.json', root / 'split.json', root / 'features',
                                             root / 'baseline', root / 'binary', root / 'legacy.json')

    @staticmethod
    def make_fixture(root):
        features = root / 'features'
        features.mkdir()
        baseline = root / 'baseline'
        baseline.mkdir()
        split = {'train': [f't{i}' for i in range(8)], 'validation': [f'v{i}' for i in range(4)], 'test': ['locked']}
        rows = []
        windows, labels = [], []
        from cashframe.learning import window_features
        for partition in ('train', 'validation'):
            for index, group_id in enumerate(split[partition]):
                label = index % 2
                values = np.full((8, 2), label * 3.)
                rows.append({'group_id': group_id, 'sha256': group_id, 'label': label, 'duration_sec': 2.})
                np.savez(features / (group_id + '.npz'), times=np.arange(0, 2, .25), values=values)
                if partition == 'train':
                    windows.append(window_features(np.arange(0, 2, .25), values, 2.)[1])
                    labels.append(label)
        rows.append({'group_id': 'locked', 'sha256': 'locked', 'label': 'MUST_NOT_READ'})
        write_json(root / 'manifest.json', rows)
        write_json(root / 'split.json', split)
        write_json(features / 'metadata.json', {'encoder': {}})
        write_json(baseline / 'config.json', {'encoder': {}, 'window_radius_sec': 1., 'window_stride_sec': .5})
        model_spec = {'descriptor': 'mean', 'kernel': 'linear', 'ridge': 10., 'bandwidth': 1.}
        save_frozen(baseline, fit_video_model(windows, labels, model_spec), .0,
                    {'encoder': {}, 'selected_spec': model_spec})


if __name__ == '__main__':
    unittest.main()
