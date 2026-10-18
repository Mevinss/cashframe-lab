// Same-origin adapter. Credentials exist only in this closure; no persistence/logs.
const JOB = /^[a-f0-9]{32}$/;
const MAX_FILE = 100*1024*1024;
export class ApiError extends Error {
  constructor(status=0, kind='http') {
    const messages={401:'Неверный или отсутствующий API-ключ.',404:'Задание отсутствует или срок хранения истёк.',408:'Истекло время запроса.',409:'Ресурс ещё не готов или операция уже выполняется.',413:'Превышен допустимый размер.',415:'Неподдерживаемый формат видео.',422:'Проверьте поля, видео или разметку.',429:'Сервис занят. Повторите действие позже.',503:'Backend не готов: проверьте ключ в конфигурации сервера, FFmpeg и локальные артефакты.',504:'Истекло время ожидания backend.'};
    super(kind==='schema'?'Ответ backend не соответствует ожидаемому контракту.':kind==='cancel'?'Ожидание отменено. Серверное задание могло продолжиться.':kind==='timeout'?'Истекло время ожидания. POST автоматически не повторяется.':messages[status]||'Запрос не выполнен. Проверьте соединение с локальным backend.');
    this.name='ApiError';this.status=status;this.kind=kind;
  }
}
const object=x=>x!==null&&typeof x==='object'&&!Array.isArray(x);
const finite=x=>typeof x==='number'&&Number.isFinite(x)&&x>=0;
const exact=(x,keys)=>object(x)&&Object.keys(x).length===keys.length&&keys.every(k=>Object.hasOwn(x,k));
const requireSchema=ok=>{if(!ok)throw new ApiError(0,'schema');};
function jobPath(id){if(!JOB.test(id))throw new ApiError(0,'schema');return `/api/v1/jobs/${id}`;}
export function validateJob(job, id) {
  requireSchema(object(job)&&JOB.test(job.job_id)&&(!id||id===job.job_id)&&['processing','failed','done'].includes(job.status));
  if(job.status==='done'){
    requireSchema(exact(job,['job_id','status','verdict','violations'])&&['violation','no_violation'].includes(job.verdict)&&Array.isArray(job.violations));
    requireSchema(Boolean(job.violations.length)===(job.verdict==='violation'));
    for(const v of job.violations){
      requireSchema(exact(v,['type','timecode_sec','description','evidence_frames'])&&typeof v.type==='string'&&typeof v.description==='string'&&finite(v.timecode_sec)&&Array.isArray(v.evidence_frames)&&v.evidence_frames.length>=1&&v.evidence_frames.length<=3);
      for(const f of v.evidence_frames)requireSchema(exact(f,['timestamp_sec','image_base64'])&&finite(f.timestamp_sec)&&typeof f.image_base64==='string');
    }
  }else requireSchema(job.error===undefined||typeof job.error==='string');
  return job;
}
export function validateMov(file){
  if(!file||typeof file.name!=='string'||!/\.mov$/i.test(file.name)||!finite(file.size)||!file.size||file.size>MAX_FILE)throw new ApiError(422);
  return file;
}
export function decodeJpeg(encoded){
  requireSchema(typeof encoded==='string'&&encoded.length<=32*1024*1024&&encoded.length%4===0&&/^[A-Za-z0-9+/]*={0,2}$/.test(encoded));
  let raw;try{raw=atob(encoded);}catch{throw new ApiError(0,'schema');}
  requireSchema(raw.length>=4&&raw.charCodeAt(0)===255&&raw.charCodeAt(1)===216&&raw.charCodeAt(raw.length-2)===255&&raw.charCodeAt(raw.length-1)===217);
  return new Blob([Uint8Array.from(raw,c=>c.charCodeAt(0))],{type:'image/jpeg'});
}
export function annotationPayload(input,duration){
  const keys=['start_sec','end_sec','reviewer','observed_fact','type','visibility','rule_id','rule_status','rule_applicability_confirmed','confirmed','origin'];
  requireSchema(exact(input,keys)&&finite(duration)&&finite(input.start_sec)&&finite(input.end_sec)&&input.start_sec<=input.end_sec&&input.end_sec<=duration);
  requireSchema(input.origin==='human'&&typeof input.confirmed==='boolean'&&typeof input.rule_applicability_confirmed==='boolean'&&['clear','partial','unusable','unknown'].includes(input.visibility)&&['unresolved','proposed','confirmed'].includes(input.rule_status));
  for(const [field,limit]of[['reviewer',100],['observed_fact',3000],['type',160]])requireSchema(typeof input[field]==='string'&&input[field].length<=limit);
  requireSchema(input.rule_id===null||(typeof input.rule_id==='string'&&input.rule_id.length<=100));
  if(input.confirmed){
    requireSchema(['reviewer','observed_fact','type'].every(k=>input[k].trim()));
    // Conservative UX gate only; backend's is_specific_type remains authoritative.
    const type=input.type.normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]+/gu,' ').trim();
    const placeholders=new Set(['unknown','unresolved','unspecified','unconfirmed','unknown event','unconfirmed event','violation','no violation','positive','negative','normal','anomaly','true','false','0','1','неизвестно','неизвестный','неподтверждено','нарушение','без нарушения','тип не установлен','тип не определен','тип не подтвержден']);
    requireSchema(type!==''&&!placeholders.has(type.replaceAll('ё','е'))&&!/^(unknown |unconfirmed |неизвестный |неподтвержден)/.test(type)&&!/(не подтвержд|тип не установлен|тип не определ)/i.test(input.type));
    requireSchema(Boolean(input.rule_id)||input.rule_status==='unresolved');
  }
  if(input.rule_status==='confirmed')requireSchema(input.confirmed&&input.visibility==='clear'&&input.rule_applicability_confirmed&&Boolean(input.rule_id));
  return Object.fromEntries(keys.map(key=>[key,input[key]]));
}
async function boundedBody(response,limit,binary=false){
  const rawLength=response.headers.get('Content-Length');
  const length=rawLength===null?null:Number(rawLength);
  if(rawLength!==null&&(!/^\d+$/.test(rawLength)||!Number.isSafeInteger(length)))throw new ApiError(0,'schema');
  if(length!==null&&length>limit)throw new ApiError(413);
  const reader=response.body?.getReader();
  if(!reader){requireSchema(length===null||length===0);return binary?new Blob([],{type:response.headers.get('Content-Type')||''}):new Uint8Array();}
  const chunks=[];let total=0;
  try{for(;;){const {done,value}=await reader.read();if(done)break;total+=value.length;if(total>limit)throw new ApiError(413);chunks.push(value);}}
  catch(error){await reader.cancel().catch(()=>{});throw error;}
  finally{reader.releaseLock();}
  if(length!==null&&length!==total)throw new ApiError(0,'schema');
  if(binary)return new Blob(chunks,{type:response.headers.get('Content-Type')||'application/octet-stream'});
  const result=new Uint8Array(total);let offset=0;for(const chunk of chunks){result.set(chunk,offset);offset+=chunk.length;}return result;
}

