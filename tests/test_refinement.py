import unittest
import numpy as np
from cashframe.learning import window_features
from cashframe.pipeline import refine_features
from cashframe.features import FeatureSequence


class RefinementTests(unittest.TestCase):
    def test_temporal_motion_is_rate_not_sampling_frequency(self):
        for step in [.25,.125]:
            times=np.arange(0,2,step)
            values=times[:,None]
            centers,features=window_features(times,values,2.)
            self.assertAlmostEqual(features[2,2],1.)

    def test_refinement_adds_only_suspicious_interval_samples(self):
        base=FeatureSequence(np.array([0.,1.,2.,3.]),np.zeros((4,2)),0.)
        samples=[(.5,np.ones((8,8,3),dtype=np.uint8)),(1.5,np.ones((8,8,3),dtype=np.uint8)),(2.5,np.ones((8,8,3),dtype=np.uint8))]
        class Encoder:
            def encode(self,frame):return np.ones(2)
        refined=refine_features(base,samples,Encoder(),[{'start_sec':1.,'end_sec':2.}])
        self.assertEqual(refined.times.tolist(),[0.,1.,1.5,2.,3.])
