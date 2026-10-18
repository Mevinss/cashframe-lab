"""Synthetic review handoff tests; no real video or frame is read."""
import copy
import csv
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from cashframe.data import write_json
from cashframe.label_table import export_rows, write_csv
from cashframe.review_bridge import (COVERAGE_COLUMNS, coverage_rows, prepare_review,
                                    sequence_rows, validate_coverage, write_bridge)
from cashframe.sequence_candidate import read_review_table, window_targets


def fixtures():
    manifest = [
        {'group_id': 'a' * 64, 'sha256': 'a' * 64, 'file': 'train.mov',
         'files': ['train.mov'], 'duration_sec': 10., 'label': 1, 'points_sec': [3.]},
        {'group_id': 'b' * 64, 'sha256': 'b' * 64, 'file': 'normal.mov',
         'duration_sec': 10., 'label': 0, 'points_sec': []},
        {'group_id': 'c' * 64, 'sha256': 'c' * 64, 'file': 'validation.mov',
         'duration_sec': 10., 'label': 1, 'points_sec': [5.]},
        {'group_id': 'd' * 64, 'sha256': 'd' * 64, 'file': 'test.mov',
         'duration_sec': 10., 'label': 1, 'points_sec': [7.]},
    ]
    return manifest, {'train': ['a' * 64, 'b' * 64],
                      'validation': ['c' * 64], 'test': ['d' * 64]}


def confirmed(row):
    return row | {'annotation_status': 'confirmed', 'reviewer': 'human-reviewer',
                  'observed_action': 'Synthetic visible event', 'visibility': 'clear',
                  'type_id': 'reviewed-action', 'start_sec': 2., 'end_sec': 8.,
                  'annotation_version': 'review-2'}


