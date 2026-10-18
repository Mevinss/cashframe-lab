import unittest

from cashframe.config import ROOT
from cashframe.data import read_json
from cashframe.rules import is_verified_rule


class ViolationCatalogTests(unittest.TestCase):
    def setUp(self):
        self.catalog = read_json(ROOT/'config/violation-types.json')

    def test_candidates_keep_the_verified_rule_bindings(self):
        expected = {
            'leave_before_service_end': ['RK231-22'],
            'cash_receipt_count_without_client_presence': ['RK231-20'],
            'prior_cash_outside_designated_storage': ['RK231-20'],
            'receipt_confirmation_not_provided': ['RK231-19-6'],
        }
        classes = self.catalog['classes']
        self.assertEqual(len(classes), 4)
        self.assertEqual({item['id']: item['candidate_rule_ids'] for item in classes}, expected)
        registry = {rule['id']: rule for rule in read_json(ROOT/'config/rules.json')['rules']}
        for item in classes:
            for rule_id in item['candidate_rule_ids']:
                with self.subTest(category=item['id'], rule=rule_id):
                    self.assertTrue(is_verified_rule(registry[rule_id]))

    def test_each_candidate_has_localized_conditions_and_limits(self):
        locales = {'ru', 'kk', 'en'}
        for item in self.catalog['classes']:
            with self.subTest(category=item['id']):
                self.assertEqual(set(item['labels']), locales)
                self.assertEqual(set(item['requirements']), locales)
                self.assertEqual(set(item['limits']), locales)
                for locale in locales:
                    self.assertIsInstance(item['labels'][locale], str)
                    self.assertTrue(item['labels'][locale].strip())
                    self.assertIsInstance(item['limits'][locale], str)
                    self.assertTrue(item['limits'][locale].strip())
                    conditions = item['requirements'][locale]
                    self.assertIsInstance(conditions, list)
                    self.assertGreaterEqual(len(conditions), 2)
                    self.assertTrue(all(isinstance(text, str) and text.strip() for text in conditions))

    def test_neutral_action_ids_are_never_reused_for_violation_candidates(self):
        neutral = read_json(ROOT/'config/action-types.json')
        self.assertTrue({item['id'] for item in neutral['classes']}.isdisjoint(
            {item['id'] for item in self.catalog['classes']}))

    def test_proposed_catalog_requires_human_confirmation_and_claims_no_classifier(self):
        self.assertEqual(self.catalog['status'], 'proposed_violation_categories')
        self.assertIs(self.catalog['human_confirmation_required'], True)
        self.assertIs(self.catalog['trained_classifier'], False)
        self.assertIs(self.catalog['official_violation_list'], False)
        self.assertTrue(self.catalog['notice'].strip())
        for item in self.catalog['classes']:
            with self.subTest(category=item['id']):
                self.assertIs(item['automatic_violation'], False)


if __name__ == '__main__':
    unittest.main()
