"""Numeric evaluator checks on synthetic caches, without training or raw video."""
import csv
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np

from cashframe.data import read_json, sha256_file, write_json
from cashframe.label_table import export_rows, write_csv
from cashframe.review_bridge import COVERAGE_COLUMNS
from cashframe.sequence_training import temporal_point_metrics
from cashframe.weak_review import prepare_weak_review


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/reviewed_tcn_evaluate.py'
SPEC = importlib.util.spec_from_file_location('reviewed_tcn_evaluate', SCRIPT)
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


class ReviewedTCNEvaluateHelpersTests(unittest.TestCase):
    def test_micro_summary_counts_each_original_point_and_unresolved_extra(self):
        first = temporal_point_metrics([{'timecode_sec': 4.}, {'timecode_sec': 6.},
                                        {'timecode_sec': 0., 'localization_status': 'unresolved'}], [5.], 2.)
        second = temporal_point_metrics([], [1., 2.], 2.)
        result = evaluation.summarize_point_metrics([first, second])
        self.assertEqual((result['reference_points'], result['predictions'], result['matched'],
                          result['missed'], result['extra'], result['unresolved_predictions']),
                         (3, 3, 1, 2, 2, 1))
        self.assertAlmostEqual(result['precision'], 1 / 3)
        self.assertAlmostEqual(result['recall'], 1 / 3)
        self.assertAlmostEqual(result['f1'], 1 / 3)
        self.assertEqual(result['mean_absolute_error_sec'], 1.)

    def test_numeric_events_strip_types_descriptions_and_rule_claims(self):
        result = evaluation.numeric_events([{'start_sec': 1., 'end_sec': 3., 'timecode_sec': 2.,
                                              'score': .8, 'type': 'private category',
                                              'description': 'private observation', 'rule_id': 'private rule',
                                              'localization_status': 'localized'}], 10.)
        self.assertEqual(result, [{'start_sec': 1., 'end_sec': 3., 'timecode_sec': 2.,
                                   'score': .8, 'localization_status': 'localized'}])
        with self.assertRaises(ValueError):
            evaluation.numeric_events([{'start_sec': 0., 'end_sec': 12., 'timecode_sec': 2.}], 10.)