class ReviewBridgeTests(unittest.TestCase):
    def setUp(self):
        self.manifest, self.split = fixtures()
        self.master = export_rows(self.manifest, self.split)
        self.coverage = coverage_rows(self.manifest, self.split)

    def bridge(self, rows=None, coverage=None):
        return sequence_rows(self.master if rows is None else rows,
                             self.coverage if coverage is None else coverage,
                             self.manifest, self.split)

    def test_unreviewed_points_never_become_events_or_coverage(self):
        rows, provenance = self.bridge()
        self.assertEqual(len(rows), 3)
        self.assertEqual({r['source_sha256'] for r in rows}, {'a' * 64, 'b' * 64, 'c' * 64})
        for row in rows:
            self.assertEqual(row['confirmed'], '')
            self.assertEqual(row['coverage_complete'], '')
            self.assertEqual(row['start_sec'], '')
            self.assertEqual(row['end_sec'], '')
        self.assertTrue(all(r['kind'] == 'coverage' for r in provenance))

    def test_confirmed_event_retains_interval_type_version_and_trusted_partition(self):
        rows, provenance = self.bridge([confirmed(r) for r in self.master])
        events = [r for r in rows if r['confirmed'] == 'yes']
        self.assertEqual([(r['source_file'], r['organizer_label'], r['organizer_point_sec'],
                           r['start_sec'], r['end_sec'], r['event_type']) for r in events],
                         [('train.mov', 1, 3., 2., 8., 'reviewed-action'),
                          ('validation.mov', 1, 5., 2., 8., 'reviewed-action')])
        facts = [r for r in provenance if r['kind'] == 'event']
        self.assertEqual([(r['source_partition'], r['training_eligible']) for r in facts],
                         [('train', True), ('validation', False)])
        self.assertEqual(facts[0]['event_id'], 'a' * 64 + '-1')
        self.assertEqual(facts[0]['annotation_version'], 'review-2')
        self.assertTrue(all(r['coverage_complete'] == '' for r in events))

    def test_only_separate_human_coverage_enables_background_and_existing_reader_accepts(self):
        coverage = [r | {'coverage_complete': 'yes', 'reviewer': 'coverage-human',
                         'annotation_version': 'full-1'} for r in self.coverage]
        rows, provenance = self.bridge([confirmed(r) for r in self.master], coverage)
        with TemporaryDirectory() as directory:
            target = Path(directory) / 'review.csv'
            with target.open('w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            reviews = read_review_table(target, self.manifest, self.split,
                                        partitions=('train', 'validation'))
        import numpy as np
        starts, ends = np.array([0., 4., 9.]), np.array([1., 5., 10.])
        self.assertEqual(window_targets(starts, ends, reviews['a' * 64], []).tolist(), [0, 1, 0])
        self.assertEqual(window_targets(starts, ends, reviews['b' * 64], []).tolist(), [0, 0, 0])
        self.assertEqual([r['reviewer'] for r in provenance if r['kind'] == 'coverage'],
                         ['coverage-human'] * 3)

    def test_one_confirmed_event_leaves_unobserved_background_unlabeled(self):
        rows, _ = self.bridge([confirmed(r) for r in self.master])
        with TemporaryDirectory() as directory:
            target = Path(directory) / 'review.csv'
            with target.open('w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            reviews = read_review_table(target, self.manifest, self.split,
                                        partitions=('train', 'validation'))
        import numpy as np
        self.assertEqual(window_targets(np.array([0., 4., 8.]), np.array([1., 5., 10.]),
                                        reviews['a' * 64], []).tolist(), [-1, 1, -1])

    def test_full_coverage_is_downgraded_when_an_annotated_interval_cannot_be_exported(self):
        clear = confirmed(self.master[0]) | {'end_sec': 4.}
        uncertain = confirmed(self.master[0]) | {'event_id': 'uncertain', 'point_sec': None,
                                                'start_sec': 7., 'end_sec': 9., 'type_id': '',
                                                'visibility': 'partial'}
        coverage = [r | {'coverage_complete': 'yes', 'reviewer': 'coverage-human'}
                    for r in self.coverage]
        for status in ('confirmed', 'draft', 'rejected', 'unreviewed'):
            row = uncertain | {'annotation_status': status}
            if status == 'unreviewed':
                row.update(observed_action='', visibility='', reviewer='')
            with self.subTest(status=status):
                rows, provenance = self.bridge([clear, row, confirmed(self.master[1])], coverage)
                coverage_row = next(r for r in rows if r['source_sha256'] == 'a' * 64
                                    and r['confirmed'] != 'yes')
                self.assertEqual(coverage_row['coverage_complete'], 'no')
                record = next(r for r in provenance if r['source_sha256'] == 'a' * 64
                              and r['kind'] == 'coverage')
                self.assertEqual(record['coverage_requested'], 'yes')
                self.assertEqual(record['coverage_downgrade_reason'], 'unexported_annotated_intervals')
                self.assertEqual(record['unexported_event_ids'], ['uncertain'])
        with TemporaryDirectory() as directory:
            target = Path(directory) / 'uncertain.csv'
            with target.open('w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            reviews = read_review_table(target, self.manifest, self.split,
                                        partitions=('train', 'validation'))
        import numpy as np
        self.assertEqual(window_targets(np.array([7.5]), np.array([8.5]),
                                        reviews['a' * 64], []).tolist(), [-1])

    def test_original_point_without_interval_does_not_cancel_explicit_coverage(self):
        clear = confirmed(self.master[0]) | {'end_sec': 4.}
        point = self.master[0] | {'event_id': 'original-other-point', 'point_sec': 7.}
        coverage = [r | {'coverage_complete': 'yes', 'reviewer': 'coverage-human'}
                    for r in self.coverage]
        rows, _ = self.bridge([clear, point], coverage)
        record = next(r for r in rows if r['source_sha256'] == 'a' * 64 and r['confirmed'] != 'yes')
        self.assertEqual(record['coverage_complete'], 'yes')

    def test_untyped_clear_confirmed_interval_is_binary_event_with_trusted_provenance(self):
        row = confirmed(self.master[0]) | {'type_id': ''}
        rows, provenance = self.bridge([row])
        events = [r for r in rows if r['confirmed'] == 'yes']
        self.assertEqual([(r['source_file'], r['start_sec'], r['end_sec'], r['event_type'])
                          for r in events], [('train.mov', 2., 8., '')])
        self.assertEqual(events[0]['reviewer'], 'human-reviewer')
        self.assertEqual(events[0]['coverage_complete'], '')
        fact = next(r for r in provenance if r['kind'] == 'event')
        self.assertEqual((fact['source_partition'], fact['training_eligible'],
                          fact['event_id'], fact['annotation_version']),
                         ('train', True, 'a' * 64 + '-1', 'review-2'))
        rows, _ = self.bridge([confirmed(r) | {'type_id': ''} for r in self.master])
        with TemporaryDirectory() as directory:
            target = Path(directory) / 'binary.csv'
            with target.open('w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            reviews = read_review_table(target, self.manifest, self.split,
                                        partitions=('train', 'validation'))
        self.assertEqual(reviews['a' * 64]['events'],
                         [{'start_sec': 2., 'end_sec': 8., 'type': None}])
        self.assertFalse(reviews['a' * 64]['coverage_complete'])

    def test_draft_point_only_and_partial_abstention_do_not_become_events(self):
        variants = [confirmed(self.master[0]) | {'annotation_status': 'draft'},
                    confirmed(self.master[0]) | {'start_sec': None, 'end_sec': None},
                    confirmed(self.master[0]) | {'type_id': '', 'visibility': 'partial'}]
        for variant in variants:
            with self.subTest(variant=variant):
                rows, _ = self.bridge([variant])
                self.assertFalse(any(r['confirmed'] == 'yes' for r in rows))

    def test_zero_length_and_negative_source_events_are_rejected(self):
        zero = confirmed(self.master[0]) | {'start_sec': 3., 'end_sec': 3.}
        negative = confirmed(self.master[0]) | {'video_id': 'NORMAL', 'source_sha256': 'b' * 64,
                                               'point_sec': None, 'event_id': 'normal-event'}
        for row in (zero, negative):
            with self.subTest(row=row), self.assertRaises(ValueError):
                self.bridge([row])

    def test_overlapping_conflicting_events_are_rejected(self):
        first = confirmed(self.master[0])
        second = first | {'event_id': 'other', 'type_id': 'different-action'}
        with self.assertRaisesRegex(ValueError, 'conflicting'):
            self.bridge([first, second])

    def test_coverage_needs_known_source_matching_video_reviewer_and_version(self):
        base = self.coverage[0]
        mutations = ({'source_sha256': 'f' * 64}, {'video_id': 'OTHER'},
                     {'coverage_complete': 'true'}, {'coverage_complete': 'yes', 'reviewer': ''},
                     {'annotation_version': ''}, {'coverage_complete': 'yes', 'reviewer': '=SUM(1)'},
                     {'coverage_complete': 'yes', 'reviewer': '-SUM(1,2)'},
                     {'source_partition': 'train'}, {'coverage_complete': True})
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate_coverage([base | mutation], self.manifest, self.split)
        with self.assertRaisesRegex(ValueError, 'unique'):
            validate_coverage([base, base], self.manifest, self.split)
        accepted = validate_coverage([base | {'coverage_complete': 'yes', 'reviewer': 'Айша-Мария',
                                              'annotation_version': 'проверка-2'}], self.manifest, self.split)
        self.assertEqual(accepted[0]['reviewer'], 'Айша-Мария')
        self.assertEqual(accepted[0]['annotation_version'], 'проверка-2')

    def test_coverage_rejects_withheld_test_and_split_leaks(self):
        test = self.coverage[0] | {'video_id': 'TEST', 'source_sha256': 'd' * 64}
        with self.assertRaisesRegex(ValueError, 'train|validation|withheld'):
            validate_coverage([test], self.manifest, self.split)
        split = copy.deepcopy(self.split)
        split['test'].append('a' * 64)
        with self.assertRaises(ValueError):
            sequence_rows(self.master, self.coverage, self.manifest, split)

    def write_inputs(self, directory):
        directory = Path(directory)
        manifest, split = directory / 'manifest.json', directory / 'split.json'
        master, coverage = directory / 'master.csv', directory / 'coverage.csv'
        write_json(manifest, self.manifest)
        write_json(split, self.split)
        write_csv(master, [confirmed(r) for r in self.master], self.manifest, self.split)
        with coverage.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=COVERAGE_COLUMNS)
            writer.writeheader()
            writer.writerows(self.coverage)
        return master, coverage, manifest, split

    def test_versioned_bridge_records_input_hashes_and_never_overwrites_inputs_or_outputs(self):
        with TemporaryDirectory() as directory:
            master, coverage, manifest, split = self.write_inputs(directory)
            output = Path(directory) / 'derived.csv'
            result = write_bridge(master, coverage, manifest, split, output)
            self.assertEqual(result['confirmed_events'], 2)
            metadata = json.loads(output.with_suffix('.bridge.json').read_text(encoding='utf-8'))
            self.assertEqual(metadata['schema_version'], 'cashframe.sequence-review-bridge/1')
            self.assertEqual(metadata['selection_partitions'], ['train', 'validation'])
            self.assertEqual(metadata['excluded_master_rows'], {'test': 1})
            import hashlib
            self.assertEqual(metadata['inputs']['master']['sha256'], hashlib.sha256(master.read_bytes()).hexdigest())
            self.assertEqual(metadata['output']['sha256'], hashlib.sha256(output.read_bytes()).hexdigest())
            original = master.read_bytes()
            for target in (master, coverage, output, Path(directory) / 'original.xlsx'):
                with self.subTest(target=target), self.assertRaises((ValueError, FileExistsError)):
                    write_bridge(master, coverage, manifest, split, target)
            self.assertEqual(master.read_bytes(), original)

    def test_prepare_copies_only_unreviewed_train_validation_original_points(self):
        with TemporaryDirectory() as directory:
            master, coverage, manifest, split = self.write_inputs(directory)
            write_csv(master, self.master, self.manifest, self.split)
            table = Path(directory) / 'handoff.csv'
            cover = Path(directory) / 'handoff.coverage.csv'
            result = prepare_review(master, manifest, split, table, cover)
            with table.open(encoding='utf-8-sig', newline='') as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual([(r['video_id'], r['point_sec'], r['annotation_status']) for r in rows],
                             [('TRAIN', '3.0', 'unreviewed'), ('VALIDATION', '5.0', 'unreviewed')])
            self.assertEqual(result['copied_points'], 2)
            self.assertTrue(table.with_suffix('.README.md').exists())
            with cover.open(encoding='utf-8-sig', newline='') as stream:
                self.assertEqual(len(list(csv.DictReader(stream))), 3)

    def test_cli_returns_blocked_without_creating_handoff_for_forged_coverage(self):
        with TemporaryDirectory() as directory:
            master, coverage, manifest, split = self.write_inputs(directory)
            with coverage.open('w', encoding='utf-8', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=COVERAGE_COLUMNS)
                writer.writeheader()
                writer.writerow(self.coverage[0] | {'coverage_complete': 'yes'})
            output = Path(directory) / 'derived.csv'
            completed = subprocess.run([sys.executable, 'scripts/sequence_review_bridge.py', 'convert',
                                        '--manifest', str(manifest), '--split', str(split),
                                        '--table', str(master), '--coverage', str(coverage),
                                        '--output', str(output)], capture_output=True, text=True,
                                       cwd=Path(__file__).resolve().parents[1])
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(json.loads(completed.stdout)['status'], 'blocked')
            self.assertFalse(output.exists())

    def test_cli_prepare_and_convert_use_canonical_ui_coverage_path_by_default(self):
        with TemporaryDirectory() as directory:
            master, _, manifest, split = self.write_inputs(directory)
            script = Path(__file__).resolve().parents[1] / 'scripts/sequence_review_bridge.py'
            env = dict(os.environ, PYTHONPATH=str(script.parents[1] / 'src'))
            common = ['--manifest', str(manifest), '--split', str(split), '--table', str(master)]
            prepared = subprocess.run([sys.executable, str(script), 'prepare', *common,
                                       '--output', str(Path(directory) / 'review.csv')],
                                      cwd=directory, env=env, capture_output=True, text=True)
            self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
            canonical = Path(directory) / 'local/dataset/review-coverage.csv'
            self.assertTrue(canonical.exists())
            converted = subprocess.run([sys.executable, str(script), 'convert', *common,
                                        '--output', str(Path(directory) / 'derived.csv')],
                                       cwd=directory, env=env, capture_output=True, text=True)
            self.assertEqual(converted.returncode, 0, converted.stdout + converted.stderr)
            metadata = json.loads((Path(directory) / 'derived.bridge.json').read_text(encoding='utf-8'))
            self.assertEqual(Path(metadata['inputs']['coverage']['path']), canonical)

    def test_conversion_rejects_input_changed_between_parse_and_provenance_hash(self):
        with TemporaryDirectory() as directory:
            master, coverage, manifest, split = self.write_inputs(directory)
            output = Path(directory) / 'derived.csv'
            from cashframe.review_bridge import _read_coverage
            def concurrent_edit(path, groups, partitions):
                rows = _read_coverage(path, groups, partitions)
                master.write_bytes(master.read_bytes() + b'\n')
                return rows
            with patch('cashframe.review_bridge._read_coverage', side_effect=concurrent_edit):
                with self.assertRaisesRegex(ValueError, 'changed'):
                    write_bridge(master, coverage, manifest, split, output)
            self.assertFalse(output.exists())
            self.assertFalse(output.with_suffix('.bridge.json').exists())


if __name__ == '__main__':
    unittest.main()
