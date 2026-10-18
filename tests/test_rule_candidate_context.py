"""A catalog reference must never become a confirmed event/legal observation."""
import unittest

from cashframe.compliance import event_compliance


class RuleCandidateContextTests(unittest.TestCase):
    def setUp(self):
        self.rule = {'id': 'RK231-22', 'verified': True, 'automatic_matching_enabled': False,
                     'url': 'https://adilet.zan.kz/rus/docs/V1900019680',
                     'title': 'Workstation during customer service', 'document': 'NB RK 231',
                     'section': '22', 'applicability': 'Cash receipt service remains in progress',
                     'visual_cues': ['Actual workstation departure'], 'limits': 'Leaving the frame is insufficient',
                     'verified_on': '2026-10-18'}
        self.catalog = {'classes': [{'id': 'leave_before_service_end',
                                    'labels': {'ru': 'Уход до окончания обслуживания', 'en': 'Leaving before service completion'},
                                    'candidate_rule_ids': ['RK231-22'],
                                    'requirements': {'ru': ['Обслуживание не завершено.', 'Видны границы рабочего места.']},
                                    'limits': {'ru': 'Выход из кадра недостаточен.'}}]}
        self.event = {'id': 'one', 'start_sec': 2, 'end_sec': 4, 'timecode_sec': 3,
                      'type': 'Уход до окончания обслуживания', 'localization_status': 'model_proposal',
                      'type_prediction': {'type': 'Уход до окончания обслуживания', 'origin': 'automatic',
                                          'confirmed': False, 'model_version': 'synthetic-validated-head'}}
        self.job = {'source_sha256': 'a'*64, 'duration_sec': 10, 'review': {'events': [self.event]}}

    def compliance(self, annotations=(), rules=None):
        return event_compliance(self.job, [self.rule] if rules is None else rules, annotations,
                                violation_catalog=self.catalog)[0]

    def test_predicted_canonical_category_exposes_reference_with_unmet_conditions_only(self):
        row = self.compliance()
        self.assertEqual(row['status'], 'unresolved')
        self.assertIsNone(row['rule'])
        self.assertIsNone(row['observed_fact'])
        candidate, = row['candidate_rules']
        self.assertEqual(candidate['rule']['id'], 'RK231-22')
        self.assertEqual(candidate['status'], 'candidate')
        self.assertEqual(candidate['type_labels']['en'], 'Leaving before service completion')
        self.assertEqual(candidate['applicability_status'], 'not_confirmed')
        self.assertEqual(candidate['requirements']['ru'], ['Обслуживание не завершено.', 'Видны границы рабочего места.'])

    def test_generic_binary_signal_and_unlocalized_prediction_have_no_reference(self):
        for override in ({'type': 'Визуальный сигнал — тип не подтверждён', 'type_prediction': None},
                         {'localization_status': 'unresolved'},
                         {'type_prediction': {'type': 'Leave workstation maybe', 'origin': 'automatic', 'confirmed': False}}):
            with self.subTest(override=override):
                self.job['review']['events'] = [dict(self.event, **override)]
                self.assertEqual(self.compliance()['candidate_rules'], [])

    def test_label_alone_does_not_imply_that_model_observed_action(self):
        self.event.pop('type_prediction')
        self.assertEqual(self.compliance()['candidate_rules'], [])

    def test_unknown_unverified_obsolete_or_foreign_source_does_not_become_reference(self):
        for changed in ({'verified': False}, {'obsolete': True}, {'repealed': True},
                        {'status': 'repealed'}, {'url': 'https://example.org/source'}):
            with self.subTest(changed=changed):
                self.assertEqual(self.compliance(rules=[dict(self.rule, **changed)])['candidate_rules'], [])
        self.catalog['classes'][0]['candidate_rule_ids'] = ['unknown']
        self.assertEqual(self.compliance()['candidate_rules'], [])

    def test_unique_human_action_can_offer_reference_without_confirming_rule(self):
        self.event.pop('type_prediction')
        annotation = {'source_sha256': 'a'*64, 'start_sec': 2, 'end_sec': 4,
                      'origin': 'human', 'confirmed': True, 'type': 'leave_before_service_end',
                      'reviewer': 'Fixture', 'observed_fact': 'Synthetic visible departure',
                      'visibility': 'clear', 'rule_status': 'unresolved'}
        row = self.compliance([annotation])
        self.assertEqual(row['status'], 'unresolved')
        self.assertIsNone(row['rule'])
        self.assertEqual(row['candidate_rules'][0]['origin'], 'human')
        self.assertEqual(row['observed_fact'], 'Synthetic visible departure')

    def test_explicit_human_rule_confirmation_is_preserved_without_duplicate_candidate(self):
        annotation = {'source_sha256': 'a'*64, 'start_sec': 2, 'end_sec': 4,
                      'origin': 'human', 'confirmed': True, 'type': 'Уход до окончания обслуживания',
                      'reviewer': 'Fixture', 'observed_fact': 'Synthetic visible departure',
                      'visibility': 'clear', 'rule_status': 'confirmed',
                      'rule_id': 'RK231-22', 'rule_applicability_confirmed': True}
        row = self.compliance([annotation])
        self.assertEqual(row['status'], 'confirmed')
        self.assertEqual(row['rule']['id'], 'RK231-22')
        self.assertEqual(row['candidate_rules'], [])

    def test_ambiguous_human_observation_does_not_attach_reference_to_two_events(self):
        self.event.pop('type_prediction')
        self.job['review']['events'].append(dict(self.event, id='two'))
        annotation = {'source_sha256': 'a'*64, 'start_sec': 2, 'end_sec': 4,
                      'origin': 'human', 'confirmed': True, 'type': 'leave_before_service_end',
                      'reviewer': 'Fixture', 'observed_fact': 'Synthetic visible departure',
                      'visibility': 'clear', 'rule_status': 'unresolved'}
        rows = event_compliance(self.job, [self.rule], [annotation], violation_catalog=self.catalog)
        self.assertTrue(all(row['candidate_rules'] == [] for row in rows))

    def test_neutral_action_context_does_not_relabel_action_as_violation(self):
        self.event['type'] = 'Counting cash'
        self.event['type_prediction']['type'] = 'Counting cash'
        action_catalog = {'classes': [{'id': 'count_cash', 'labels': {'en': 'Counting cash'},
                                      'candidate_rule_ids': ['RK231-22'],
                                      'requirements': 'Operation and client visibility require confirmation',
                                      'limits': 'Counting is a normal action.'}]}
        row = event_compliance(self.job, [self.rule], [], action_catalog=action_catalog)[0]
        self.assertEqual(row['type'], 'Counting cash')
        self.assertEqual(row['status'], 'unresolved')
        self.assertEqual(row['candidate_rules'][0]['category_kind'], 'observed_action')


if __name__ == '__main__':
    unittest.main()
