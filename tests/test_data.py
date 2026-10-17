import unittest

from cashframe.data import build_split, group_records, parse_timecode


class DataTests(unittest.TestCase):
    def test_russian_minute_timecode(self):
        for raw, expected in [('1минута 8 секунда', 68), ('22 секунда', 22), ('01:08', 68), ('', None)]:
            self.assertEqual(parse_timecode(raw), expected)

    def test_duplicate_group_keeps_both_points(self):
        grouped = group_records([
            {'sha256': 'same', 'file': 'a.mov', 'label': 1, 'point_sec': 57},
            {'sha256': 'same', 'file': 'b.mov', 'label': 1, 'point_sec': 68},
        ])
        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0]['points_sec'], [57, 68])
        self.assertEqual(grouped[0]['files'], ['a.mov', 'b.mov'])

    def test_conflicting_duplicate_labels_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            group_records([{'sha256': 'x', 'file': 'a', 'label': 1, 'point_sec': 2},
                           {'sha256': 'x', 'file': 'b', 'label': 0, 'point_sec': None}])

    def test_split_groups_do_not_overlap(self):
        groups = [{'group_id': str(i), 'label': i % 2} for i in range(40)]
        split = build_split(groups, 42)
        self.assertEqual([len(split[k]) for k in ('train', 'validation', 'test')], [24, 8, 8])
        sets = [set(split[k]) for k in ('train', 'validation', 'test')]
        self.assertFalse(sets[0] & sets[1] or sets[0] & sets[2] or sets[1] & sets[2])
        self.assertEqual(len(set.union(*sets)), 40)
        for ids in sets:
            self.assertEqual({int(i) % 2 for i in ids}, {0, 1})
        self.assertEqual(split, build_split(groups, 42))

    def test_unknown_video_has_no_imputed_label(self):
        with self.assertRaises(ValueError):
            build_split([{'group_id': 'unknown', 'label': None}], 42)
