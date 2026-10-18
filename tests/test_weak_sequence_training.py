"""Synthetic-only checks for explicitly weak temporal supervision."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from cashframe.data import write_json
from cashframe.sequence_training import train


class WeakSequenceTrainingTest(unittest.TestCase):
    def test_cli_rejects_weak_mode_for_other_phases_before_reading_inputs(self):
        root = Path(__file__).resolve().parents[1]
        process = subprocess.run([sys.executable, str(root/'scripts/sequence_experiment.py'),
                                  'review-table', '--supervision', 'weak_reviewed_boundaries',
                                  '--manifest', 'does-not-exist.json'], cwd=root,
                                 env=dict(os.environ, PYTHONPATH=str(root/'src')),
                                 capture_output=True, text=True)
        self.assertEqual(process.returncode, 2)
        self.assertIn('Weak supervision is supported only for train', process.stderr)

    def test_validation_weighting_is_invariant_to_repeated_windows_of_one_source(self):
        from cashframe.sequence_training import source_balanced_weights
        initial = source_balanced_weights(['normal', 'event'], [0, 1])
        repeated = source_balanced_weights(['normal']*19+['event'], [0]*19+[1])
        self.assertAlmostEqual(float(initial @ np.array([.2, .8])), .5)
        self.assertAlmostEqual(float(repeated @ np.array([.2]*19+[.8])), .5)
        mixed = source_balanced_weights(['n1']*8+['n2']+['p'], [0]*9+[1])
        self.assertAlmostEqual(float(mixed[:8].sum()), .25)
        self.assertAlmostEqual(float(mixed[8]), .25)
        self.assertAlmostEqual(float(mixed[9]), .5)

    def test_unknown_supervision_is_rejected_before_reading_data(self):
        with self.assertRaisesRegex(ValueError, 'supervision'):
            train('missing', 'missing', 'missing', 'missing', 'missing', 'missing',
                  supervision='pretend_human_confirmation')

    def test_weak_candidate_is_binary_and_records_unconfirmed_scope_and_input_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            features = root/'features'; features.mkdir()
            baseline = root/'baseline'; baseline.mkdir()
            review = root/'review'; review.mkdir()
            split = {'train': ['tn', 'tp'], 'validation': ['vn', 'vp'], 'test': ['withheld']}
            manifest, reviews = [], {}
            for partition, ids in split.items():
                for gid in ids:
                    label = int(gid.endswith('p'))
                    manifest.append(dict(group_id=gid, sha256=gid, file=gid+'.mov',
                                         label=label, duration_sec=6., points_sec=[3.] if label else []))
                    reviews[gid] = dict(coverage_complete=not label,
                                        events=[dict(start_sec=1., end_sec=4., type='must-not-be-a-trained-type')] if label else [])
                    if partition != 'test':
                        values = np.full((24, 384), -.25, dtype=np.float32)
                        if label:values[4:17] = .5
                        np.savez(features/(gid+'.npz'), times=np.arange(24)*.25, values=values)
            encoder = {'model': 'synthetic-only'}
            write_json(root/'manifest.json', manifest)
            write_json(root/'split.json', split)
            write_json(features/'metadata.json', {'encoder': encoder})
            write_json(baseline/'config.json', {'split': split, 'encoder': encoder})
            write_json(review/'metadata.json', {
                'supervision': 'organizer_video_labels_with_human_action_boundaries',
                'human_target_confirmation': False, 'legal_applicability': False})
            write_json(review/'manifest.json', manifest)
            write_json(review/'split.json', split)
            (review/'events-review.csv').write_text('Synthetic frozen input only')
            (review/'review-coverage.csv').write_text('Synthetic frozen input only')
            loaded = (manifest, split, {g['group_id']: g for g in manifest}, reviews, {'encoder': encoder})
            with patch('cashframe.sequence_training._load_inputs', return_value=loaded), \
                    patch('cashframe.sequence_training.baseline_cached_events', return_value=([], True)):
                result = train(root/'manifest.json', root/'split.json', features, review,
                               root/'candidate', baseline, supervision='weak_reviewed_boundaries')
            frozen = json.loads((root/'candidate/frozen.json').read_text())
            self.assertEqual(frozen['classes'], [])
            self.assertEqual(frozen['supervision'], 'weak_reviewed_boundaries')
            self.assertIs(frozen['human_target_confirmation'], False)
            self.assertIs(frozen['legal_applicability'], False)
            self.assertIs(frozen['promotion_performed'], False)
            self.assertEqual(set(frozen['train_validation_feature_sha256']), {'tn', 'tp', 'vn', 'vp'})
            self.assertEqual(set(frozen['baseline_artifact_sha256']), {'config.json'})
            self.assertEqual(frozen['baseline_validation']['recall'], 1.)
            self.assertGreaterEqual(frozen['validation']['recall'], frozen['baseline_validation']['recall'])
            self.assertFalse(frozen['test_opened_for_selection'])
            self.assertEqual(frozen['early_stopping_objective'], 'source_class_balanced_cross_entropy')
            self.assertGreaterEqual(frozen['best_epoch'], 1)
            self.assertEqual(len(frozen['validation_loss_history']), frozen['epochs'])
            self.assertEqual(result['classes'], 0)


if __name__ == '__main__':
    unittest.main()
