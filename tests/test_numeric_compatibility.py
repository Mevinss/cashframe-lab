import unittest
import numpy as np


class NumericCompatibilityTests(unittest.TestCase):
    def test_float32_window_aggregation_preserves_frozen_training_semantics(self):
        from cashframe.learning import window_features
        values=np.array([[1.1234567,8.123456],[2.7654321,1.987654],[9.111119,2.333337]],dtype=np.float32)
        _,windows=window_features(np.array([0.,.25,.5]),values,1.)
        np.testing.assert_array_equal(windows[0,:2],values.mean(0).astype(np.float64))
        np.testing.assert_array_equal(windows[0,2:4],values.std(0).astype(np.float64))
        with self.assertRaises(ValueError):window_features(np.array([0.,.25]),np.array([[np.nan],[1.]],dtype=np.float32),1.)

    def test_roundoff_tie_does_not_depend_on_blas_batch_shape(self):
        from cashframe.research import meets_threshold
        threshold=.5;epsilon=np.finfo(np.float64).eps
        self.assertTrue(bool(meets_threshold(threshold-2*epsilon,threshold)))
        self.assertFalse(bool(meets_threshold(threshold-1e-8,threshold)))
        self.assertTrue(bool(meets_threshold(threshold+1e-8,threshold)))
        with self.assertRaises(ValueError):meets_threshold(float('nan'),threshold)

    def test_calibration_reports_the_same_decisions_as_runtime(self):
        from cashframe.research import calibrate_threshold,meets_threshold,binary_metrics
        scores=np.array([.5,np.nextafter(.5,-np.inf)]);labels=np.array([1,0])
        threshold,reported=calibrate_threshold(scores,labels,'validation')
        self.assertEqual(reported,binary_metrics(labels,meets_threshold(scores,threshold)))
        threshold,reported=calibrate_threshold(scores,[0,0],'validation')
        self.assertGreater(threshold,.5)
        self.assertFalse(meets_threshold(scores,threshold).any())


if __name__=='__main__':unittest.main()
