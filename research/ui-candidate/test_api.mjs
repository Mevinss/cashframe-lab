// All payloads and credentials are synthetic. No request reaches a real backend.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createApi,ApiError,validateJob,annotationPayload,decodeJpeg} from './api.mjs';
import {RealSession,correlateEvents} from './real.mjs';
import {readFileSync} from 'node:fs';
const id='a'.repeat(32),key='synthetic-only';
const fixture=JSON.parse(readFileSync(new URL('./synthetic_fixture.json',import.meta.url),'utf8'));
const frame=fixture.violations[0].evidence_frames[0];
const violation={type:'Synthetic action',description:'Synthetic fixture',timecode_sec:3,evidence_frames:[frame]};
const done={job_id:id,status:'done',verdict:'violation',violations:[violation]};
const review={job_id:id,status:'done',stage:'done',review:{duration_sec:10,events:[{...violation,start_sec:2,end_sec:4,localization_status:'model_proposal'}],limitations:['Synthetic limitation']}};
const response=(data,status=200,headers={})=>new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json',...headers}});
const file=()=>new File(['synthetic bytes, not video'],'synthetic.mov',{type:'video/quicktime'});
const annotation={start_sec:2,end_sec:4,reviewer:'Synthetic reviewer',observed_fact:'Synthetic observation',type:'Synthetic action',visibility:'clear',rule_id:null,rule_status:'unresolved',rule_applicability_confirmed:false,confirmed:false,origin:'human'};
function adapter(handler){const calls=[];const api=createApi({fetchImpl:async(path,options)=>{calls.push({path,method:options.method});return handler(path,options);}});api.setKey(key);return {api,calls};}

