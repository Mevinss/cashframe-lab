"""Human factual checklist for three team-documented references; never a verdict."""
import argparse
import json
from pathlib import Path

CATALOG = Path(__file__).resolve().parents[1]/'ui-candidate'/'rule-catalog.json'
ANSWERS = {'satisfied','not_satisfied','unknown','not_visible'}
RULE_IDS = {'RK231-20','RK231-22','RK231-19-6'}


def evaluate(item, catalog=None):
    catalog=json.loads(CATALOG.read_text()) if catalog is None else catalog
    allowed={'rule_id','origin','human_confirmed','visibility','answers'}
    if not isinstance(item,dict) or set(item)!=allowed:raise ValueError('Invalid sanitized checklist structure')
    if len(catalog['rules'])!=3 or {rule['id'] for rule in catalog['rules']}!=RULE_IDS:raise ValueError('Unsupported rule registry')
    rules={rule['id']:rule for rule in catalog['rules']}
    if item['rule_id'] not in rules or item['origin'] not in ('human','automatic') or type(item['human_confirmed']) is not bool or item['visibility'] not in ('clear','partial','unknown','unusable'):raise ValueError('Invalid checklist attributes')
    rule=rules[item['rule_id']];questions=rule['context']+rule['observations'];expected={q['id'] for q in questions}
    answers=item['answers']
    if not isinstance(answers,dict) or set(answers)-expected or any(not isinstance(value,str) or value not in ANSWERS for value in answers.values()):raise ValueError('Invalid factual answers')
    values={key:answers.get(key,'unknown') for key in expected}
    if item['origin']!='human' or item['human_confirmed'] is not True:status='needs_human_confirmation'
    elif item['visibility']!='clear':status='insufficient_visibility'
    elif any(values[q['id']]=='not_satisfied' for q in rule['context']):status='context_not_established'
    elif any(values[q['id']] in ('unknown','not_visible') for q in questions):status='insufficient_evidence'
    elif any(values[q['id']]=='not_satisfied' for q in rule['observations']):status='factual_discrepancy_requires_review'
    else:status='factual_checklist_supported'
    return {'schema_version':1,'rule_id':rule['id'],'status':status,
            'supported_conditions':sum(v=='satisfied' for v in values.values()),
            'total_conditions':len(values),'unknown_or_invisible':sum(v in ('unknown','not_visible') for v in values.values()),
            'legal_conclusion':None,'automatic_rule_confirmation':False,
            'notice':catalog['notice'],'limits':rule['limits']}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('input',type=Path);args=parser.parse_args()
    try:result=evaluate(json.loads(args.input.read_text()))
    except (OSError,ValueError,KeyError,TypeError):
        print('Checklist rejected; no input values displayed.');return 2
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
