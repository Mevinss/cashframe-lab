"""Synthetic classifier status fixtures; no claim about bank model accuracy."""
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from cashframe.model_capabilities import classifier_capabilities
from cashframe.taxonomy import FEATURE_VERSION, VERSION, _fingerprint


class ModelCapabilitiesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.encoder = {'model': 'synthetic-only', 'revision': 'fixture'}
        (self.root / 'config.json').write_text(json.dumps({'encoder': self.encoder}))

    def artifact(self, *, quality=1., active=True):
        labels = ['fixture-a', 'fixture-b']
        metadata = {'version': VERSION, 'feature_version': FEATURE_VERSION,
                    'encoder': self.encoder, 'encoder_sha256': _fingerprint(self.encoder),
                    'feature_dimension': 2, 'labels': labels, 'active': active,
                    'source_disjoint': True, 'selection_partitions': ['train', 'validation'],
                    'counts': {label: {'train_groups': 3, 'validation_groups': 2} for label in labels},
                    'validation': {'macro_f1': quality, 'coverage': quality,
                                   'per_class': {label: {'precision': quality, 'recall': quality, 'f1': quality}
                                                 for label in labels}}}
        np.savez(self.root / 'types.npz', metadata=np.asarray(json.dumps(metadata)),
                 mean=np.zeros(2), std=np.ones(2), centroids=np.array([[1., -1.], [-1., 1.]]),
                 radii=np.ones(2))

    def test_absent_does_not_require_a_model_or_claim_trained_types(self):
        (self.root / 'config.json').unlink()
        status = classifier_capabilities(self.root)
        self.assertEqual(status['status'], 'absent')
        self.assertEqual(status['class_count'], 0)
        self.assertIsNone(status['artifact_sha256'])
        self.assertIsNone(status['training_support'])

    def test_accepted_fixture_uses_same_gate_as_runtime_and_omits_labels(self):
        self.artifact()
        status = classifier_capabilities(self.root)
        self.assertEqual(status['status'], 'active')
        self.assertEqual(status['class_count'], 2)
        self.assertEqual(status['model_version'], VERSION)
        self.assertEqual(len(status['artifact_sha256']), 64)
        self.assertEqual(status['training_support']['validation_source_type_pairs'], 4)
        self.assertNotIn('fixture-a', json.dumps(status))
        self.assertEqual(status['normative_classification'], 'not_validated')

    def test_low_quality_cannot_be_activated_by_metadata_flag(self):
        self.artifact(quality=.5, active=True)
        self.assertEqual(classifier_capabilities(self.root)['status'], 'inactive')

    def test_explicitly_inactive_model_remains_inactive(self):
        self.artifact(active=False)
        self.assertEqual(classifier_capabilities(self.root)['status'], 'inactive')

    def test_corrupt_or_encoder_incompatible_is_invalid_without_private_error(self):
        (self.root / 'types.npz').write_bytes(b'private corrupt content')
        status = classifier_capabilities(self.root)
        self.assertEqual(status['status'], 'invalid')
        self.assertNotIn('private', json.dumps(status))
        self.artifact()
        self.assertEqual(classifier_capabilities(self.root, {'model': 'different'})['status'], 'invalid')

    def test_missing_encoder_metadata_does_not_report_an_active_classifier(self):
        self.artifact()
        (self.root / 'config.json').write_text('{}')
        self.assertEqual(classifier_capabilities(self.root)['status'], 'invalid')


if __name__ == '__main__':
    unittest.main()
