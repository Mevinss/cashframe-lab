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
        rule={'id':'r','verified':True,'applicability':'cash receipt','visual_cues':['test'],
              'title':'Receipt','document':'NB RK cash rules','url':'https://adilet.zan.kz/rus/docs/test',
              'automatic_matching_enabled':False}
        self.assertIsNone(resolve_rule('r',[rule]))

    def test_automatic_rule_requires_source_name_and_applicability(self):
        rule={'id':'r','verified':True,'automatic_matching_enabled':True,
              'title':'Receipt in client presence','document':'NB RK cash rules',
              'url':'https://adilet.zan.kz/rus/docs/test',
              'applicability':'Confirmed cash receipt','visual_cues':['Client presence is visible']}
        self.assertEqual(resolve_rule('r',[rule]),rule)
        for field in ('title','document','url','applicability','visual_cues'):
            incomplete=dict(rule);incomplete.pop(field)
            with self.subTest(field=field):self.assertIsNone(resolve_rule('r',[incomplete]))
        for override in ({'verified':'false'},{'automatic_matching_enabled':'false'},
                         {'title':'  '},{'url':'javascript:alert(1)'},{'url':'https://'},
                         {'visual_cues':['  ']},{'visual_cues':'Client absent'}):
            with self.subTest(override=override):self.assertIsNone(resolve_rule('r',[dict(rule,**override)]))

    def test_null_fields_cannot_confirm_an_annotation(self):
        item={'start_sec':0,'end_sec':1,'confirmed':True,'origin':'human','reviewer':None,'observed_fact':None,'type':None,'rule_status':'unresolved'}
        with self.assertRaises(ValueError):validate_annotation(item,10)
        item.update(reviewer='test',observed_fact='fact',type='test',confirmed='false')
        with self.assertRaises(ValueError):validate_annotation(item,10)

    def test_rule_confirmation_needs_clear_visibility_and_applicability(self):
        item={'start_sec':1,'end_sec':2,'confirmed':True,'origin':'human','reviewer':'test',
              'observed_fact':'Synthetic action','type':'fixture','rule_id':'r','rule_status':'confirmed'}
        with self.assertRaises(ValueError):validate_annotation(item,10)
        item.update(visibility='clear',rule_applicability_confirmed=True)
        self.assertEqual(validate_annotation(item,10)['rule_status'],'confirmed')
        item['visibility']='partial'
        with self.assertRaises(ValueError):validate_annotation(item,10)

    def test_invalid_visibility_and_rule_status_rejected(self):
        for extra in ({'visibility':'invented'},{'rule_status':'approved-by-bank'}, {'rule_applicability_confirmed':'true'}):
            with self.assertRaises(ValueError):validate_annotation({'start_sec':0,'end_sec':1,**extra},10)

    def test_placeholder_type_cannot_be_confirmed(self):
        item={'start_sec':0,'end_sec':1,'confirmed':True,'origin':'human','reviewer':'test','observed_fact':'fact','type':'unknown','rule_status':'unresolved'}
        for label in ('unknown','unconfirmed_event','unconfirmed event','Тип не установлен','Визуальный сигнал — тип не подтверждён',
                      'Действие не определено','Әрекет анықталмады','Action undetermined'):
            item['type']=label
            with self.subTest(label=label),self.assertRaises(ValueError):validate_annotation(item,10)
