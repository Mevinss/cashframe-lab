"""Synthetic type fixtures exercise mechanics, never production accuracy."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np

from cashframe.taxonomy import InsufficientTypeData, classify_interval, predict_type, train_types


class TaxonomyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.features = self.root / 'features'
        self.features.mkdir()
        self.encoder = {'model': 'synthetic-test-only', 'revision': 'fixture', 'sample_fps': 4}
        (self.features / 'metadata.json').write_text(json.dumps({'encoder': self.encoder}))
        self.manifest, self.split, self.rows = [], {'train': [], 'validation': [], 'test': []}, []
        for category, center in [('fixture-red', [2., -2.]), ('fixture-green', [-2., 2.])]:
            for partition, n in [('train', 3), ('validation', 2)]:
                for i in range(n):
                    gid = f'{category}-{partition}-{i}'
                    self.manifest.append({'group_id': gid, 'sha256': gid, 'duration_sec': 1., 'label': i % 2})
                    self.split[partition].append(gid)
                    values = np.tile(np.asarray(center) + (i-1)*.02, (5, 1))
                    np.savez(self.features / f'{gid}.npz', times=np.linspace(0, 1, 5), values=values)
                    self.rows.append({'source_sha256': gid, 'start_sec': 0, 'end_sec': 1, 'type': category,
                                      'confirmed': True, 'origin': 'human', 'reviewer': 'fixture reviewer',
                                      'observed_fact': 'Synthetic category fixture', 'visibility': 'clear',
                                      'source_partition': 'external', 'training_eligible': False})
        self.artifact = self.root / 'types.npz'

    def train(self):
        for name, value in [('manifest.json', self.manifest), ('split.json', self.split)]:
            (self.root / name).write_text(json.dumps(value), encoding='utf-8')
        (self.root / 'annotations.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in self.rows), encoding='utf-8')
        return train_types(self.root / 'annotations.jsonl', self.root / 'manifest.json',
                           self.root / 'split.json', self.features, self.artifact)

    def test_human_types_train_and_predict_on_independent_separable_sources(self):
        result = self.train()
        self.assertTrue(result['active'])
        self.assertEqual(result['validation']['macro_f1'], 1.)
        self.assertEqual(result['counts']['fixture-red']['train_groups'], 3)
        self.assertEqual(result['counts']['fixture-green']['validation_groups'], 2)
        for value, expected in [([2., -2.], 'fixture-red'), ([-2., 2.], 'fixture-green')]:
            candidate = classify_interval(self.artifact, [0., 1.], [value, value], 0, 1, self.encoder)
            self.assertEqual(candidate['type'], expected)
            self.assertIs(candidate['confirmed'], False)
            self.assertNotIn('rule_id', candidate)
            self.assertEqual(candidate['score_kind'], 'uncalibrated_margin')
            import hashlib
            self.assertEqual(candidate['artifact_sha256'], hashlib.sha256(self.artifact.read_bytes()).hexdigest())

    def test_no_labels_does_not_create_or_replace_artifact(self):
        self.rows = []
        with self.assertRaisesRegex(InsufficientTypeData, 'human'):
            self.train()
        self.assertFalse(self.artifact.exists())
        self.artifact.write_bytes(b'existing-artifact')
        with self.assertRaises(InsufficientTypeData):
            self.train()
        self.assertEqual(self.artifact.read_bytes(), b'existing-artifact')

    def test_repeated_intervals_do_not_replace_independent_sources(self):
        self.rows = [r for r in self.rows if not r['source_sha256'].endswith('train-2')]
        self.rows *= 20
        with self.assertRaisesRegex(InsufficientTypeData, 'independent'):
            self.train()

    def test_unconfirmed_unknown_binary_and_obscured_labels_cannot_train(self):
        original = self.rows
        for update in [{'confirmed': False}, {'origin': 'automatic'}, {'type': 'unknown'},
                       {'type': 'нарушение'}, {'type': 1}, {'visibility': 'partial'},
                       {'type': 'unconfirmed_visual_event'}, {'type': 'visual_anomaly'},
                       {'type': 'Визуальный сигнал — тип не подтверждён'},
                       {'visibility': 'unknown'}, {'visibility': 'unusable'}, {'visibility': None}]:
            with self.subTest(update=update):
                self.rows = [dict(row, **update) if row['type'] == 'fixture-red' else row for row in original]
                with self.assertRaises(InsufficientTypeData):
                    self.train()
                self.rows = original

    def test_test_external_and_jury_claiming_train_are_never_read(self):
        for gid, partition in [('withheld', 'test'), ('jury-source', 'jury')]:
            self.manifest.append({'group_id': gid, 'sha256': gid, 'duration_sec': 1})
            self.split.setdefault(partition, []).append(gid)
            # Deliberately no cache: opening this would fail, independent of its false label.
            self.rows.append(dict(self.rows[0], source_sha256=gid, source_partition='train', training_eligible=True,
                                  type='must-never-be-trained'))
        self.rows.append(dict(self.rows[0], source_sha256='external', source_partition='train', training_eligible=True))
        report = self.train()
        self.assertEqual(set(report['counts']), {'fixture-red', 'fixture-green'})
        self.assertEqual(report['excluded']['withheld_or_external'], 3)

    def test_source_alias_across_partitions_is_rejected(self):
        first = self.manifest[0]
        self.manifest.append(dict(first, group_id='alias'))
        self.split['validation'].append('alias')
        with self.assertRaisesRegex(ValueError, 'leak'):
            self.train()

    def test_weak_validation_artifact_never_assigns_type(self):
        for row in self.rows:
            if '-validation-' in row['source_sha256']:
                row['type'] = 'fixture-green' if row['type'] == 'fixture-red' else 'fixture-red'
        report = self.train()
        self.assertFalse(report['active'])
        self.assertIsNone(classify_interval(self.artifact, [0, 1], [[2., -2.]]*2, 0, 1, self.encoder))

    def test_absent_unverified_ambiguous_and_out_of_distribution_return_unknown(self):
        self.assertIsNone(predict_type(self.artifact, [1, 2]))
        self.train()
        self.assertIsNone(classify_interval(self.artifact, [0, 1], [[2., -2.]]*2, 0, 1))
        for values in [[[0., 0.]]*2, [[100., 100.]]*2]:
            self.assertIsNone(classify_interval(self.artifact, [0, 1], values, 0, 1, self.encoder))

    def test_corrupt_incompatible_or_invalid_features_report_errors(self):
        self.train()
        with self.assertRaisesRegex(ValueError, 'encoder'):
            classify_interval(self.artifact, [0, 1], [[2., -2.]]*2, 0, 1, {'model': 'other'})
        for times, values, start, end in [([0, 1], [[2., -2.]]*2, 1, 0),
                                          ([0, 1], [[float('nan'), 2.]]*2, 0, 1),
                                          ([0, 1], [[2., -2.]]*2, .1, .2)]:
            with self.assertRaises(ValueError):
                classify_interval(self.artifact, times, values, start, end, self.encoder)
        self.artifact.write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            predict_type(self.artifact, [1, 2], self.encoder)

    def test_command_explains_missing_labels_and_writes_no_artifact(self):
        self.rows = []
        with self.assertRaises(InsufficientTypeData):
            self.train()
        command = [sys.executable, 'scripts/train_types.py', '--annotations', str(self.root/'annotations.jsonl'),
                   '--manifest', str(self.root/'manifest.json'), '--split', str(self.root/'split.json'),
                   '--features', str(self.features), '--output', str(self.artifact)]
        result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], env=os.environ.copy(),
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 2)
        self.assertIn('human', result.stdout.lower())
        self.assertFalse(self.artifact.exists())

    def test_artifact_gate_is_rechecked_and_invalid_validation_is_rejected(self):
        self.train()
        with np.load(self.artifact, allow_pickle=False) as archive:
            arrays = {key: archive[key] for key in archive.files}
        metadata = json.loads(str(arrays['metadata'].item()))
        metadata['validation']['macro_f1'] = 0.
        arrays['metadata'] = np.asarray(json.dumps(metadata))
        np.savez(self.artifact, **arrays)
        self.assertIsNone(classify_interval(self.artifact, [0, 1], [[2., -2.]]*2, 0, 1, self.encoder))
        metadata['validation']['macro_f1'] = float('inf')
        arrays['metadata'] = np.asarray(json.dumps(metadata))
        np.savez(self.artifact, **arrays)
        with self.assertRaisesRegex(ValueError, 'artifact'):
            classify_interval(self.artifact, [0, 1], [[2., -2.]]*2, 0, 1, self.encoder)
        metadata['validation']['per_class'] = []
        arrays['metadata'] = np.asarray(json.dumps(metadata))
        np.savez(self.artifact, **arrays)
        with self.assertRaisesRegex(ValueError, 'artifact'):
            predict_type(self.artifact, [1, 2], self.encoder)


if __name__ == '__main__':
    unittest.main()
