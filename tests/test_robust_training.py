"""Leakage and preservation contracts for the optional cached-feature experiment."""
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from cashframe.data import sha256_file, write_json
from cashframe.research import fit_video_model, save_frozen, score_videos


class RobustTrainingTests(unittest.TestCase):
    def experiment(self):
        path = Path(__file__).resolve().parents[1] / 'scripts/experiment_v4.py'
        self.assertTrue(path.exists(), 'The bounded robust-training runner is missing')
        spec = importlib.util.spec_from_file_location('experiment_v4', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_mixup_uses_only_same_class_fitting_sources_and_preserves_mass(self):
        experiment = self.experiment()
        values = np.array([[-2., 0.], [0., 2.], [3., 5.], [5., 7.], [7., 9.]])
        labels = np.array([0, 0, 1, 1, 1])
        original = values.copy()
        ids = list('abcde')
        augmented, targets, weights, parents = experiment.augment_descriptors(values, labels, ids, 'mixup', seed=42)
        np.testing.assert_array_equal(values, original)
        np.testing.assert_array_equal(augmented[:5], original)
        self.assertEqual(augmented.shape, (10, 2))
        self.assertAlmostEqual(weights[targets == 0].sum(), 2.5)
        self.assertAlmostEqual(weights[targets == 1].sum(), 2.5)
        for row, target, source_parents in zip(augmented[5:], targets[5:], parents[5:]):
            self.assertEqual(len(source_parents), 2)
            self.assertNotEqual(*source_parents)
            parent_indices = [ids.index(source) for source in source_parents]
            self.assertTrue(all(labels[index] == target for index in parent_indices))
            self.assertTrue(np.all(row >= values[parent_indices].min(axis=0)))
            self.assertTrue(np.all(row <= values[parent_indices].max(axis=0)))

    def test_noise_is_reproducible_and_normalization_uses_original_train_only(self):
        experiment = self.experiment()
        windows = [np.array([[value, value + 1.]]) for value in (0., 2., 10., 12.)]
        spec = dict(descriptor='mean', kernel='linear', ridge=10., bandwidth=1., augmentation='noise')
        first = experiment.fit_augmented(windows, [0, 0, 1, 1], list('abcd'), spec, seed=42)
        second = experiment.fit_augmented(windows, [0, 0, 1, 1], list('abcd'), spec, seed=42)
        np.testing.assert_array_equal(first['reference'], second['reference'])
        np.testing.assert_array_equal(first['alpha'], second['alpha'])
        np.testing.assert_allclose(first['mean'], [6., 7.])
        np.testing.assert_allclose(first['std'], [np.sqrt(26.), np.sqrt(26.)])
        self.assertFalse(np.array_equal(first['reference'][:4], first['reference'][4:]))
        before = first['mean'].copy()
        self.assertTrue(np.isfinite(score_videos(first, [np.array([[1e6, 1e6]])])).all())
        np.testing.assert_array_equal(before, first['mean'])

    def fixture(self, root):
        features = root / 'features'
        features.mkdir()
        base = root / 'active'
        base.mkdir()
        split = {'train': [f't{i}' for i in range(8)], 'validation': [f'v{i}' for i in range(4)], 'test': ['locked']}
        rows = []
        training = []
        for partition in ('train', 'validation'):
            for i, gid in enumerate(split[partition]):
                label = i % 2
                rows.append(dict(group_id=gid, sha256=gid, label=label, duration_sec=2.))
                np.savez(features / (gid + '.npz'), times=np.arange(0, 2, .25), values=np.full((8, 2), 3. if label else -3.))
                if partition == 'train':
                    training.append(np.full((8, 6), 3. if label else -3.))
        rows.append(dict(group_id='locked', sha256='locked', label='MUST_NOT_READ'))
        write_json(root / 'manifest.json', rows)
        write_json(root / 'split.json', split)
        write_json(features / 'metadata.json', {'encoder': {'synthetic': True}})
        write_json(base / 'config.json', {'encoder': {'synthetic': True}, 'video_classifier': 'selected.npz'})
        base_spec = dict(descriptor='mean', kernel='linear', ridge=10., bandwidth=1.)
        save_frozen(base, fit_video_model(training, [i % 2 for i in range(8)], base_spec), 0., {'encoder': {'synthetic': True}, 'selected_spec': base_spec})
        return features, base, rows, split

    def test_selection_has_no_test_access_keeps_active_bytes_and_uses_group_folds(self):
        experiment = self.experiment()
        with TemporaryDirectory() as folder:
            root = Path(folder)
            features, base, _, split = self.fixture(root)
            original = {path.name: sha256_file(path) for path in base.iterdir()}
            result = experiment.run(root / 'manifest.json', root / 'split.json', features, root / 'run', base)
            self.assertFalse(result['test_evaluated'])
            self.assertEqual(result['validation']['accuracy'], 1.)
            self.assertNotIn('locked', result['feature_hashes'])
            self.assertEqual({path.name: sha256_file(path) for path in base.iterdir()}, original)
            held = []
            for fit_ids, held_ids in result['folds']:
                self.assertFalse(set(fit_ids) & set(held_ids))
                self.assertEqual(set(fit_ids) | set(held_ids), set(split['train']))
                held.extend(held_ids)
            self.assertEqual(sorted(held), sorted(split['train']))
            with self.assertRaises(FileExistsError):
                experiment.run(root / 'manifest.json', root / 'split.json', features, root / 'run', base)

    def test_validation_changes_cannot_affect_training_selection_or_fold_weights(self):
        experiment = self.experiment()
        with TemporaryDirectory() as folder:
            root = Path(folder)
            features, base, rows, split = self.fixture(root)
            first = experiment.run(root / 'manifest.json', root / 'split.json', features, root / 'first', base)
            for row in rows:
                if row['group_id'] in split['validation']:
                    row['label'] = 1 - row['label']
                    np.savez(features / (row['group_id'] + '.npz'), times=np.arange(0, 2, .25), values=np.full((8, 2), 1e5))
            write_json(root / 'manifest.json', rows)
            second = experiment.run(root / 'manifest.json', root / 'split.json', features, root / 'second', base)
            self.assertEqual(first['selected_spec'], second['selected_spec'])
            self.assertEqual(first['candidates'], second['candidates'])
            with np.load(root / 'first/selected.npz') as a, np.load(root / 'second/selected.npz') as b:
                for key in ('mean', 'std', 'reference', 'alpha'):
                    np.testing.assert_array_equal(a[key], b[key])

    def test_duplicate_source_is_rejected_before_feature_use(self):
        experiment = self.experiment()
        with TemporaryDirectory() as folder:
            root = Path(folder)
            features, base, rows, _ = self.fixture(root)
            rows[-1]['sha256'] = rows[0]['sha256']
            write_json(root / 'manifest.json', rows)
            with self.assertRaisesRegex(ValueError, 'source'):
                experiment.run(root / 'manifest.json', root / 'split.json', features, root / 'run', base)

    def test_budget_exhaustion_stops_before_fitting(self):
        experiment = self.experiment()
        with TemporaryDirectory() as folder:
            root = Path(folder)
            features, base, _, _ = self.fixture(root)
            with self.assertRaisesRegex(TimeoutError, 'budget'):
                experiment.run(root / 'manifest.json', root / 'split.json', features, root / 'run', base, max_seconds=0.)
