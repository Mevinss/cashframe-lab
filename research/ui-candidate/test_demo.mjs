import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {DURATION, STAGES, SCENARIOS, createState, chooseScenario, chooseEvent, selectedEvent,
  eventsFor, seek, advanceStage, reviewKey, saveReview, validateFileMetadata, formatTime} from './demo.mjs';

test('all six scenarios distinguish missing decisions from binary verdicts',()=>{
  const original=createState();
  for(const name of Object.keys(SCENARIOS)){
    const state=chooseScenario(original,name);
    assert.equal(state.scenario,name);assert.equal(state.playing,false);
    assert.equal(eventsFor(name).length,name==='positive'?2:name==='unresolved'?1:0);
    if(['failed','empty','pending'].includes(name))assert.equal(SCENARIOS[name].verdict,null);
  }
  assert.equal(original.scenario,'positive');assert.throws(()=>chooseScenario(original,'anything'));
});
test('selection and synthetic seeking use real timestamps and bounded range',()=>{
  const state=chooseEvent(createState(),'signal-02');
  assert.equal(selectedEvent(state).timecode_sec,67.25);assert.equal(state.time,67.25);
  assert.equal(seek(state,-1).time,0);assert.equal(seek(state,200).time,DURATION);
  assert.throws(()=>seek(state,NaN));assert.throws(()=>chooseEvent(state,'absent'));
  assert.equal(formatTime(67.25),'01:07.25');assert.equal(formatTime(59.999),'01:00.00');
});
test('pending stages are manual and lead only to synthetic results',()=>{
  let state=chooseScenario(createState(),'pending');
  for(let i=0;i<STAGES.length-1;i++){assert.equal(state.stage,i);state=advanceStage(state);}
  assert.equal(state.scenario,'pending');state=advanceStage(state);assert.equal(state.scenario,'positive');
});
const form={start:22,end:28,reviewer:'Учебный проверяющий',observation:'Синтетическое наблюдение',visibility:'clear',confirmed:false};
test('draft and confirmation do not mutate model output or establish a rule',()=>{
  const state=createState(),events=eventsFor('positive');let next=saveReview(state,form);
  assert.equal(next.reviews[reviewKey(state)].confirmed,false);assert.deepEqual(state.reviews,{});
  next=saveReview(next,{...form,confirmed:true});const review=next.reviews[reviewKey(state)];
  assert.equal(review.confirmed,true);assert.equal(review.synthetic,true);assert.equal(review.normative_status,'unresolved');
  assert.deepEqual(eventsFor('positive'),events);
  assert.equal(next.reviews[reviewKey(chooseEvent(next,'signal-02'))],undefined);
});
test('review refuses invalid ranges and unclear confirmations',()=>{
  for(const changes of [{start:30,end:20},{start:NaN},{end:121},{reviewer:''},{observation:''},
    {confirmed:true,visibility:'unknown'},{confirmed:'yes'}])assert.throws(()=>saveReview(createState(),{...form,...changes}));
  assert.throws(()=>saveReview(chooseScenario(createState(),'negative'),form));
});
test('file metadata selection never accesses bytes or reveals original filenames',()=>{
  const file={name:'synthetic.mov',size:1024,arrayBuffer(){throw new Error('Must not read');}};
  assert.equal(validateFileMetadata(file).ok,true);assert.ok(!validateFileMetadata(file).message.includes(file.name));
  for(const file of [null,{name:'x.mp4',size:1},{name:'x.mov',size:0},{name:'x.MOV',size:101*1024*1024}])
    assert.equal(validateFileMetadata(file).ok,false);
});
test('unresolved context is not a localized zero-second event',()=>{
  const state=chooseScenario(createState(),'unresolved'),event=selectedEvent(state);
  assert.equal(event.localization_status,'unresolved');assert.equal(event.timecode_sec,0);
  assert.equal(SCENARIOS.unresolved.verdict,'violation');assert.ok(SCENARIOS.unresolved.copy.includes('контекст'));
});
test('browser code has no network, persistent storage, file reads or unsafe text injection',()=>{
  const code=readFileSync(new URL('./app.mjs',import.meta.url),'utf8')+readFileSync(new URL('./demo.mjs',import.meta.url),'utf8');
  for(const pattern of [/\bfetch\s*\(/,/\bXMLHttpRequest\b/,/\bWebSocket\b/,/\bsendBeacon\b/,
    /\bFileReader\b/,/\.arrayBuffer\(/,/\blocalStorage\b/,/\bsessionStorage\b/,/\.innerHTML\s*=/])assert.doesNotMatch(code,pattern);
});
test('DOM controller references existing HTML IDs',()=>{
  const html=readFileSync(new URL('./index.html',import.meta.url),'utf8');
  const code=readFileSync(new URL('./app.mjs',import.meta.url),'utf8');
  const ids=new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]));
  for(const match of code.matchAll(/\$\('([^']+)'\)/g))assert.ok(ids.has(match[1]),match[1]);
});
