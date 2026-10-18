import {ApiError,validateMov} from './api.mjs';
const sleep=(ms,signal)=>new Promise((resolve,reject)=>{
  if(signal.aborted){reject(new ApiError(0,'cancel'));return;}
  const abort=()=>{clearTimeout(timer);reject(new ApiError(0,'cancel'));};
  const timer=setTimeout(()=>{signal.removeEventListener('abort',abort);resolve();},ms);signal.addEventListener('abort',abort,{once:true});
});
// Explicitly correlates review by type, timestamp and description; never by index.
export function correlateEvents(result,review){
  const rows=Array.isArray(review?.review?.events)?review.review.events.filter(row=>row&&typeof row==='object'):[];
  const count=target=>result.violations.filter(v=>v.type===target.type&&v.description===target.description&&v.timecode_sec===target.timecode_sec).length;
  return result.violations.map((v,index)=>{
    const candidates=rows.filter(row=>row.type===v.type&&row.timecode_sec===v.timecode_sec&&row.description===v.description);
    const meta=candidates.length===1&&count(v)===1?candidates[0]:null;
    const duration=review?.review?.duration_sec;
    const valid=meta&&Number.isFinite(duration)&&meta.end_sec<=duration&&meta.localization_status==='model_proposal'&&Number.isFinite(meta.start_sec)&&Number.isFinite(meta.end_sec)&&0<=meta.start_sec&&meta.start_sec<meta.end_sec&&meta.start_sec<=v.timecode_sec&&v.timecode_sec<=meta.end_sec;
    return {...v,id:`real-${index}`,meta,localized:Boolean(valid),unresolved:!valid};
  });
}
export class RealSession{
  constructor(api,{onChange=()=>{},delay=sleep,pollLimitMs=360000,intervalMs=1000}={}){
    this.api=api;this.onChange=onChange;this.delay=delay;this.pollLimitMs=pollLimitMs;this.intervalMs=intervalMs;
    this.mode='demo';this.generation=0;this.controller=null;this.busy=false;this.reset();
  }
  reset(){this.data={status:'idle',jobId:null,result:null,review:null,rules:[],annotations:[],error:null,optionalErrors:[],readiness:null};}
  emit(){this.onChange(this.data);}
  switchMode(mode){if(!['demo','real'].includes(mode))throw new ApiError(0,'schema');this.generation++;this.controller?.abort();this.api.clear();this.mode=mode;this.busy=false;this.reset();this.emit();}
  stop(){this.generation++;this.controller?.abort();this.busy=false;this.data.status=this.data.result?'done':'stopped';this.data.error=new ApiError(0,'cancel').message;this.emit();}
  async start(file,key,{explicit=false}={}){
    if(this.mode!=='real'||!explicit)throw new ApiError(0,'cancel');if(this.busy)throw new ApiError(409);
    validateMov(file);this.api.setKey(key);this.busy=true;this.reset();this.controller=new AbortController();const signal=this.controller.signal,gen=++this.generation;
    try{
      this.data.status='readiness';this.emit();const readiness=await this.api.checkReadiness({signal});if(gen!==this.generation)return;
      this.data.readiness=readiness.ready;if(!readiness.ready){this.data.status='not-ready';this.data.error=new ApiError(503).message;this.emit();return;}
      this.data.status='uploading';this.emit();const {job_id}=await this.api.analyzeVideo(file,key,{intent:'upload',signal});if(gen!==this.generation)return;this.data.jobId=job_id;
      await this.poll(gen,signal);
    }catch(error){if(gen===this.generation){this.data.status='error';this.data.error=error instanceof ApiError?error.message:new ApiError().message;this.emit();}}
    finally{if(gen===this.generation){this.busy=false;this.emit();}}
  }
  async resume(key){
    if(this.mode!=='real'||!this.data.jobId||this.busy)throw new ApiError(409);
    this.api.setKey(key);this.busy=true;this.controller=new AbortController();const gen=++this.generation;
    try{await this.poll(gen,this.controller.signal);}catch(error){if(gen===this.generation){this.data.status='error';this.data.error=error instanceof ApiError?error.message:new ApiError().message;this.emit();}}
    finally{if(gen===this.generation){this.busy=false;this.emit();}}
  }
  async poll(gen,signal){
    const deadline=Date.now()+this.pollLimitMs;
    while(Date.now()<deadline){
      let job;
      try{job=await this.api.getJob(this.data.jobId,{signal});}
      catch(error){if(gen!==this.generation)return;if(!(error instanceof ApiError)||![0,408,429,502,503,504].includes(error.status)||['cancel','schema'].includes(error.kind))throw error;
        this.data.status='processing';this.data.error='Временно нет связи или сервис занят. Повторяется только GET статуса.';this.emit();await this.delay(this.intervalMs,signal);continue;}
      if(gen!==this.generation)return;this.data.error=null;
      if(job.status==='failed'){this.data.status='failed';this.data.error='Backend завершил анализ с ошибкой. Отрицательный вердикт не установлен.';this.emit();return;}
      try{const review=await this.api.getReview(this.data.jobId,{signal});if(gen!==this.generation)return;this.data.review=review;}
      catch{if(gen!==this.generation)return;this.data.review=null;this.data.optionalErrors=['Метаданные review недоступны; локализация не подтверждена.'];}
      if(job.status==='done'){
        this.data.result=job;this.data.status='done';this.emit();
        // Optional reads can fail without discarding the validated completed result.
        const responses=await Promise.allSettled([this.api.getRules({signal}),this.api.getAnnotations(this.data.jobId,{signal})]);
        if(gen!==this.generation)return;
        if(responses[0].status==='fulfilled')this.data.rules=responses[0].value.rules;else this.data.optionalErrors.push('Реестр норм недоступен.');
        if(responses[1].status==='fulfilled')this.data.annotations=responses[1].value.annotations;else this.data.optionalErrors.push('Разметка недоступна.');this.emit();return;
      }
      this.data.status='processing';this.emit();await this.delay(this.intervalMs,signal);
    }
    throw new ApiError(408,'timeout');
  }
}
