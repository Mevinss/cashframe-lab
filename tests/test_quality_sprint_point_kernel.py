"""Source-weighted weak-point RBF experiment boundaries on synthetic embeddings."""
import importlib.util
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from cashframe.data import read_json, write_json
from cashframe.learning import window_features
from cashframe.research import fit_video_model, save_frozen


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/quality_sprint_point_kernel.py'
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location('quality_sprint_point_kernel', SCRIPT)
kernel = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(kernel)


class WeakPointKernelTests(unittest.TestCase):
    def test_nearest_original_points_exclude_unknown_positive_background_and_balance_groups(self):
        groups = {'n1': {'label': 0, 'points_sec': [], 'duration_sec': 2.},
                  'n2': {'label': 0, 'points_sec': [], 'duration_sec': 2.},
                  'p1': {'label': 1, 'points_sec': [.9], 'duration_sec': 2.},
                  'p2': {'label': 1, 'points_sec': [1.8], 'duration_sec': 2.}}
        cache = {'n1': (np.array([0.]), np.array([[0.]])),
                 'n2': (np.arange(3.), np.array([[1.], [2.], [3.]])),
                 'p1': (np.arange(3.), np.array([[1000.], [4.], [1000.]])),
                 'p2': (np.arange(3.), np.array([[1000.], [1000.], [5.]]))}
        x, labels, weights, details = kernel.weak_training_rows(list(groups), groups, cache)
        np.testing.assert_array_equal(x.ravel(), [0., 1., 2., 3., 4., 5.])
        np.testing.assert_array_equal(labels, [0, 0, 0, 0, 1, 1])
        np.testing.assert_allclose(weights, [1., 1/3, 1/3, 1/3, 1., 1.])
        self.assertEqual(details['ignored_positive_windows'], 4)
        self.assertFalse(details['human_confirmed'])
        self.assertEqual(details['supervision_kind'], 'source_weak_supervision')

    def test_nonlinear_kernel_can_separate_symmetric_classes_and_never_refits_on_scoring(self):
        x = np.array([[-2.], [-1.], [1.], [2.]])
        model = kernel.fit_window_kernel(x, [1, 0, 0, 1], np.ones(4))
        before = model['mean'].copy()
        scores = kernel.window_scores(model, x)
        self.assertGreater(min(scores[0], scores[3]), max(scores[1], scores[2]))
        self.assertTrue(np.isfinite(kernel.window_scores(model, [[10000.]])).all())
        np.testing.assert_array_equal(model['mean'], before)
        np.testing.assert_allclose(model['mean'], [0.])
        np.testing.assert_allclose(model['std'], [np.sqrt(2.5)])

    def test_max_aggregation_is_fixed_and_frozen_weights_reject_corruption(self):
        model = {'mean': np.zeros(1), 'std': np.ones(1), 'reference': np.array([[-1.], [1.]]),
                 'alpha': np.array([-1., 1.]), 'bandwidth': np.asarray(1.), 'ridge': np.asarray(1.)}
        expected = 1 - np.exp(-2.)
        self.assertAlmostEqual(float(kernel.bag_scores(model, [np.array([[-1.], [0.], [1.]])])[0]), expected)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            kernel.save_candidate(root, model, {'threshold': .1, 'encoder': {}})
            loaded, metadata = kernel.load_candidate(root, expected_encoder={})
            self.assertEqual(metadata['threshold'], .1)
            np.testing.assert_allclose(kernel.window_scores(loaded, [[1.]]), [expected])
            with (root / 'point-kernel.npz').open('ab') as stream:
                stream.write(b'corrupt')
            with self.assertRaisesRegex(ValueError, 'hash'):
                kernel.load_candidate(root)

    def test_selection_fits_only_train_point_rows_with_no_test_cache_or_usable_test_label(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_fixture(root)
            result = kernel.run_selection(root / 'manifest.json', root / 'split.json', root / 'features',
                                          root / 'baseline', root / 'candidate', root / 'audit')
            self.assertEqual(result['sprint_candidate_number'], 3)
            self.assertFalse(result['test_evaluated'])
            self.assertEqual(result['supervision']['training_positive_windows'], 4)
            self.assertGreater(result['supervision']['ignored_positive_windows'], 0)
            self.assertEqual(result['candidate']['validation']['metrics']['confusion_matrix'], [[2, 0], [0, 2]])
            self.assertEqual(result['candidate_count'], 1)
            self.assertNotIn('locked', result['feature_sha256'])
            _, metadata = kernel.load_candidate(root / 'candidate')
            self.assertFalse(metadata['human_confirmed'])
            self.assertEqual(metadata['selection_partitions'], ['train', 'validation'])
            with self.assertRaises(FileExistsError):
                kernel.run_selection(root / 'manifest.json', root / 'split.json', root / 'features',
                                     root / 'baseline', root / 'candidate', root / 'audit')

    @staticmethod
    def make_fixture(root):
        features, baseline = root / 'features', root / 'baseline'
        features.mkdir()
        baseline.mkdir()
        split = {'train': [f't{i}' for i in range(8)], 'validation': [f'v{i}' for i in range(4)], 'test': ['locked']}
        rows, train_windows, train_labels = [], [], []
        for partition in ('train', 'validation'):
            for index, group_id in enumerate(split[partition]):
                label = index % 2
                values = np.full((8, 2), label * 3.)
                rows.append({'group_id': group_id, 'sha256': group_id, 'label': label,
                             'duration_sec': 2., 'points_sec': [1.] if label else []})
                np.savez(features / (group_id + '.npz'), times=np.arange(0, 2, .25), values=values)
                if partition == 'train':
                    train_windows.append(window_features(np.arange(0, 2, .25), values, 2.)[1])
                    train_labels.append(label)
        rows.append({'group_id': 'locked', 'sha256': 'locked', 'label': 'MUST_NOT_READ'})
        write_json(root / 'manifest.json', rows)
        write_json(root / 'split.json', split)
        write_json(features / 'metadata.json', {'encoder': {}})
        write_json(baseline / 'config.json', {'encoder': {}, 'window_radius_sec': 1., 'window_stride_sec': .5})
        model_spec = {'descriptor': 'mean', 'kernel': 'linear', 'ridge': 10., 'bandwidth': 1.}
        save_frozen(baseline, fit_video_model(train_windows, train_labels, model_spec), .0,
                    {'encoder': {}, 'selected_spec': model_spec})


if __name__ == '__main__':
    unittest.main()
