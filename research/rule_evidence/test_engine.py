import json
from pathlib import Path
import unittest
from research.rule_evidence.engine import evaluate,CATALOG,RULE_IDS


class ChecklistTests(unittest.TestCase):
    def fixture(self,rule):
        return {'rule_id':rule['id'],'origin':'human','human_confirmed':True,'visibility':'clear',
                'answers':{q['id']:'satisfied' for q in rule['context']+rule['observations']}}

    def test_only_documented_registry_and_never_a_verdict(self):
        catalog=json.loads(CATALOG.read_text());team=json.loads((CATALOG.parents[2]/'config/rules.json').read_text())
        self.assertEqual({r['id'] for r in catalog['rules']},RULE_IDS)
        self.assertEqual(RULE_IDS,{r['id'] for r in team['rules'] if r['verified']})
        for rule in catalog['rules']:
            result=evaluate(self.fixture(rule));self.assertEqual(result['status'],'factual_checklist_supported')
            self.assertIsNone(result['legal_conclusion']);self.assertFalse(result['automatic_rule_confirmation'])

    def test_unknown_invisible_missing_and_partial_never_succeed(self):
        for rule in json.loads(CATALOG.read_text())['rules']:
            for answer in ('unknown','not_visible'):
                item=self.fixture(rule);item['answers'][rule['observations'][0]['id']]=answer
                self.assertEqual(evaluate(item)['status'],'insufficient_evidence')
            item=self.fixture(rule);item['answers']={};self.assertEqual(evaluate(item)['status'],'insufficient_evidence')
            item=self.fixture(rule);item['visibility']='partial';self.assertEqual(evaluate(item)['status'],'insufficient_visibility')

    def test_automatic_unconfirmed_and_factual_discrepancies(self):
        rule=json.loads(CATALOG.read_text())['rules'][0]
        for field,value in [('origin','automatic'),('human_confirmed',False)]:
            item=self.fixture(rule);item[field]=value;self.assertEqual(evaluate(item)['status'],'needs_human_confirmation')
        item=self.fixture(rule);item['answers'][rule['context'][0]['id']]='not_satisfied';self.assertEqual(evaluate(item)['status'],'context_not_established')
        item=self.fixture(rule);item['answers'][rule['observations'][0]['id']]='not_satisfied';self.assertEqual(evaluate(item)['status'],'factual_discrepancy_requires_review')
        self.assertIsNone(evaluate(item)['legal_conclusion'])

    def test_bad_rule_and_extra_private_fields_rejected(self):
        rule=json.loads(CATALOG.read_text())['rules'][0]
        for changes in ({'rule_id':'invented'},{'reviewer':'Synthetic private marker'},{'human_confirmed':1},{'answers':{'invented':'satisfied'}}):
            with self.assertRaises(ValueError):evaluate({**self.fixture(rule),**changes})

class BrowserParityTests(unittest.TestCase):
    def test_python_and_browser_assess_same_synthetic_facts(self):
        import subprocess
        catalog=json.loads(CATALOG.read_text());items=[]
        for rule in catalog['rules']:
            base=ChecklistTests().fixture(rule)
            items.extend([{**base,'human_confirmed':False},{**base,'origin':'automatic'},{**base,'visibility':'partial'},base])
            for question in rule['context']+rule['observations']:
                for answer in ('not_satisfied','unknown','not_visible'):
                    items.append({**base,'answers':{**base['answers'],question['id']:answer}})
        module=(CATALOG.parent/'rule-checklist.mjs').as_uri()
        script=('import {assessChecklist} from '+json.dumps(module)+'; import fs from "node:fs"; '
                'const input=JSON.parse(fs.readFileSync(0,"utf8")); process.stdout.write(JSON.stringify(input.items.map(item=>assessChecklist(item,input.catalog))));')
        result=subprocess.run(['node','--input-type=module','-e',script],input=json.dumps({'items':items,'catalog':catalog}),capture_output=True,text=True,check=True,timeout=10)
        self.assertEqual(json.loads(result.stdout),[evaluate(item,catalog) for item in items])


if __name__=='__main__':unittest.main()
