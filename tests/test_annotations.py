import unittest
from cashframe.annotations import validate_annotation
from cashframe.rules import resolve_rule, assign_priority


class AnnotationTests(unittest.TestCase):
    def test_confirmation_needs_human_and_fact(self):
        with self.assertRaises(ValueError):
            validate_annotation({'start_sec':1, 'end_sec':2, 'confirmed':True, 'origin':'automatic'}, 10)
        item = validate_annotation({'start_sec':1, 'end_sec':2, 'confirmed':True, 'origin':'human',
                                    'reviewer':'tester', 'observed_fact':'Fixture action', 'type':'fixture',
                                    'rule_id':None, 'rule_status':'unresolved'}, 10)
        self.assertTrue(item['confirmed'])

    def test_reversed_or_out_of_bounds_interval_rejected(self):
        for start, end in [(3,1),(-1,2),(1,11)]:
            with self.assertRaises(ValueError):
                validate_annotation({'start_sec':start,'end_sec':end,'confirmed':False},10)

    def test_unknown_and_unverified_rule_never_becomes_conclusion(self):
        self.assertIsNone(resolve_rule('invented', []))
        self.assertIsNone(resolve_rule('r', [{'id':'r','verified':False}]))

    def test_priority_does_not_follow_model_score(self):
        for score in [.1, .99]:
            priority, reason = assign_priority({'score':score,'confirmed':False}, {})
            self.assertEqual(priority,'unspecified')
            self.assertTrue(reason)

    def test_verified_rule_without_automatic_match_is_not_auto_selected(self):
        rule={'id':'r','verified':True,'applicability':'cash receipt','visual_cues':['test'], 'automatic_matching_enabled':False}
        self.assertIsNone(resolve_rule('r',[rule]))
