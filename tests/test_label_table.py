"""Synthetic annotation security/partition checks; no real video frames are used."""
import copy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from cashframe.label_table import (COLUMNS, confirmed_annotations, export_rows,
                                   read_csv, summary, validate_rows, write_csv)


def fixtures():
    manifest = [{'group_id': p, 'sha256': str(i) * 64, 'file': f'{p}.mov',
                 'duration_sec': 10, 'label': 1, 'points_sec': [2, 7] if i == 1 else [3]}
                for i, p in enumerate(('train', 'validation', 'test'), 1)]
    manifest.append({'group_id': 'negative', 'sha256': '4' * 64, 'file': 'negative.mov',
                     'duration_sec': 10, 'label': 0, 'points_sec': []})
    return manifest, {'train': ['train', 'negative'], 'validation': ['validation'], 'test': ['test']}


def confirmed(row):
    return row | {'type_id': 'visible-action', 'observed_action': 'Synthetic visible action',
                  'visibility': 'clear', 'reviewer': 'synthetic-reviewer',
                  'annotation_status': 'confirmed', 'start_sec': 0, 'end_sec': 9}


class LabelTableTests(unittest.TestCase):
    def setUp(self):
        self.manifest, self.split = fixtures()
        self.rows = export_rows(self.manifest, self.split)

    def validate(self, rows):
        return validate_rows(rows, self.manifest, self.split)

    def test_export_preserves_points_and_does_not_invent_intervals_types_or_negative_events(self):
        self.assertEqual([r['point_sec'] for r in self.rows], [2, 7, 3, 3])
        self.assertEqual(len({r['event_id'] for r in self.rows}), 4)
        self.assertEqual({r['annotation_status'] for r in self.rows}, {'unreviewed'})
        for row in self.rows:
            self.assertEqual(set(row), set(COLUMNS))
            for key in ('start_sec', 'end_sec'):
                self.assertIsNone(row[key])
            for key in ('type_id', 'observed_action', 'visibility', 'rule_id', 'reviewer'):
                self.assertEqual(row[key], '')
        self.assertNotIn('NEGATIVE', [r['video_id'] for r in self.rows])

    def test_utf8_roundtrip_supports_multiple_events_and_human_abstention(self):
        row = confirmed(self.rows[0]) | {'reviewer': 'Әлия', 'observed_action': 'Қол қозғалысы',
                                         'type_id': '', 'visibility': 'partial'}
        with TemporaryDirectory() as directory:
            target = Path(directory) / 'review.csv'
            write_csv(target, [row, self.rows[1]], self.manifest, self.split)
            result = read_csv(target, self.manifest, self.split)
        self.assertEqual(result[0]['reviewer'], 'Әлия')
        self.assertEqual(confirmed_annotations(result, self.manifest, self.split), [])

    def test_partition_membership_is_derived_and_test_never_enters_training(self):
        rows = [confirmed(r) for r in self.rows]
        converted = confirmed_annotations(rows, self.manifest, self.split)
        self.assertEqual([r['source_partition'] for r in converted], ['train', 'train', 'validation'])
        self.assertEqual([r['training_eligible'] for r in converted], [True, True, False])
        for row in self.rows:
            for field in ('training_eligible', 'source_partition', 'origin', 'confirmed'):
                with self.assertRaises(ValueError):
                    self.validate([row | {field: 'train'}])

    def test_conflicting_source_hashes_and_split_leaks_are_rejected(self):
        for mutation in ({'source_sha256': 'f' * 64}, {'video_id': 'OTHER'}, {'point_sec': 11},
                         {'start_sec': 0}, {'start_sec': 8, 'end_sec': 9},
                         {'point_sec': 'nan'}, {'end_sec': 'inf'}, {'annotation_status': 'automatic'},
                         {'type_id': 'violation', 'annotation_status': 'draft'},
                         {'visibility': 'visible'}, {'annotation_version': ''}, {'event_id': '../escape'}):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.validate([self.rows[0] | mutation])
        with self.assertRaises(ValueError):
            self.validate([self.rows[0], self.rows[0]])
        with self.assertRaises(ValueError):
            export_rows(self.manifest, self.split | {'test': ['train']})
        aliases = copy.deepcopy(self.manifest)
        aliases[1]['sha256'] = aliases[0]['sha256']
        with self.assertRaises(ValueError):
            export_rows(aliases, self.split)

    def test_export_cannot_overwrite_workbook_and_formulas_are_rejected(self):
        with TemporaryDirectory() as directory:
            workbook = Path(directory) / 'open_labels.xlsx'
            workbook.write_bytes(b'original-workbook')
            with self.assertRaises(ValueError):
                write_csv(workbook, self.rows, self.manifest, self.split)
            self.assertEqual(workbook.read_bytes(), b'original-workbook')
        for formula in ('=WEBSERVICE("secret")', '+SUM(1,2)', '@SUM(1,2)'):
            with self.assertRaises(ValueError):
                self.validate([confirmed(self.rows[0]) | {'observed_action': formula}])

    def test_confirmation_requires_provenance_clear_type_and_observed_fact(self):
        base = confirmed(self.rows[0])
        for mutation in ({'reviewer': ''}, {'observed_action': ''}, {'visibility': ''},
                         {'visibility': 'partial'}, {'type_id': 'unknown'},
                         {'annotation_status': 'unreviewed'}, {'rule_id': 'R1', 'annotation_status': 'draft'}):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.validate([base | mutation])
        point_only = base | {'start_sec': None, 'end_sec': None}
        self.assertEqual(confirmed_annotations([point_only], self.manifest, self.split), [])
        self.assertEqual(summary(self.rows, self.manifest, self.split)['type_classifier_status'], 'not_trained')

    def test_csv_rejects_extra_columns_duplicate_headers_and_ragged_rows(self):
        with TemporaryDirectory() as directory:
            target = Path(directory) / 'review.csv'
            for header in [','.join(COLUMNS) + ',training_eligible', ','.join(COLUMNS[:-1])]:
                target.write_text(header + '\n', encoding='utf-8')
                with self.assertRaises(ValueError):
                    read_csv(target, self.manifest, self.split)


if __name__ == '__main__':
    unittest.main()
