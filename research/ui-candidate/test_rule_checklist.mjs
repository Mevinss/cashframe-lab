import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {assessChecklist} from './rule-checklist.mjs';
const catalog=JSON.parse(readFileSync(new URL('./rule-catalog.json',import.meta.url),'utf8'));
const fixture=rule=>({rule_id:rule.id,origin:'human',human_confirmed:true,visibility:'clear',answers:Object.fromEntries([...rule.context,...rule.observations].map(q=>[q.id,'satisfied']))});
test('only three team references and no automatic legal confirmation',()=>{
 assert.equal(catalog.rules.length,3);
 for(const rule of catalog.rules){const result=assessChecklist(fixture(rule),catalog);assert.equal(result.status,'factual_checklist_supported');assert.equal(result.legal_conclusion,null);assert.equal(result.automatic_rule_confirmation,false);}
});
test('missing, invisible, automatic and unconfirmed facts cannot become supported',()=>{
 for(const rule of catalog.rules){
  for(const changes of[{human_confirmed:false},{origin:'automatic'},{visibility:'partial'},{answers:{}}])assert.notEqual(assessChecklist({...fixture(rule),...changes},catalog).status,'factual_checklist_supported');
  const input=fixture(rule);input.answers[rule.observations[0].id]='not_visible';assert.equal(assessChecklist(input,catalog).status,'insufficient_evidence');
 }
});
test('checklist is never posted or connected to legal confirmation controls',()=>{
 const code=readFileSync(new URL('./rule-checklist.mjs',import.meta.url),'utf8');
 assert.doesNotMatch(code,/saveAnnotation|\/bridge\/|method:\s*['"]POST|r-applicability|r-rule-status/);
 assert.match(code,/app-mode/);
});
