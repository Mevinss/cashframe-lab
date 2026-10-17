import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path

import numpy as np

from cashframe.data import write_json
from cashframe.sequence_candidate import ANNOTATION_COLUMNS
from cashframe.sequence_training import train


class SequenceTrainingTest(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('torch') is not None,
                         'PyTorch is installed separately for local training')
    def test_selection_does_not_open_test_feature_files_or_require_test_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            feature_dir = root/'features'; feature_dir.mkdir()
            baseline_dir = root/'baseline'; baseline_dir.mkdir()
            split = {'train': ['train-n1', 'train-n2', 'train-p1', 'train-p2'],
                     'validation': ['val-n', 'val-p'], 'test': ['test-n', 'test-p']}
            manifest = []
            for partition, ids in split.items():
                for gid in ids:
                    label = int('-p' in gid)
                    manifest.append({'group_id': gid, 'sha256': gid, 'file': gid+'.mov',
                                     'label': label, 'duration_sec': 6., 'points_sec': [2.] if label else []})
                    if partition != 'test':
                        rng = np.random.default_rng(abs(sum(map(ord, gid))))
                        values = rng.normal(0, .1, (24, 384)).astype(np.float32)
                        if label:
                            values[4:16] += .5
                        np.savez(feature_dir/(gid+'.npz'), times=np.arange(24)*.25, values=values)
            encoder = {'model': 'synthetic'}
            write_json(root/'manifest.json', manifest)
            write_json(root/'split.json', split)
            write_json(feature_dir/'metadata.json', {'encoder': encoder})
            write_json(baseline_dir/'config.json', {'split': split, 'encoder': encoder})
            with (root/'review.csv').open('w', newline='', encoding='utf-8') as stream:
                writer = csv.DictWriter(stream, fieldnames=ANNOTATION_COLUMNS)
                writer.writeheader()
                for group in manifest:
                    if group['group_id'] in split['test']:
                        continue
                    positive = bool(group['label'])
                    writer.writerow({'source_sha256': group['sha256'], 'source_file': group['file'],
                                     'organizer_label': group['label'], 'organizer_point_sec': 2 if positive else '',
                                     'start_sec': 1 if positive else '', 'end_sec': 4 if positive else '',
                                     'visibility': 'clear' if positive else '',
                                     'coverage_complete': 'no' if positive else 'yes',
                                     'reviewer': 'test reviewer', 'confirmed': 'yes' if positive else 'no'})
            result = train(root/'manifest.json', root/'split.json', feature_dir, root/'review.csv',
                           root/'candidate', baseline_dir)
            self.assertEqual(result['status'], 'frozen')
            self.assertTrue((root/'candidate'/'weights.pt').is_file())
            self.assertFalse((feature_dir/'test-p.npz').exists())
            self.assertFalse((feature_dir/'test-n.npz').exists())


if __name__ == '__main__':
    unittest.main()
