import unittest
import numpy as np

from cashframe.learning import supervised_mask, select_threshold, window_features
from cashframe.events import merge_events


class LearningTests(unittest.TestCase):
    def test_unknown_positive_regions_excluded(self):
        times = np.array([0., 1., 2., 3., 4., 5.])
        mask, labels = supervised_mask(times, 1, [2.])
        self.assertEqual(times[mask].tolist(), [2.])
        self.assertEqual(labels.tolist(), [1])
        mask, labels = supervised_mask(times, 0, [])
        self.assertTrue(mask.all())
        self.assertFalse(labels.any())

    def test_threshold_requires_validation(self):
        with self.assertRaises(ValueError):
            select_threshold([.2, .8], [0, 1], partition='test')
        threshold, metrics = select_threshold([.2, .8], [0, 1], partition='validation')
        self.assertGreater(threshold, .2)
        self.assertLessEqual(threshold, .8)
        self.assertEqual(metrics['f1'], 1)

    def test_windows_include_both_ends(self):
        centers, values = window_features(np.arange(0, 3, .25), np.ones((12, 3)), 3.)
        self.assertEqual(centers[0], 0)
        self.assertEqual(centers[-1], 3)
        self.assertTrue(np.isfinite(values).all())
        self.assertEqual(values.shape[1], 9)

    def test_merges_repeats_keeps_separate_events(self):
        events = merge_events(np.array([1., 1.5, 2., 8., 8.5]), np.array([.8, .9, .8, .95, .85]), .7, 10.)
        self.assertEqual(len(events), 2)
        self.assertEqual([e['timecode_sec'] for e in events], [1.5, 8.])
        self.assertTrue(all(e['priority'] == 'unspecified' for e in events))
