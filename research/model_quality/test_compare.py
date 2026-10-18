import unittest
from research.model_quality.compare import compare, metrics, wilson


class QualityTests(unittest.TestCase):
    def test_metrics_reuse_and_fp_fn_tradeoff(self):
        result=compare([0,0,1,1],[1,1,1,1],[0,0,0,1])
        self.assertEqual(result['delta']['fp'],-2)
        self.assertEqual(result['delta']['fn'],1)
        self.assertEqual(result['candidate']['specificity'],1)
        self.assertEqual(result['candidate']['recall'],.5)
        self.assertFalse(result['promotion'])

    def test_paired_exact_uncertainty_and_no_independence_claim(self):
        result=compare([0,1],[0,1],[0,1])
        self.assertEqual(result['paired']['exact_two_sided_p'],1)
        self.assertFalse(result['independence_verified'])
        self.assertLess(result['accuracy_wilson_95']['candidate'][0],1)
        self.assertIsNone(wilson(0,0))

    def test_invalid_and_empty_data_fail(self):
        for labels,pred in [([],[]),([0],[0,1]),([2],[0])]:
            with self.assertRaises(ValueError):metrics(labels,pred)


if __name__=='__main__':unittest.main()