test('readiness 200 and 503 are normal; malformed readiness rejected',async()=>{
 for(const [body,status,expected]of[[{ready:true},200,true],[{ready:false},503,false]]){
  const {api}=adapter(()=>response(body,status));assert.equal((await api.checkReadiness()).ready,expected);
 }
 const {api}=adapter(()=>response({ready:'yes'}));await assert.rejects(api.checkReadiness(),e=>e.kind==='schema');
});
test('HTTP errors are safe, not upstream traceback text',async()=>{
 for(const status of[401,404,408,409,413,415,422,429,503,500]){
  const {api}=adapter(()=>response({detail:'SYNTHETIC_TRACE_MUST_NOT_ECHO'},status));
  await assert.rejects(api.getJob(id),e=>e.status===status&&!e.message.includes('SYNTHETIC_TRACE'));
 }
});
test('explicit upload has same-origin path, multipart file, returns real job id',async()=>{
 const {api,calls}=adapter((path,options)=>{assert.equal(path,'/bridge/api/v1/analyze');assert.equal(options.headers['X-API-Key'],key);assert.equal(options.credentials,'same-origin');assert.ok(options.body.get('file') instanceof File);return response({job_id:id},202);});
 assert.deepEqual(await api.analyzeVideo(file(),key,{intent:'upload'}),{job_id:id});assert.equal(calls.length,1);
 await assert.rejects(api.analyzeVideo(file(),key),e=>e.kind==='cancel');assert.equal(calls.length,1);
});
test('concurrent duplicate POST is rejected; network failure is never retried',async()=>{
 let finish;const {api,calls}=adapter(()=>new Promise(resolve=>finish=resolve));
 const first=api.analyzeVideo(file(),key,{intent:'upload'});await assert.rejects(api.analyzeVideo(file(),key,{intent:'upload'}),e=>e.status===409);
 finish(response({job_id:id},202));await first;assert.equal(calls.length,1);
 const failing=adapter(()=>{throw new Error('synthetic disconnect');});await assert.rejects(failing.api.analyzeVideo(file(),key,{intent:'upload'}));assert.equal(failing.calls.length,1);
});
test('completed schema: negative empty, positive multiple, malformed rejected',()=>{
 assert.equal(validateJob({...done,verdict:'no_violation',violations:[]},id).violations.length,0);
 assert.equal(validateJob({...done,violations:[violation,{...violation,timecode_sec:4}]},id).violations.length,2);
 for(const data of[{...done,violations:[]},{...done,extra:true},{...done,violations:[{...violation,timecode_sec:NaN}]},{job_id:id,status:'other'}])assert.throws(()=>validateJob(data,id));
});
test('JPEG base64 envelope decoded and malformed payload rejected',async()=>{
 const blob=decodeJpeg(frame.image_base64);assert.equal(blob.type,'image/jpeg');const bytes=new Uint8Array(await blob.arrayBuffer());assert.deepEqual([...bytes.slice(0,2)],[255,216]);assert.deepEqual([...bytes.slice(-2)],[255,217]);
 for(const invalid of['bad','data:image/jpeg;base64,xx','SGVsbG8='])assert.throws(()=>decodeJpeg(invalid));
});
test('review correspondence is identity-based and conservative for ambiguous/unresolved',()=>{
 assert.equal(correlateEvents(done,review)[0].localized,true);
 const unresolved=structuredClone(review);unresolved.review.events[0].localization_status='unresolved';assert.equal(correlateEvents(done,unresolved)[0].localized,false);
 assert.equal(correlateEvents(done,{...review,review:{...review.review,events:[]}})[0].localized,false);
 assert.equal(correlateEvents({...done,violations:[violation,violation]},review).every(x=>!x.localized),true);
 const reordered={...done,violations:[{...violation,timecode_sec:7},violation]};assert.equal(correlateEvents(reordered,review)[1].meta.start_sec,2);
});
test('manual annotation validates exact payload and rule applicability',()=>{
 assert.deepEqual(annotationPayload(annotation,10),annotation);
 for(const changes of[{extra:true},{start_sec:-1},{end_sec:11},{confirmed:true,type:'unknown'},{rule_status:'confirmed'},{origin:'automatic'}])assert.throws(()=>annotationPayload({...annotation,...changes},10));
 assert.equal(annotationPayload({...annotation,confirmed:true,rule_id:'synthetic-rule',rule_status:'confirmed',rule_applicability_confirmed:true},10).confirmed,true);
});
test('annotation POST occurs only on explicit intent, once, without unsupported fields',async()=>{
 const {api,calls}=adapter((path,options)=>{assert.equal(path,`/bridge/api/v1/jobs/${id}/annotations`);assert.deepEqual(JSON.parse(options.body),annotation);return response({saved:true,confirmed:false});});
 await assert.rejects(api.saveAnnotation(id,annotation,{duration:10}));assert.equal(calls.length,0);
 await api.saveAnnotation(id,annotation,{duration:10,intent:'save-annotation'});assert.equal(calls.length,1);
});
test('timeout and cancellation have sanitized errors',async()=>{
 const api=createApi({timeoutMs:5,fetchImpl:(_path,options)=>new Promise((_resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new Error('synthetic abort'))))});api.setKey(key);
 await assert.rejects(api.getJob(id),e=>e.kind==='timeout');
 const controller=new AbortController();const pending=api.getJob(id,{signal:controller.signal});controller.abort();await assert.rejects(pending,e=>e.kind==='cancel');
});
test('video, PDF and passport use authenticated binary requests and preserve integrity metadata',async()=>{
 for(const [method,type]of[['getVideoPreview','video/mp4'],['downloadReport','application/pdf'],['downloadPassport','application/json']]){
  const {api}=adapter((_p,o)=>{assert.equal(o.headers['X-API-Key'],key);return new Response('synthetic document',{headers:{'Content-Type':type,'X-Report-SHA256':'b'.repeat(64),'X-Passport-SHA256':'c'.repeat(64),'Content-Disposition':'attachment; filename="synthetic"'}});});
  const data=await api[method](id);assert.equal(data.blob.type,type);assert.equal(data.reportSha256,'b'.repeat(64));assert.ok(data.contentDisposition);
 }
});
function sessionFixture({ready=true,previewFails=false,jobError=null}={}){
 let polls=0;const {api,calls}=adapter(path=>{
  if(path.endsWith('/health/ready'))return response({ready},ready?200:503);
  if(path.endsWith('/analyze'))return response({job_id:id},202);
  if(path.endsWith('/review'))return response(review);
  if(path.endsWith('/rules'))return response({rules:[]});
  if(path.endsWith('/annotations'))return response({annotations:[]});
  if(path.endsWith('/video'))return response({},previewFails?409:200);
  if(jobError)return response({},jobError);
  return response(++polls===1?{job_id:id,status:'processing'}:done);
 });
 const session=new RealSession(api,{delay:async()=>{},pollLimitMs:100});return {session,api,calls};
}
test('Demo makes no requests; Real processes actual async responses; mode changes clear data',async()=>{
 const {session,calls}=sessionFixture();await assert.rejects(session.start(file(),key,{explicit:true}));assert.equal(calls.length,0);
 session.switchMode('real');await session.start(file(),key,{explicit:true});assert.equal(session.data.status,'done');assert.equal(session.data.result.verdict,'violation');
 session.switchMode('demo');assert.equal(session.data.result,null);assert.equal(session.data.jobId,null);
});
test('503 or malformed REAL responses never fall back to demo, and never upload when not ready',async()=>{
 const {session,calls}=sessionFixture({ready:false});session.switchMode('real');await session.start(file(),key,{explicit:true});assert.equal(session.data.status,'not-ready');assert.equal(session.data.result,null);assert.equal(calls.some(x=>x.method==='POST'),false);
 const bad=adapter(path=>path.endsWith('/health/ready')?response({ready:true}):response({unexpected:true}));const s=new RealSession(bad.api);s.switchMode('real');await s.start(file(),key,{explicit:true});assert.equal(s.data.status,'error');assert.equal(s.data.result,null);
});
test('preview 409 does not lose the independently completed result',async()=>{
 const {session,api}=sessionFixture({previewFails:true});session.switchMode('real');await session.start(file(),key,{explicit:true});const result=session.data.result;
 await assert.rejects(api.getVideoPreview(id),e=>e.status===409);assert.equal(session.data.result,result);assert.equal(session.data.status,'done');
});
test('missing/expired job becomes real error without synthetic result',async()=>{
 const {session}=sessionFixture({jobError:404});session.switchMode('real');await session.start(file(),key,{explicit:true});assert.equal(session.data.status,'error');assert.equal(session.data.result,null);
});
test('mode switch invalidates delayed real responses',async()=>{
 let resolve;const {api}=adapter(()=>new Promise(r=>resolve=r));const session=new RealSession(api);session.switchMode('real');const pending=session.start(file(),key,{explicit:true});session.switchMode('demo');resolve(response({ready:true}));await pending;assert.equal(session.mode,'demo');assert.equal(session.data.result,null);
});
test('credentials are absent from persistent storage, URLs and logs',()=>{
 const code=['api.mjs','real.mjs','real-view.mjs'].map(name=>readFileSync(new URL(name,import.meta.url),'utf8')).join('\n');
 for(const pattern of[/\blocalStorage\b/,/\bsessionStorage\b/,/\bindexedDB\b/,/console\./,/document\.cookie/,/https?:\/\//])assert.doesNotMatch(code,pattern);
});


test('real DOM controller references existing IDs and never injects server HTML',()=>{
 const html=readFileSync(new URL('./index.html',import.meta.url),'utf8');
 const code=readFileSync(new URL('./real-view.mjs',import.meta.url),'utf8');
 const ids=new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]));
 for(const match of code.matchAll(/\$\('([^']+)'\)/g))assert.ok(ids.has(match[1]),match[1]);
 assert.doesNotMatch(code,/\.innerHTML\s*=/);
});