export function createApi({fetchImpl=globalThis.fetch, timeoutMs=20000}={}){
  let key='',disposed=false;
  const active=new Set(),posts=new Set();
  function setKey(value){if(typeof value!=='string'||!value.trim()||value.length>4096||/[^\x20-\x7e]/.test(value))throw new ApiError(401);key=value.trim();disposed=false;}
  function clear(){disposed=true;key='';for(const controller of active)controller.abort();active.clear();}
  async function request(path,{method='GET',body,headers={},signal,timeout=timeoutMs,binary=false,limit=64*1024*1024,allow503=false}={}){
    if(disposed)throw new ApiError(0,'cancel');
    if(path!=='/health/ready'&&!key)throw new ApiError(401);
    const tag=method==='POST'?path:null;if(tag&&posts.has(tag))throw new ApiError(409);
    const controller=new AbortController();active.add(controller);if(tag)posts.add(tag);
    let timedOut=false;const timer=setTimeout(()=>{timedOut=true;controller.abort();},timeout);
    const abort=()=>controller.abort();signal?.addEventListener('abort',abort,{once:true});if(signal?.aborted)abort();
    try{
      const response=await fetchImpl(`/bridge${path}`,{method,body,signal:controller.signal,credentials:'same-origin',cache:'no-store',redirect:'error',referrerPolicy:'no-referrer',headers:{'X-Cashframe-Client':'ui-candidate-v1',...(path==='/health/ready'?{}:{'X-API-Key':key}),...headers}});
      if(allow503&&response.status===503)return {ready:false};
      if(!response.ok)throw new ApiError(response.status);
      const bytes=await boundedBody(response,limit,binary);
      if(binary){
        return {blob:bytes,
          reportSha256:response.headers.get('X-Report-SHA256'),passportSha256:response.headers.get('X-Passport-SHA256'),
          contentDisposition:response.headers.get('Content-Disposition')};
      }
      requireSchema((response.headers.get('Content-Type')||'').includes('application/json'));
      try{return JSON.parse(new TextDecoder().decode(bytes));}catch(error){if(error instanceof ApiError)throw error;throw new ApiError(0,'schema');}
    }catch(error){if(error instanceof ApiError)throw error;if(controller.signal.aborted)throw new ApiError(0,timedOut?'timeout':'cancel');throw new ApiError(0,'network');}
    finally{clearTimeout(timer);signal?.removeEventListener('abort',abort);active.delete(controller);controller.abort();if(tag)posts.delete(tag);}
  }
  const jsonGet=(path,options)=>request(path,options);
  return {
    setKey, clear,
    async checkReadiness(options={}){const data=await request('/health/ready',{...options,timeout:70000,allow503:true});requireSchema(object(data)&&typeof data.ready==='boolean');return data;},
    async analyzeVideo(file,apiKey,{intent,signal}={}){
      if(intent!=='upload')throw new ApiError(0,'cancel');validateMov(file);setKey(apiKey);
      const form=new FormData();form.append('file',file);
      const data=await request('/api/v1/analyze',{method:'POST',body:form,timeout:275000,signal});
      requireSchema(exact(data,['job_id'])&&JOB.test(data.job_id));return data;
    },
    async getJob(id,options){return validateJob(await jsonGet(jobPath(id),options),id);},
    async getReview(id,options){const data=await jsonGet(jobPath(id)+'/review',options);requireSchema(object(data)&&data.job_id===id&&['processing','done','failed'].includes(data.status)&&typeof data.stage==='string'&&(data.review===null||object(data.review)));return data;},
    async getRules(options){const data=await jsonGet('/api/v1/rules',options);requireSchema(object(data)&&Array.isArray(data.rules));return data;},
    async getAnnotations(id,options){const data=await jsonGet(jobPath(id)+'/annotations',options);requireSchema(object(data)&&Array.isArray(data.annotations)&&data.annotations.every(object));return data;},
    async saveAnnotation(id,input,{duration,intent,signal}={}){
      if(intent!=='save-annotation')throw new ApiError(0,'cancel');const body=annotationPayload(input,duration);
      const data=await request(jobPath(id)+'/annotations',{method:'POST',body:JSON.stringify(body),headers:{'Content-Type':'application/json'},signal});
      requireSchema(object(data)&&data.saved===true&&typeof data.confirmed==='boolean');return data;
    },
    async getVideoPreview(id,options){const data=await request(jobPath(id)+'/video',{...options,binary:true,limit:128*1024*1024,timeout:60000});requireSchema(data.blob.type.startsWith('video/mp4'));return data;},
    async downloadReport(id,options){const data=await request(jobPath(id)+'/report.pdf',{...options,binary:true,timeout:60000,limit:128*1024*1024});requireSchema(data.blob.type.startsWith('application/pdf'));return data;},
    async downloadPassport(id,options){const data=await request(jobPath(id)+'/passport',{...options,binary:true,timeout:60000,limit:128*1024*1024});requireSchema(data.blob.type.startsWith('application/json'));return data;}
  };
}