@unittest.skipUnless(importlib.util.find_spec('torch') is not None, 'Local PyTorch required for frozen synthetic inference')
class ReviewedTCNEvaluateTests(unittest.TestCase):
    def setUp(self):
        import torch
        from cashframe.sequence_training import make_model
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.features, self.baseline, self.candidate = [self.root / name for name in ('features', 'baseline', 'candidate')]
        for directory in (self.features, self.baseline, self.candidate):
            directory.mkdir()
        self.encoder = {'model': 'synthetic-numeric-encoder'}
        self.manifest = [{'group_id': letter * 64, 'sha256': letter * 64,
                          'file': f'NEVER_EXPOSE_{part}_{label}.mov', 'label': label,
                          'duration_sec': 12., 'points_sec': [3., 9.] if label else []}
                         for letter, part, label in [('a', 'train', 0), ('b', 'train', 1),
                                                     ('c', 'validation', 0), ('d', 'validation', 1), ('e', 'test', 1)]]
        self.split = {'train': ['a' * 64, 'b' * 64], 'validation': ['c' * 64, 'd' * 64], 'test': ['e' * 64]}
        self.manifest_path, self.split_path = self.root / 'manifest.json', self.root / 'split.json'
        write_json(self.manifest_path, self.manifest)
        write_json(self.split_path, self.split)
        write_json(self.features / 'metadata.json', {'encoder': self.encoder})
        for group in self.manifest:
            if group['group_id'] not in self.split['test']:
                np.savez(self.features / (group['group_id'] + '.npz'), times=np.arange(24) * .5,
                         values=np.full((24, 384), group['label'], dtype=np.float32))
        rows = export_rows(self.manifest, self.split)
        for row in rows:
            if row['source_sha256'] in self.split['train'] + self.split['validation']:
                row.update(start_sec=2. if row['point_sec'] == 3 else 8., end_sec=4. if row['point_sec'] == 3 else 10.,
                           observed_action='PRIVATE_SYNTHETIC_OBSERVATION', reviewer='Synthetic reviewer',
                           annotation_status='draft', visibility='clear')
        master, coverage = self.root / 'events-review.csv', self.root / 'review-coverage.csv'
        write_csv(master, rows, self.manifest, self.split)
        with coverage.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=COVERAGE_COLUMNS)
            writer.writeheader()
            for group in self.manifest[:4]:
                writer.writerow(dict(video_id=Path(group['file']).stem.upper(), source_sha256=group['sha256'],
                                     coverage_complete='yes', reviewer='Synthetic reviewer', annotation_version='1'))
        self.review = self.root / 'review-snapshot'
        prepare_weak_review(master, coverage, self.manifest_path, self.split_path, self.review)
        np.savez(self.baseline / 'trained.npz', mean=np.zeros(1152), std=np.ones(1152),
                 coef=np.zeros(1152), bias=np.asarray(1.))
        write_json(self.baseline / 'config.json', {'selected_variant': 'trained', 'thresholds': {'trained': .5},
                                                   'encoder': self.encoder, 'split': self.split})
        model = make_model(2)
        with torch.no_grad():
            for parameter in model.parameters():
                parameter.zero_()
        torch.save(model.state_dict(), self.candidate / 'weights.pt')
        np.savez(self.candidate / 'normalization.npz', mean=np.zeros(384), std=np.ones(384))
        self.frozen = {'version': 1, 'selection_partitions': ['train', 'validation'], 'classes': [],
                       'encoder': self.encoder, 'supervision': 'weak_reviewed_boundaries',
                       'human_target_confirmation': False, 'legal_applicability': False,
                       'threshold': .6, 'window_sec': 4., 'stride_sec': 1., 'samples': 16,
                       'manifest_sha256': sha256_file(self.manifest_path), 'split_sha256': sha256_file(self.split_path),
                       'training_review_sha256': sha256_file(self.review / 'metadata.json'),
                       'baseline_config_sha256': sha256_file(self.baseline / 'config.json'),
                       'baseline_artifact_sha256': {path.relative_to(self.baseline).as_posix():sha256_file(path)
                                                     for path in self.baseline.rglob('*')
                                                     if path.is_file() and path.suffix.lower() in ('.npz','.json')},
                       'weights_sha256': sha256_file(self.candidate / 'weights.pt'),
                       'normalization_sha256': sha256_file(self.candidate / 'normalization.npz'),
                       'train_validation_feature_sha256': {gid: sha256_file(self.features / (gid + '.npz'))
                                                            for part in ('train', 'validation') for gid in self.split[part]}}
        self.write_record()
        self.output = self.root / 'evaluation.json'

    def write_record(self):
        write_json(self.candidate / 'frozen.json', self.frozen)

    def evaluate(self):
        return evaluation.evaluate_reviewed_tcn(self.manifest_path, self.split_path, self.features,
                                                self.review, self.candidate, self.baseline, self.output)

    def test_real_frozen_inference_opens_only_validation_arrays_and_reports_numeric_weak_metrics(self):
        opened = []
        real_loader = evaluation.load_cached_sequence
        def checked_loader(directory, gid):
            self.assertIn(gid, self.split['validation'])
            opened.append(gid)
            return real_loader(directory, gid)
        with patch.object(evaluation, 'load_cached_sequence', side_effect=checked_loader):
            report = self.evaluate()
        self.assertEqual(opened, self.split['validation'])
        self.assertFalse((self.features / ('e' * 64 + '.npz')).exists())
        self.assertEqual(report['partition'], 'validation')
        self.assertFalse(report['test_features_opened'])
        self.assertFalse(report['promotion_performed'])
        self.assertIsNone(report['type_metrics'])
        self.assertFalse(report['legal_mapping_performed'])
        self.assertEqual(report['models']['candidate']['binary']['confusion_matrix'], [[1, 0], [1, 0]])
        self.assertEqual(report['models']['candidate']['point_metrics']['2']['missed'], 2)
        self.assertEqual(report['models']['baseline']['binary']['confusion_matrix'], [[0, 1], [0, 1]])
        self.assertEqual(len(report['per_source']), 2)
        text = self.output.read_text(encoding='utf-8')
        self.assertNotIn('NEVER_EXPOSE', text)
        self.assertNotIn('PRIVATE_SYNTHETIC_OBSERVATION', text)

    def test_changed_feature_hashes_encoder_or_review_metadata_abort_without_output(self):
        original = dict(self.frozen)
        variants = [('manifest_sha256', '0' * 64), ('split_sha256', '0' * 64),
                    ('encoder', {'model': 'changed'}), ('training_review_sha256', '0' * 64),
                    ('train_validation_feature_sha256', {**self.frozen['train_validation_feature_sha256'], 'e' * 64: '0' * 64})]
        for field, value in variants:
            self.frozen = original | {field: value}
            self.write_record()
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.evaluate()
            self.assertFalse(self.output.exists())

    def test_train_cache_changes_are_detected_without_loading_train_arrays(self):
        path = self.features / ('a' * 64 + '.npz')
        path.write_bytes(path.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, 'feature|cache'):
            self.evaluate()
        self.assertFalse(self.output.exists())

    def test_swapped_baseline_weights_with_unchanged_config_reject_before_feature_inference(self):
        config_bytes=(self.baseline/'config.json').read_bytes()
        np.savez(self.baseline/'trained.npz',mean=np.zeros(1152),std=np.ones(1152),
                 coef=np.zeros(1152),bias=np.asarray(-2.))
        self.assertEqual((self.baseline/'config.json').read_bytes(),config_bytes)
        with patch.object(evaluation,'load_cached_sequence',side_effect=AssertionError('Feature inference began before baseline identity check')):
            with self.assertRaisesRegex(ValueError,'baseline'):
                self.evaluate()
        self.assertFalse(self.output.exists())

    def test_legacy_record_without_selection_time_baseline_identity_is_rejected(self):
        del self.frozen['baseline_artifact_sha256']
        self.write_record()
        with self.assertRaisesRegex(ValueError,'baseline_artifact_sha256'):
            self.evaluate()
        self.assertFalse(self.output.exists())

    def test_added_baseline_artifact_during_inference_fails_exact_inventory_postcheck(self):
        real_predict=evaluation.predict
        def added(*args,**kwargs):
            result=real_predict(*args,**kwargs)
            write_json(self.baseline/'new-artifact.json',{'synthetic':True})
            return result
        with patch.object(evaluation,'predict',side_effect=added):
            with self.assertRaisesRegex(ValueError,'baseline'):
                self.evaluate()
        self.assertFalse(self.output.exists())

    def test_source_edit_after_initial_verification_cannot_be_recaptured_as_valid(self):
        real_review_loader = evaluation.load_weak_review
        def edited(*args, **kwargs):
            result = real_review_loader(*args, **kwargs)
            self.manifest_path.write_bytes(self.manifest_path.read_bytes() + b'\n')
            return result
        with patch.object(evaluation, 'load_weak_review', side_effect=edited):
            with self.assertRaisesRegex(ValueError, 'changed|hash'):
                self.evaluate()
        self.assertFalse(self.output.exists())

    def test_refuses_existing_output_and_changed_validation_cache_during_inference(self):
        self.output.write_text('preserved')
        with self.assertRaises(FileExistsError):
            self.evaluate()
        self.assertEqual(self.output.read_text(), 'preserved')
        self.output.unlink()
        real_predict = evaluation.predict
        def changed(*args, **kwargs):
            result = real_predict(*args, **kwargs)
            path = self.features / ('c' * 64 + '.npz')
            path.write_bytes(path.read_bytes() + b'changed')
            return result
        with patch.object(evaluation, 'predict', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'changed|feature|cache'):
                self.evaluate()
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
