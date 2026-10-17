import {ru} from './i18n.ru.js';

const $=id=>document.getElementById(id);
const state={file:null,key:'',jobId:null,result:null,review:null,selected:null,blob:null,timer:null,generation:0,drafts:{}};
const time=value=>`${Math.floor(Math.max(0,value||0)/60)}:${String(Math.floor(Math.max(0,value||0)%60)).padStart(2,'0')}`;
const preciseTime=value=>`${time(value)}.${String(Math.round(((value||0)%1)*100)).padStart(2,'0')}`;
function message(text){$('notice').textContent=text;$('notice').hidden=!text;}
function screen(name){for(const view of ['upload','processing','result']){$(`${view}-view`).hidden=view!==name;const step=$(`step-${view}`);if(view===name)step.setAttribute('aria-current','step');else step.removeAttribute('aria-current');}}
function element(tag,className,text){const node=document.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=text;return node;}
function controls(){ $('analyze-button').disabled=!(state.file&&$('api-key').value.trim()); }
function setFile(file){state.file=file||null;$('file-summary').hidden=!file;if(file){$('file-name').textContent=file.name;$('file-size').textContent=`${(file.size/1024/1024).toFixed(1)} МБ`;}controls();}
async function request(path,options={}){
  let response;
  try{response=await fetch(path,{...options,headers:{'X-API-Key':state.key,...options.headers}});}catch{throw new Error(ru.requestFailed);}
  if(!response.ok){let detail;try{detail=(await response.json()).detail;}catch{}throw new Error(typeof detail==='string'?detail:`Ошибка сервиса (${response.status}). Повторите попытку.`);}
  return response;
}
$('api-key').addEventListener('input',controls);
$('video-file').addEventListener('change',e=>setFile(e.target.files[0]));
$('remove-file').addEventListener('click',()=>{$('video-file').value='';setFile(null);});
for(const event of ['dragover','dragenter'])$('drop-zone').addEventListener(event,e=>{e.preventDefault();$('drop-zone').classList.add('dragging');});
for(const event of ['dragleave','drop'])$('drop-zone').addEventListener(event,e=>{e.preventDefault();$('drop-zone').classList.remove('dragging');});
$('drop-zone').addEventListener('drop',e=>{$('video-file').files=e.dataTransfer.files;setFile(e.dataTransfer.files[0]);});
$('upload-form').addEventListener('submit',async e=>{
  e.preventDefault();message('');if(!state.file)return;
  if(!/\.(mov|mp4)$/i.test(state.file.name)){message('Выберите видео в формате MOV или MP4.');return;}
  if(state.file.size>100*1024*1024){message('Размер видео превышает 100 МБ. Выберите файл меньшего размера.');return;}
  state.key=$('api-key').value.trim();state.generation++;const generation=state.generation;
  screen('processing');$('processing-stage').textContent=ru.stages.accepted;$('processing-filename').textContent=state.file.name;
  const start=Date.now();state.timer=setInterval(()=>{$('elapsed').textContent=`Прошло ${time((Date.now()-start)/1000)}`;},1000);
  try{
    const form=new FormData();form.append('file',state.file);
    const response=await request('/api/v1/analyze',{method:'POST',body:form});
    state.jobId=(await response.json()).job_id;
    while(generation===state.generation){
      const job=await(await request(`/api/v1/jobs/${state.jobId}`)).json();
      const meta=await(await request(`/api/v1/jobs/${state.jobId}/review`)).json();
      $('processing-stage').textContent=ru.stages[meta.stage]||'Обработка видео';
      if(job.status==='failed')throw new Error(ru.errors[job.error]||ru.errors.analysis_failed);
      if(job.status==='done'){state.result=job;state.review=meta;await renderResult();break;}
      await new Promise(resolve=>setTimeout(resolve,1000));
    }
  }catch(error){message(error.message);screen('upload');}
  finally{clearInterval(state.timer);}
});