test('truncated body, oversized preview and placeholder confirmations are rejected',async()=>{
 const truncated=adapter(()=>new Response('short',{headers:{'Content-Type':'application/json','Content-Length':'999'}}));
 await assert.rejects(truncated.api.getJob(id),e=>e.kind==='schema');
 const oversized=adapter(()=>new Response('synthetic',{headers:{'Content-Type':'video/mp4','Content-Length':String(129*1024*1024)}}));
 await assert.rejects(oversized.api.getVideoPreview(id),e=>e.status===413);
 for(const type of['unknown_event','unconfirmed event','нарушение','без нарушения','Тип не подтверждён'])assert.throws(()=>annotationPayload({...annotation,confirmed:true,type},10));
});

test('busy completion notification and unavailable final review do not retain stale metadata',async()=>{
 let reviewCalls=0,jobCalls=0;const observed=[];
 const {api}=adapter(path=>{
  if(path.endsWith('/health/ready'))return response({ready:true});
  if(path.endsWith('/analyze'))return response({job_id:id},202);
  if(path.endsWith('/review'))return ++reviewCalls===1?response({...review,status:'processing',stage:'evidence'}):response({},503);
  if(path.endsWith('/rules'))return response({rules:[]});
  if(path.endsWith('/annotations'))return response({annotations:[]});
  return response(++jobCalls===1?{job_id:id,status:'processing'}:done);
 });
 let session;session=new RealSession(api,{delay:async()=>{},onChange:()=>observed.push(session.busy)});
 session.switchMode('real');await session.start(file(),key,{explicit:true});assert.equal(session.data.status,'done');assert.equal(session.data.review,null);assert.equal(observed.at(-1),false);
});


test('event selection resets human observation and confirmation instead of carrying them across events',()=>{
 const code=readFileSync(new URL('./real-view.mjs',import.meta.url),'utf8');
 const selection=code.slice(code.indexOf('function renderSelected(){'),code.indexOf('function renderAnnotations(){'));
 assert.ok(selection.indexOf("$('r-annotation').reset()")>=0);
 assert.ok(selection.indexOf("$('r-annotation').reset()")<selection.indexOf('const event=events[selected]'));
});
