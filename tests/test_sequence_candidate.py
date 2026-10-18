import csv
import tempfile
import unittest
from pathlib import Path

import numpy as np

from cashframe.data import write_json
from cashframe.sequence_candidate import (ANNOTATION_COLUMNS, checked_split, evidence_times,
                                          explain_event, merge_predictions, read_review_table,
                                          temporal_windows, window_targets, write_review_table)


class SequenceCandidateTest(unittest.TestCase):
    def test_ordered_overlapping_windows_span_ends(self):
        times = np.arange(0, 6, .5)
        values = times[:, None].astype(np.float32)
        starts, ends, windows = temporal_windows(times, values, 6., window_sec=2., stride_sec=1., samples=4)
        self.assertEqual(starts[0], 0)
        self.assertEqual(ends[-1], 6)
        self.assertTrue(np.all(windows[0, 1:, 0] > windows[0, :-1, 0]))
        self.assertTrue(np.any(starts[1:] < ends[:-1]))

    def test_unreviewed_background_is_never_negative(self):
        starts, ends = np.array([0., 4., 8.]), np.array([2., 6., 10.])
        review = {'events': [{'start_sec': 4., 'end_sec': 6., 'type': None}], 'coverage_complete': False}
        self.assertEqual(window_targets(starts, ends, review, []).tolist(), [-1, 1, -1])
        review['coverage_complete'] = True
        self.assertEqual(window_targets(starts, ends, review, []).tolist(), [0, 1, 0])

    def test_duplicate_group_cannot_cross_split(self):
        manifest = [{'group_id': 'a', 'sha256': 'a', 'duplicate_group': 'same'},
                    {'group_id': 'b', 'sha256': 'b', 'duplicate_group': 'same'}]
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            checked_split(manifest, {'train': ['a'], 'validation': ['b'], 'test': []})

    def test_review_template_does_not_turn_organizer_point_into_interval(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            manifest = [{'group_id': 'a', 'sha256': 'a', 'file': 'local.mov',
                         'duration_sec': 10., 'label': 1, 'points_sec': [5.]}]
            write_json(root/'manifest.json', manifest)
            write_review_table(root/'manifest.json', root/'review.csv')
            with (root/'review.csv').open(encoding='utf-8-sig', newline='') as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual(row['organizer_point_sec'], '5.0')
            self.assertEqual(row['start_sec'], '')
            self.assertEqual(row['event_type'], '')
            with self.assertRaisesRegex(ValueError, 'lacks confirmed'):
                read_review_table(root/'review.csv', manifest, {'train': ['a'], 'validation': [], 'test': []})

    def test_merging_preserves_category_and_original_timestamp(self):
        starts, ends = np.array([0., 1., 6.]), np.array([4., 5., 10.])
        probabilities = np.array([[.1, .9], [.2, .8], [.1, .9]])
        events = merge_predictions(starts, ends, probabilities, .7, [], max_gap_sec=.5)
        self.assertEqual(len(events), 2)
        self.assertEqual((events[0]['start_sec'], events[0]['end_sec']), (0., 5.))
        self.assertEqual(evidence_times(events[0], [0., 1., 2., 3., 4., 5.], 6.), [0., 2., 5.])

    def test_rule_not_claimed_without_enabled_verified_mapping(self):
        rule = {'id': 'r1', 'title': 'Review', 'verified': True, 'automatic_matching_enabled': False,
                'applicability': 'a', 'visual_cues': ['b'],
                'document': 'NB RK cash rules', 'url': 'https://adilet.zan.kz/rus/docs/test'}
        explanation = explain_event({'type': 'Human category'}, [rule], {'Human category': 'r1'})
        self.assertIsNone(explanation['rule_id'])


if __name__ == '__main__':
    unittest.main()
