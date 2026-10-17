import unittest
from cashframe.event_labels import is_specific_type


class EventLabelTests(unittest.TestCase):
    def test_placeholders_are_rejected_across_normalized_spellings(self):
        for value in (None,'','unknown','unknown_event','unconfirmed_event','unconfirmed event',
                      'Тип не установлен','тип_не_подтвержден','Визуальный сигнал — тип не подтверждён','нарушение'):
            with self.subTest(value=value):self.assertFalse(is_specific_type(value))
        self.assertTrue(is_specific_type('Синтетический тест'))
