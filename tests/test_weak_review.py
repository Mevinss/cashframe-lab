"""Synthetic weak binary supervision tests. No video or private text is read."""
import csv
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from cashframe.data import write_json
from cashframe.label_table import COLUMNS, export_rows, write_csv
from cashframe.review_bridge import COVERAGE_COLUMNS
from cashframe.weak_review import load_weak_review, prepare_weak_review


class WeakReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.manifest = [{'group_id': letter * 64, 'sha256': letter * 64,
                          'file': f'{part}-{label}.mov', 'duration_sec': 10.,
                          'label': label, 'points_sec': [5.] if label else []}
                         for letter, part, label in [('a', 'train', 1), ('b', 'train', 0),
                                                     ('c', 'validation', 1), ('d', 'validation', 0),
                                                     ('e', 'test', 1)]]
        self.split = {'train': ['a' * 64, 'b' * 64],
                      'validation': ['c' * 64, 'd' * 64], 'test': ['e' * 64]}
        self.manifest_path, self.split_path = self.root / 'manifest.json', self.root / 'split.json'
        self.master, self.coverage = self.root / 'events-review.csv', self.root / 'review-coverage.csv'
        write_json(self.manifest_path, self.manifest)
        write_json(self.split_path, self.split)
        self.rows = export_rows(self.manifest, self.split)
        for row in self.rows:
            if row['source_sha256'] != 'e' * 64:
                row.update(start_sec=4., end_sec=6., type_id='synthetic-private-category',
                           observed_action='Synthetic private observation, never metadata.',
                           annotation_status='draft', visibility='clear', reviewer='Synthetic reviewer')
        write_csv(self.master, self.rows, self.manifest, self.split)
        self.coverage_rows = [dict(video_id=f'{part.upper()}-{label}', source_sha256=letter * 64,
                                   coverage_complete='yes', reviewer='Synthetic reviewer', annotation_version='coverage-1')
                              for letter, part, label in [('a', 'train', 1), ('b', 'train', 0),
                                                         ('c', 'validation', 1), ('d', 'validation', 0)]]
        self.write_coverage()
        self.output = self.root / 'snapshot'

    def tearDown(self):
        self.temporary.cleanup()

    def write_coverage(self):
        with self.coverage.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=COVERAGE_COLUMNS)
            writer.writeheader()
            writer.writerows(self.coverage_rows)

    def prepare(self):
        return prepare_weak_review(self.master, self.coverage, self.manifest_path,
                                   self.split_path, self.output)

    def rewrite_master(self):
        write_csv(self.master, self.rows, self.manifest, self.split)

    def test_snapshot_preserves_exact_input_bytes_and_reports_only_weak_binary_contract(self):
        inputs = {path.name: path.read_bytes() for path in (self.master, self.coverage,
                                                            self.manifest_path, self.split_path)}
        metadata = self.prepare()
        for name, original in inputs.items():
            self.assertEqual((self.output / name).read_bytes(), original)
            self.assertEqual(metadata['inputs'][name]['sha256'], hashlib.sha256(original).hexdigest())
            self.assertEqual((self.root / name).read_bytes(), original)
        self.assertEqual(metadata['supervision'], 'organizer_video_labels_with_human_action_boundaries')
        self.assertIs(metadata['human_target_confirmation'], False)
        self.assertIs(metadata['legal_applicability'], False)
        self.assertEqual(metadata['classes'], [])
        self.assertEqual(metadata['selection_partitions'], ['train', 'validation'])
        text = (self.output / 'metadata.json').read_text(encoding='utf-8')
        self.assertNotIn('Synthetic private observation', text)
        self.assertNotIn('synthetic-private-category', text)
        self.assertEqual(metadata['excluded_status_counts'], {'unreviewed': 1})

    def test_loader_uses_draft_action_boundaries_as_untyped_weak_events_and_never_positive_background(self):
        self.prepare()
        reviews = load_weak_review(self.output, self.manifest, self.split)
        self.assertEqual(reviews['a' * 64], {'events': [{'start_sec': 4., 'end_sec': 6., 'type': None}],
                                           'coverage_complete': False})
        self.assertEqual(reviews['c' * 64]['events'], [{'start_sec': 4., 'end_sec': 6., 'type': None}])
        self.assertEqual(reviews['b' * 64], {'events': [], 'coverage_complete': True})
        self.assertEqual(reviews['d' * 64], {'events': [], 'coverage_complete': True})
        self.assertEqual(reviews['e' * 64], {'events': [], 'coverage_complete': False})
        import numpy as np
        from cashframe.sequence_candidate import window_targets
        self.assertEqual(window_targets(np.array([0., 4., 8.]), np.array([1., 5., 10.]),
                                        reviews['a' * 64], []).tolist(), [-1, 1, -1])

    def test_negative_background_requires_explicit_whole_source_human_coverage(self):
        self.coverage_rows[1]['coverage_complete'] = 'no'
        self.write_coverage()
        self.prepare()
        reviews = load_weak_review(self.output, self.manifest, self.split)
        self.assertFalse(reviews['b' * 64]['coverage_complete'])

    def test_confirmed_interval_is_still_weak_and_never_emits_a_type(self):
        self.rows[0]['annotation_status'] = 'confirmed'
        self.rewrite_master()
        self.prepare()
        reviews = load_weak_review(self.output, self.manifest, self.split)
        self.assertIsNone(reviews['a' * 64]['events'][0]['type'])

    def test_rejected_partial_and_point_only_rows_cannot_replace_missing_positive_intervals(self):
        variants = [{'annotation_status': 'rejected'}, {'visibility': 'partial'},
                    {'start_sec': None, 'end_sec': None}]
        original = dict(self.rows[0])
        for mutation in variants:
            self.rows[0] = original | mutation
            self.rewrite_master()
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, 'positive|Positive'):
                self.prepare()
            self.assertFalse(self.output.exists())

    def test_human_boundary_without_reviewer_or_observed_action_is_rejected(self):
        original = dict(self.rows[0])
        for field in ('reviewer', 'observed_action'):
            self.rows[0] = original | {field: ''}
            self.rewrite_master()
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.prepare()
            self.assertFalse(self.output.exists())

    def test_bad_interval_and_negative_source_event_are_rejected(self):
        original = dict(self.rows[0])
        for mutation in ({'start_sec': 5., 'end_sec': 5.},
                         {'video_id': 'TRAIN-0', 'source_sha256': 'b' * 64, 'point_sec': None}):
            self.rows[0] = original | mutation
            self.rewrite_master()
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.prepare()

    def test_withheld_interval_and_withheld_partition_request_are_rejected(self):
        self.rows[-1].update(start_sec=4., end_sec=6., observed_action='Synthetic action',
                              reviewer='Synthetic reviewer', visibility='clear', annotation_status='draft')
        self.rewrite_master()
        with self.assertRaisesRegex(ValueError, 'test|withheld'):
            self.prepare()
        self.rows[-1].update(start_sec=None, end_sec=None, observed_action='', reviewer='',
                              visibility='', annotation_status='unreviewed')
        self.rewrite_master()
        self.prepare()
        with self.assertRaisesRegex(ValueError, 'test|withheld|train'):
            load_weak_review(self.output, self.manifest, self.split, partitions=('test',))

    def test_snapshot_is_write_once_and_detects_modified_snapshot_bytes(self):
        self.prepare()
        with self.assertRaises(FileExistsError):
            self.prepare()
        original = self.output / 'events-review.csv'
        original.write_bytes(original.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'hash|changed|digest'):
            load_weak_review(self.output, self.manifest, self.split)

    def test_loader_rejects_upgraded_supervision_or_changed_trusted_split(self):
        self.prepare()
        metadata_path = self.output / 'metadata.json'
        original = json.loads(metadata_path.read_text(encoding='utf-8'))
        for field, value in [('human_target_confirmation', True), ('legal_applicability', True),
                             ('supervision', 'human_confirmed'), ('classes', ['invented'])]:
            metadata_path.write_text(json.dumps(original | {field: value}), encoding='utf-8')
            with self.subTest(field=field), self.assertRaises(ValueError):
                load_weak_review(self.output, self.manifest, self.split)
        metadata_path.write_text(json.dumps(original), encoding='utf-8')
        changed = self.split | {'train': self.split['validation'], 'validation': self.split['train']}
        with self.assertRaisesRegex(ValueError, 'split|manifest|trusted'):
            load_weak_review(self.output, self.manifest, changed)

    def test_source_edit_during_preparation_aborts_before_snapshot_is_created(self):
        import cashframe.weak_review as weak
        build = weak._build_reviews
        def edited(*args, **kwargs):
            result = build(*args, **kwargs)
            self.master.write_bytes(self.master.read_bytes() + b'\n')
            return result
        with patch('cashframe.weak_review._build_reviews', side_effect=edited):
            with self.assertRaisesRegex(ValueError, 'changed'):
                self.prepare()
        self.assertFalse(self.output.exists())

    def test_subset_loader_does_not_return_other_partition_events(self):
        self.prepare()
        reviews = load_weak_review(self.output, self.manifest, self.split, partitions=('validation',))
        self.assertEqual(reviews['a' * 64]['events'], [])
        self.assertFalse(reviews['b' * 64]['coverage_complete'])
        self.assertEqual(len(reviews['c' * 64]['events']), 1)


if __name__ == '__main__':
    unittest.main()
