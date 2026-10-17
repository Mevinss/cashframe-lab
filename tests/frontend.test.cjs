const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

function app(){
  const nodes=new Map();
  function node(){return {hidden:true,style:{},dataset:{},children:[],value:'',classList:{add(){},remove(){}},
    addEventListener(){},setAttribute(){},removeAttribute(){},replaceChildren(...items){this.children=items;},
    append(...items){this.children.push(...items);},prepend(item){this.children.unshift(item);},
    querySelectorAll(){return [];},querySelector(){return null;},focus(){},reset(){},pause(){},remove(){}};}
  const document={getElementById(id){if(!nodes.has(id))nodes.set(id,node());return nodes.get(id);},createElement:node,body:node()};
  const context=vm.createContext({document,Response,TextDecoder,AbortController,URL,FormData,console,
    setTimeout,clearTimeout,setInterval,clearInterval,fetch:async()=>new Response('{}')});
  vm.runInContext(fs.readFileSync('web/i18n.ru.js','utf8').replace('export const','const'),context);
  vm.runInContext(fs.readFileSync('web/app.js','utf8').replace(/^import[^\n]+\n/,''),context);
  return {context,nodes,run:code=>vm.runInContext(code,context)};
}
test('centisecond rounding carries into seconds and minutes',()=>{
  const a=app();assert.equal(a.run('preciseTime(1.9999)'),'0:02.00');assert.equal(a.run('preciseTime(59.9999)'),'1:00.00');
});
test('new negative result clears old selected interval',()=>{
  const a=app();a.run("state.review={review:{duration_sec:10}};state.result={violations:[]};$('selected-interval').hidden=false;renderTimeline()");
  assert.equal(a.nodes.get('selected-interval').hidden,true);assert.equal(a.nodes.get('selected-interval').style.width,'0%');
});
test('preview failure preserves completed verdict and JPEG evidence',async()=>{
  const a=app();await a.run(`state.review={review:{duration_sec:10,events:[]}};state.jobId='test';state.result={verdict:'violation',violations:[{type:'test',timecode_sec:1,description:'test',evidence_frames:[{timestamp_sec:1,image_base64:'fake-fixture'}]}]};request=async()=>{const e=new Error('media unavailable');e.status=404;throw e;};renderResult()`);
  assert.equal(a.nodes.get('result-view').hidden,false);assert.equal(a.nodes.get('event-detail').hidden,false);
  assert.equal(a.nodes.get('evidence').children.length,1);assert.equal(a.nodes.get('video-error').hidden,false);
});
test('GET retries transient errors, POST is not automatically duplicated',async()=>{
  const a=app();let calls=0;a.context.fetch=async()=>{calls++;if(calls===1)throw Error('offline');return new Response('{}');};
  await a.run("request('/test',{retries:1})");assert.equal(calls,2);
  calls=0;a.context.fetch=async()=>{calls++;throw Error('offline');};
  await assert.rejects(a.run("request('/test',{method:'POST'})"));assert.equal(calls,1);
});
test('hung requests are aborted',async()=>{
  const a=app();a.context.fetch=(_,options)=>new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(Error('aborted'))));
  await assert.rejects(a.run("request('/test',{timeoutMs:10,retries:0})"));
});
test('optional review failure cannot suppress jury result',async()=>{
  const a=app();await a.run(`state.jobId='test';request=async path=>{if(path.endsWith('/test'))return new Response(JSON.stringify({status:'done',verdict:'no_violation',violations:[]}));const e=new Error('optional unavailable');e.status=404;throw e;};pollJob(0)`);
  assert.equal(a.nodes.get('result-view').hidden,false);assert.equal(a.nodes.get('event-count').textContent,0);
});
