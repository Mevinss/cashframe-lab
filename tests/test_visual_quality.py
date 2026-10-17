import unittest
import numpy as np
from cashframe.visual_quality import inspect_sample


class VisualQualityTests(unittest.TestCase):
    def test_measured_motion_is_zero_for_identical_frames(self):
        image=np.full((16,16,3),128,dtype=np.uint8)
        gray,_=inspect_sample(image)
        _,result=inspect_sample(image,gray,.25)
        self.assertEqual(result['image_change_per_sec'],0.)
        self.assertEqual(result['object_visibility'],'not_assessed')

    def test_image_change_is_time_normalized(self):
        gray,_=inspect_sample(np.zeros((16,16,3),dtype=np.uint8))
        _,result=inspect_sample(np.full((16,16,3),255,dtype=np.uint8),gray,.5)
        self.assertEqual(result['image_change_per_sec'],2.)
        self.assertIn('mostly_bright',result['technical_flags'])

    def test_dark_low_detail_is_warning_and_not_confirmation(self):
        _,result=inspect_sample(np.zeros((16,16,3),dtype=np.uint8))
        self.assertIn('mostly_dark',result['technical_flags'])
        self.assertIn('low_detail',result['technical_flags'])
        self.assertEqual(result['object_visibility'],'not_assessed')