async function renderResult(){
  const {result,review}=state,detail=review.review||{};
  $('result-title').textContent=result.verdict==='violation'?ru.resultPositive:ru.resultNegative;
  $('result-subtitle').textContent=`${review.filename||state.file?.name||'Видео'} · ${time(detail.duration_sec)} · ${result.violations.length} событий`;
  $('event-count').textContent=result.violations.length;
  const list=$('limitations-list');list.replaceChildren();for(const text of detail.limitations||[])list.append(element('li','',text));
  const measured=detail.measurements||{};$('measurements').textContent=`Полное время: ${review.elapsed_sec??measured.analysis_wall_sec??'—'} сек · Визуальных выборок: ${measured.samples??'—'}`;
  $('seek').max=detail.duration_sec||1;state.selected=null;state.drafts={};renderEvents();renderTimeline();
  const media=await request(`/api/v1/jobs/${state.jobId}/video`);
  if(state.blob)URL.revokeObjectURL(state.blob);state.blob=URL.createObjectURL(await media.blob());$('player').src=state.blob;
  $('video-error').hidden=true;screen('result');
  if(result.violations.length)selectEvent(0);else $('event-detail').hidden=true;
  $('result-title').tabIndex=-1;$('result-title').focus({preventScroll:true});
  try{
    const registry=await(await request('/api/v1/rules')).json();const select=$('annotation-rule');
    select.replaceChildren(element('option','','Основание пока не установлено'));select.firstElementChild.value='';
    for(const rule of registry.rules||[]){const option=element('option','',`${rule.section} — ${rule.title}`);option.value=rule.id;select.append(option);}
  }catch{/* Rules remain explicitly unassigned when the registry is unavailable. */}
}
function reviewEvent(index){return state.review?.review?.events?.[index]||{};}
function renderEvents(){
  const list=$('event-list');list.replaceChildren();const events=state.result.violations;
  if(!events.length){list.append(element('p','empty-state',ru.emptyEvents));return;}
  const rank={high:0,medium:1,low:2,unspecified:3};const indices=events.map((_,i)=>i);
  indices.sort((a,b)=>$('event-sort').value==='priority'?(rank[reviewEvent(a).priority]??3)-(rank[reviewEvent(b).priority]??3)||events[a].timecode_sec-events[b].timecode_sec:events[a].timecode_sec-events[b].timecode_sec);
  for(const i of indices){
    const event=events[i],meta=reviewEvent(i),priority=meta.priority||'unspecified';const button=element('button','event-item');button.type='button';
    button.dataset.eventIndex=i;button.setAttribute('aria-pressed',String(state.selected===i));button.append(element('span','event-time tabular',time(event.timecode_sec)),element('strong','',event.type),element('span',`badge ${priority}`,ru.priority[priority]||ru.priority.unspecified));
    button.addEventListener('click',()=>selectEvent(i));list.append(button);
  }
}
function renderTimeline(){
  for(const node of $('timeline').querySelectorAll('.event-marker,.coverage-segment'))node.remove();const duration=state.review.review.duration_sec;
  const intervals=state.review.review.coverage?.length?state.review.review.coverage:[{start_sec:0,end_sec:duration,status:'unknown'}];
  for(const interval of intervals){const status=['sampled','incomplete','unknown'].includes(interval.status)?interval.status:'unknown';const segment=element('div',`coverage-segment coverage-${status}`);segment.style.left=`${100*interval.start_sec/duration}%`;segment.style.width=`${100*(interval.end_sec-interval.start_sec)/duration}%`;segment.setAttribute('role','img');segment.setAttribute('aria-label',`${time(interval.start_sec)}–${time(interval.end_sec)}: ${status==='sampled'?'выборочный анализ, видимость не оценена':status==='incomplete'?'неполный анализ':'состояние покрытия неизвестно'}`);$('timeline').prepend(segment);}
  state.result.violations.forEach((event,i)=>{const marker=element('button','event-marker');marker.type='button';marker.style.left=`${Math.min(99,100*event.timecode_sec/duration)}%`;marker.setAttribute('aria-label',`Событие ${i+1}, ${time(event.timecode_sec)}`);marker.title=`Событие ${i+1} · ${time(event.timecode_sec)}`;marker.addEventListener('click',()=>selectEvent(i));$('timeline').append(marker);});
}
function selectEvent(index){
  const keyboardSelection=document.activeElement?.matches('.event-item');
  if(state.selected!==null){state.drafts[state.selected]={start:$('annotation-start').value,end:$('annotation-end').value,reviewer:$('annotation-reviewer').value,type:$('annotation-type').value,fact:$('annotation-fact').value,rule:$('annotation-rule').value,confirm:$('annotation-confirm').checked};}
  state.selected=index;const event=state.result.violations[index],meta=reviewEvent(index),duration=state.review.review.duration_sec;
  $('player').currentTime=event.timecode_sec;$('event-detail').hidden=false;$('detail-title').textContent=event.type;
  $('detail-time').textContent=`${time(event.timecode_sec)} · фрагмент ${time(meta.start_sec??event.timecode_sec)}–${time(meta.end_sec??event.timecode_sec)}`;
  const priority=meta.priority||'unspecified';$('detail-priority').className=`badge ${priority}`;$('detail-priority').textContent=ru.priority[priority]||ru.priority.unspecified;
  $('detail-description').textContent=event.description;$('priority-reason').textContent=meta.priority_reason||'';
  $('rule-description').textContent='Не сопоставлено: конкретное действие и условия применения нормы требуют подтверждения.';
  const interval=$('selected-interval');interval.hidden=false;interval.style.left=`${100*(meta.start_sec??event.timecode_sec)/duration}%`;interval.style.width=`${Math.max(.5,100*((meta.end_sec??event.timecode_sec)-(meta.start_sec??event.timecode_sec))/duration)}%`;
  $('evidence').replaceChildren();event.evidence_frames.forEach((frame,i)=>{
    const figure=element('figure'),button=element('button','frame-button');button.type='button';
    const img=element('img');img.src=`data:image/jpeg;base64,${frame.image_base64}`;img.alt=`Оригинальный кадр, ${preciseTime(frame.timestamp_sec)}`;
    button.append(img);button.addEventListener('click',()=>{$('full-frame').src=img.src;$('frame-dialog').showModal();});
    const position=frame.timestamp_sec<event.timecode_sec-.05?0:frame.timestamp_sec>event.timecode_sec+.05?2:1;
    figure.append(button,element('figcaption','',`${ru.frames[position]} · ${preciseTime(frame.timestamp_sec)}`));$('evidence').append(figure);
  });
  const draft=state.drafts[index];
  $('annotation-start').value=draft?.start??(meta.start_sec??event.timecode_sec).toFixed(2);$('annotation-end').value=draft?.end??(meta.end_sec??event.timecode_sec).toFixed(2);
  $('annotation-start').max=duration;$('annotation-end').max=duration;$('annotation-type').value=draft?.type??'';$('annotation-fact').value=draft?.fact??'';$('annotation-reviewer').value=draft?.reviewer??'';$('annotation-rule').value=draft?.rule??'';$('annotation-confirm').checked=draft?.confirm??false;$('annotation-feedback').textContent='';renderEvents();
  if(keyboardSelection)$('event-list').querySelector(`[data-event-index="${index}"]`)?.focus({preventScroll:true});
}
$('event-sort').addEventListener('change',renderEvents);
$('seek').addEventListener('input',()=>{$('player').currentTime=Number($('seek').value);});
$('player').addEventListener('timeupdate',()=>{const t=$('player').currentTime,d=state.review?.review?.duration_sec||1;$('seek').value=t;$('player-time').textContent=`${time(t)} / ${time(d)}`;$('playhead').style.left=`${100*t/d}%`;});
$('player').addEventListener('error',()=>{$('video-error').hidden=false;});
$('new-check').addEventListener('click',()=>{state.generation++;$('player').pause();$('player').removeAttribute('src');if(state.blob){URL.revokeObjectURL(state.blob);state.blob=null;}message('');screen('upload');$('video-file').focus();});
$('close-frame').addEventListener('click',()=>$('frame-dialog').close());
$('annotation-form').addEventListener('submit',async e=>{
  e.preventDefault();const rule=$('annotation-rule').value;
  const annotation={start_sec:Number($('annotation-start').value),end_sec:Number($('annotation-end').value),reviewer:$('annotation-reviewer').value.trim(),type:$('annotation-type').value.trim(),observed_fact:$('annotation-fact').value.trim(),origin:'human',confirmed:$('annotation-confirm').checked,rule_id:rule||null,rule_status:rule?'proposed':'unresolved'};
  try{await request(`/api/v1/jobs/${state.jobId}/annotations`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(annotation)});$('annotation-feedback').textContent='Подтверждённая разметка сохранена локально. Ответ модели остаётся исходным.';}
  catch(error){$('annotation-feedback').textContent=error.message;}
});
