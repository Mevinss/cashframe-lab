import {ru,tr,language,locale,hasTranslation,initPreferences} from './i18n.js';
import {initHero,initHeroCarousel} from './hero.js';

const $=id=>document.getElementById(id);
const state={file:null,key:'',authorized:false,jobId:null,result:null,review:null,selected:null,blob:null,timer:null,generation:0,drafts:{},overview:null,compliance:[],currentView:'overview',progress:null,startedAt:null};
let heroCarousel=null;
let actionTypes=[];
const time=value=>`${Math.floor(Math.max(0,value||0)/60)}:${String(Math.floor(Math.max(0,value||0)%60)).padStart(2,'0')}`;
const preciseTime=value=>{const ticks=Math.round(Math.max(0,Number(value)||0)*100);return `${time(ticks/100)}.${String(ticks%100).padStart(2,'0')}`;};
const fragmentWord=count=>language()==='kk'?tr('фрагментов'):language()==='en'?(count===1?tr('фрагмент'):tr('фрагментов')):count%10===1&&count%100!==11?tr('фрагмент'):count%10>=2&&count%10<=4&&(count%100<12||count%100>14)?tr('фрагмента'):tr('фрагментов');
function localDate(value){if(!/^\d{4}-\d{2}-\d{2}$/.test(value||''))return value;return new Date(value+'T12:00:00').toLocaleDateString(locale());}
function syncPlayerTime(){const t=$('player').currentTime||0,d=state.review?.review?.duration_sec||1;$('seek').value=t;$('player-time').textContent=`${time(t)} / ${time(d)}`;$('playhead').style.left=`${100*t/d}%`;}
function startElapsed(){clearInterval(state.timer);if(!state.startedAt)return;const update=()=>{$('elapsed').textContent=tr`Прошло ${time((Date.now()-state.startedAt)/1000)}`;};update();state.timer=setInterval(update,1000);}

function message(text){$('notice').textContent=text;$('notice').hidden=!text;}
function screen(name){
  state.currentView=name;
  heroCarousel?.block('view',name!=='overview');
  for(const view of ['overview','history','rules','guide','system','upload','processing','result'])$(`${view}-view`).hidden=view!==name;
  $('workflow').hidden=!['upload','processing','result'].includes(name);
  for(const view of ['upload','processing','result']){const step=$(`step-${view}`);if(view===name)step.setAttribute('aria-current','step');else step.removeAttribute('aria-current');}
  for(const button of document.querySelectorAll?.('.side-nav button')||[]){if(button.dataset.view===name)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');}
}
function element(tag,className,text){const node=document.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=text;return node;}
function controls(){ $('analyze-button').disabled=!(state.file&&state.authorized); }
function setFile(file){state.file=file||null;$('file-summary').hidden=!file;if(file){$('file-name').textContent=file.name;$('file-size').textContent=tr`${(file.size/1024/1024).toFixed(1)} МБ`;}controls();}
async function request(path,options={}){
  const {timeoutMs=15000,retries=options.method&&options.method!=='GET'?0:2,...fetchOptions}=options;
  for(let attempt=0;;attempt++){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),timeoutMs);
    try{
      const response=await fetch(path,{...fetchOptions,signal:controller.signal,headers:{'X-API-Key':state.key,...options.headers}});
      const body=await response.arrayBuffer();
      if(!response.ok){let detail;try{detail=JSON.parse(new TextDecoder().decode(body)).detail;}catch{}const error=new Error(typeof detail==='string'&&hasTranslation(detail)?tr(detail):response.status===401?tr('Ключ не принят'):response.status===413?tr('Размер видео превышает 100 МБ. Выберите файл меньшего размера.'):tr`Ошибка сервиса (${response.status}). Повторите попытку.`);error.status=response.status;throw error;}
      return new Response(body,{status:response.status,headers:response.headers});
    }catch(error){
      if(attempt>=retries||(error.status&&error.status<500&&error.status!==429)){if(!error.status)error.message=ru.requestFailed;throw error;}
    }finally{clearTimeout(timer);}
    await new Promise(resolve=>setTimeout(resolve,500*2**attempt));
  }
}
const sidebarToggle=$('sidebar-toggle');
let sidebarOpen=true;
sidebarToggle.addEventListener('click',()=>{
  sidebarOpen=!sidebarOpen;
  document.body.classList[sidebarOpen?'remove':'add']('sidebar-closed');
  sidebarToggle.setAttribute('aria-expanded',String(sidebarOpen));
  const label=sidebarOpen?tr('Свернуть боковую панель'):tr('Открыть боковую панель');
  sidebarToggle.setAttribute('aria-label',label);
  sidebarToggle.title=label;
});
function currentKey(){return state.authorized?state.key:'';}
function verdictLabel(verdict){return verdict==='violation'?ru.resultPositive:verdict==='no_violation'?ru.resultNegative:tr('Вердикт недоступен');}
function jobLabel(job){const outcome=job.status==='done'?verdictLabel(job.verdict):job.status==='processing'?tr('В обработке'):tr('Ошибка обработки');return `${job.filename||tr('Видео')} · ${new Date(job.created_at*1000).toLocaleString(locale())} · ${outcome}`;}
function displayEventTitle(event,meta){return meta.localization_status==='unresolved'?tr('Момент события не установлен'):event.type.startsWith('Визуальный сигнал')?tr('Тип действия не определён'):event.type;}
function renderJobRows(target,jobs){const node=$(target);node.replaceChildren();if(!jobs.length){node.append(element('p','empty-state',tr('Проверок за срок хранения пока нет.')));return;}
  for(const job of jobs){const row=element('div','recent-row'),description=element('strong','',jobLabel(job)),note=element('span','',job.elapsed_sec==null?tr('Время обработки пока неизвестно'):tr`Полное время: ${job.elapsed_sec} сек`);row.append(description,note);
    if(job.status==='done'){const button=element('button','text-button',tr('Открыть результат'));button.type='button';button.addEventListener('click',()=>openSavedJob(job.job_id));row.append(button);}
    node.append(row);}}
function renderHistory(){if(!state.overview)return;const filter=$('history-filter').value;renderJobRows('history-list',state.overview.jobs.filter(job=>!filter||filter==='all'||job.status===filter));}
function renderLockedMetrics(){const card=element('div','metrics-locked');const copy=element('div');copy.append(element('strong','',tr('Подключитесь, чтобы увидеть показатели')),element('p','',tr('Введите ключ доступа в верхней панели.')));card.append(copy);$('overview-metrics').replaceChildren(card);}
function renderOverview(data){state.overview=data;const metrics=$('overview-metrics');metrics.replaceChildren();
  for(const [value,label] of [[data.available,tr('Доступные проверки')],[data.processing,tr('В обработке')],[data.done_with_signals,tr('С фрагментами для просмотра')],[data.failed,tr('Ошибки обработки')],[data.average_processing_sec==null?'—':tr`${data.average_processing_sec} с`,tr('Среднее измеренное время')]]){const card=element('div','metric');card.append(element('strong','',String(value)),element('span','',label));metrics.append(card);}
  renderJobRows('overview-recent',data.recent.slice(0,3));renderHistory();$('overview-period').textContent=tr`За последние ${data.retention_hours} ч · задания удаляются по сроку хранения.`;}
async function loadOverview(){if(!currentKey()){message(tr('Введите ключ в верхней панели и нажмите «Подключить».'));return;}try{message('');renderOverview(await(await request('/api/v1/overview')).json());}catch(error){if(error.status===401){state.authorized=false;state.key='';$('access-status').textContent=tr('Ключ не принят');controls();}message(error.message);}}
async function loadRules(){const node=$('rules-list');node.replaceChildren();if(!currentKey()){node.append(element('p','plain-panel',tr('Введите ключ в верхней панели.')));return;}
  try{const data=await(await request('/api/v1/rules')).json();for(const rule of data.rules||[]){const card=element('article','rule-card');card.append(element('h2','',rule.title),element('p','',`${rule.document} · ${rule.section}`));
      const link=element('a','',tr`Источник · проверено ${rule.verified_on?localDate(rule.verified_on):tr('не проверено')}`);link.href=rule.url;link.target='_blank';link.rel='noopener noreferrer';card.append(link,element('p','source-language',tr('Нормативные сведения приведены на языке источника.')));
      const details=element('dl');for(const [name,value] of [[tr('Визуально проверяемые условия'),(rule.visual_cues||[]).join('; ')],[tr('Применимость'),rule.applicability],[tr('Ограничения и исключения'),rule.limits],[tr('Статус'),rule.verified?tr`Источник проверен; автоматическое сопоставление ${rule.automatic_matching_enabled?tr('включено'):tr('выключено')}`:tr('Источник не проверен')]]){details.append(element('dt','',name),element('dd','',value||tr('Не указано')));}card.append(details);node.append(card);}
  }catch(error){node.append(element('p','plain-panel',error.message));}}
async function loadSystem(){if(!currentKey()){$('system-details').textContent=tr('Введите ключ в верхней панели.');return;}$('system-details').textContent=tr('Проверка локальной модели…');
  try{const data=await(await request('/api/v1/system',{timeoutMs:75000,retries:0})).json();$('system-details').textContent=tr`Модель ${data.model_profile}: ${data.ready?tr('технически готова'):tr('не готова')}. Детектор денег: не настроен. Трекинг: не настроен. Хранение заданий: ${data.retention_hours} ч.`;}
  catch(error){$('system-details').textContent=error.message;}}
async function openSavedJob(jobId){try{state.generation++;state.jobId=jobId;state.result=await(await request(`/api/v1/jobs/${jobId}`)).json();try{state.review=await(await request(`/api/v1/jobs/${jobId}/review`)).json();}catch{state.review={review:{duration_sec:0,events:[]}};}if(state.result.status!=='done')throw new Error(tr('Результат ещё не готов.'));await renderResult();}catch(error){message(error.message);}}
function navigate(name){screen(name);message('');if((name==='overview'||name==='history')&&state.authorized)void loadOverview();if(name==='rules')void loadRules();if(name==='system')void loadSystem();}
for(const button of document.querySelectorAll?.('.side-nav button')||[])button.addEventListener('click',()=>navigate(button.dataset.view));
for(const button of document.querySelectorAll?.('[data-open-upload]')||[])button.addEventListener('click',()=>navigate('upload'));
$('load-overview').addEventListener('click',loadOverview);$('refresh-system').addEventListener('click',loadSystem);
$('history-filter').addEventListener('change',renderHistory);
$('api-key').addEventListener('input',()=>{if($('api-key').value.trim()!==state.key){state.key='';state.authorized=false;state.overview=null;renderLockedMetrics();$('overview-recent').textContent=tr('Нажмите «Подключить», чтобы загрузить данные.');$('history-list').textContent=tr('Нажмите «Подключить», чтобы загрузить данные.');$('access-status').textContent=tr('Не подключено');controls();}});
$('access-form').addEventListener('submit',async event=>{event.preventDefault();const candidate=$('api-key').value.trim();if(!candidate){message(tr('Введите ключ доступа.'));return;}state.key=candidate;state.authorized=false;$('access-status').textContent=tr('Проверяем ключ…');
  try{const data=await(await request('/api/v1/overview',{retries:0})).json();state.authorized=true;$('access-status').textContent=tr('Подключено');message('');renderOverview(data);controls();}
  catch(error){state.key='';state.authorized=false;$('access-status').textContent=tr('Ключ не принят');message(error.message);controls();}});
async function pollJob(generation){
  const deadline=Date.now()+360000;
  $('resume-poll').hidden=true;
  while(generation===state.generation){
    if(Date.now()>deadline)throw new Error(tr('Ожидание прервано. Можно повторно получить результат сохранённого задания.'));
    let job;
    try{job=await(await request(`/api/v1/jobs/${state.jobId}`)).json();}
    catch(error){if(error.status===401||error.status===404)throw error;message(tr('Связь прервана. Повторяем получение результата…'));await new Promise(resolve=>setTimeout(resolve,2000));continue;}
    if(generation!==state.generation)return;
    let meta={review:{duration_sec:0,events:[]}};
    try{meta=await(await request(`/api/v1/jobs/${state.jobId}/review`,{retries:0})).json();}catch{/* Jury result is independently available. */}
    if(generation!==state.generation)return;
    renderProgress(meta.progress,meta.stage);
    if(job.status==='failed')throw new Error(ru.errors[job.error]||ru.errors.analysis_failed);
    if(job.status==='done'){message('');state.result=job;state.review=meta;await renderResult();return;}
    await new Promise(resolve=>setTimeout(resolve,1000));
  }
}
$('resume-poll').addEventListener('click',async()=>{message('');startElapsed();try{await pollJob(state.generation);}catch(error){message(error.message);$('resume-poll').hidden=false;}finally{clearInterval(state.timer);}});
$('video-file').addEventListener('change',e=>setFile(e.target.files[0]));
$('remove-file').addEventListener('click',()=>{$('video-file').value='';setFile(null);});
for(const event of ['dragover','dragenter'])$('drop-zone').addEventListener(event,e=>{e.preventDefault();$('drop-zone').classList.add('dragging');});
for(const event of ['dragleave','drop'])$('drop-zone').addEventListener(event,e=>{e.preventDefault();$('drop-zone').classList.remove('dragging');});
$('drop-zone').addEventListener('drop',e=>{$('video-file').files=e.dataTransfer.files;setFile(e.dataTransfer.files[0]);});
$('upload-form').addEventListener('submit',async e=>{
  e.preventDefault();message('');if(!state.file)return;
  if(!/\.(mov|mp4)$/i.test(state.file.name)){message(tr('Выберите видео в формате MOV или MP4.'));return;}
  if(state.file.size>100*1024*1024){message(tr('Размер видео превышает 100 МБ. Выберите файл меньшего размера.'));return;}
  if(!state.authorized){message(tr('Сначала введите ключ в верхней панели и нажмите «Подключить».'));$('api-key').focus();return;}
  state.generation++;const generation=state.generation;
  state.jobId=null;state.result=null;$('resume-poll').hidden=true;
  screen('processing');renderProgress(null,'accepted');$('processing-filename').textContent=state.file.name;
  state.startedAt=Date.now();startElapsed();
  try{
    const form=new FormData();form.append('file',state.file);
    const response=await request('/api/v1/analyze',{method:'POST',body:form,timeoutMs:270000});
    state.jobId=(await response.json()).job_id;
    await pollJob(generation);
  }catch(error){message(error.message);if(state.jobId){$('resume-poll').hidden=false;}else screen('upload');}
  finally{clearInterval(state.timer);}
});

async function renderResult(){
  const {result,review}=state,detail=review.review||{};
  await loadCompliance();
  renderResultCopy();
  $('seek').max=detail.duration_sec||1;state.selected=null;state.drafts={};renderEvents();renderTimeline();
  renderMotion(detail);
  $('player').removeAttribute('src');
  if(state.blob){URL.revokeObjectURL(state.blob);state.blob=null;}
  $('video-error').hidden=true;screen('result');globalThis.scrollTo?.({top:0,left:0,behavior:'instant'});
  void loadActionTypes();
  if(result.violations.length)selectEvent(0);else{
    $('event-detail').hidden=true;
    $('annotation-form').reset();$('annotation-start').value=0;$('annotation-end').value=(detail.duration_sec||0).toFixed(2);
    $('annotation-start').max=detail.duration_sec;$('annotation-end').max=detail.duration_sec;
    $('annotation-feedback').textContent='';
  }
  void loadPreview(state.generation,state.jobId);
  await renderHumanObservations();
  $('result-title').tabIndex=-1;$('result-title').focus({preventScroll:true});
  try{
    const registry=await(await request('/api/v1/rules')).json();const select=$('annotation-rule');
    select.replaceChildren(element('option','',tr('Основание пока не установлено')));select.firstElementChild.value='';
    for(const rule of registry.rules||[]){const option=element('option','',`${rule.section} — ${rule.title}`);option.value=rule.id;select.append(option);}
  }catch{/* Rules remain explicitly unassigned when the registry is unavailable. */}
}
function renderResultCopy(){const {result,review}=state,detail=review.review||{};
  $('result-title').textContent=verdictLabel(result.verdict);
  const count=result.violations.length;
  $('result-subtitle').textContent=`${review.filename||state.file?.name||tr('Видео')} · ${time(detail.duration_sec)} · ${count?`${count} ${fragmentWord(count)}`:tr('нет отмеченных фрагментов')}`;
  const summary=$('result-explanation');summary.replaceChildren();summary.className=`result-explanation ${result.verdict==='violation'?'has-signals':'no-signals'}`;
  summary.append(element('strong','',count?tr`${count} ${fragmentWord(count)} для просмотра`:tr('Нет отмеченных фрагментов')),
    element('p','',result.verdict==='violation'?tr('Модель обнаружила признаки возможного нарушения. Проверьте исходные кадры и применимость правила.'):tr('Отсутствие срабатываний не гарантирует отсутствие нарушения. Короткие или закрытые действия могут быть пропущены.')));
  $('event-count').textContent=result.violations.length;
  $('research-banner').textContent=tr('Требует проверки человеком.');
  $('report-feedback').textContent='';
  const list=$('limitations-list');list.replaceChildren();for(const text of [...new Set([...(detail.limitations||[]).map(value=>tr(value)),tr('Новый ракурс и типы действий требуют отдельной оценки.'),tr('Временная локализация и применимость правил проверяются отдельно.'),tr('Звук и идентификация лиц не используются.')])])list.append(element('li','',text));
  const measured=detail.measurements||{};$('measurements').textContent=tr`Полное время: ${review.elapsed_sec??measured.analysis_wall_sec??'—'} сек · Визуальных выборок: ${measured.samples??'—'}`;
}
function renderMotion(detail){
  const samples=(detail.motion_spectrum?.samples||[]).filter(row=>Number.isFinite(row.image_change_per_sec));
  $('motion-panel').hidden=!samples.length;$('motion-path').setAttribute('d','');
  if(samples.length){const peak=Math.max(...samples.map(row=>row.image_change_per_sec),.000001),duration=detail.duration_sec||1;
    $('motion-path').setAttribute('d',samples.map((row,i)=>`${i?'L':'M'}${(1000*row.timestamp_sec/duration).toFixed(2)},${(95-90*row.image_change_per_sec/peak).toFixed(2)}`).join(' '));}
  const guard=detail.visibility_guard;
  $('visibility-guard').hidden=!guard?.flagged_samples;$('visibility-guard').textContent=guard?tr`Качество изображения: ${guard.flagged_samples} из ${guard.sample_count} выборок имеют технические предупреждения.`:'';
}
$('motion-spectrum').addEventListener('click',event=>{const box=$('motion-spectrum').getBoundingClientRect(),duration=state.review?.review?.duration_sec||0;const target=Math.max(0,Math.min(1,(event.clientX-box.left)/box.width))*duration;$('player').currentTime=target;$('seek').value=target;});
async function loadPreview(generation,jobId){
  for(let attempt=0;attempt<12&&generation===state.generation;attempt++){
    try{
      const media=await request(`/api/v1/jobs/${jobId}/video`,{retries:0});
      const blob=await media.blob();if(generation!==state.generation)return;
      state.blob=URL.createObjectURL(blob);$('player').src=state.blob;$('video-error').hidden=true;return;
    }catch(error){
      if(generation!==state.generation)return;
      $('video-error').hidden=false;
      if(error.status&&error.status!==409)return;
    }
    await new Promise(resolve=>setTimeout(resolve,2000));
  }
}
function reviewEvent(index){return state.review?.review?.events?.[index]||{};}
function renderEvents(){
  const list=$('event-list');list.replaceChildren();const events=state.result.violations;
  if(!events.length){list.append(element('p','empty-state',ru.emptyEvents));return;}
  const rank={high:0,medium:1,low:2,unspecified:3};const indices=events.map((_,i)=>i);
  indices.sort((a,b)=>$('event-sort').value==='priority'?(rank[reviewEvent(a).priority]??3)-(rank[reviewEvent(b).priority]??3)||events[a].timecode_sec-events[b].timecode_sec:events[a].timecode_sec-events[b].timecode_sec);
  for(const i of indices){
    const event=events[i],meta=reviewEvent(i),priority=meta.priority||'unspecified';const button=element('button','event-item');button.type='button';
    button.dataset.eventIndex=i;button.setAttribute('aria-pressed',String(state.selected===i));
    const unresolved=meta.localization_status==='unresolved';
    const title=unresolved?tr('Сигнал относится ко всему видео'):displayEventTitle(event,meta);
    button.append(element('span','event-kicker',tr`Фрагмент ${i+1} · ${unresolved?tr('время не установлено'):time(event.timecode_sec)}`),element('strong','',title),element('span','event-explainer',unresolved?tr('Кадры показывают контекст, а не момент нарушения.'):tr('Откройте видео и исходные кадры для проверки.')),element('span',`badge ${priority}`,ru.priority[priority]||ru.priority.unspecified));
    button.addEventListener('click',()=>selectEvent(i));list.append(button);
  }
}
function renderTimeline(){
  $('selected-interval').hidden=true;$('selected-interval').style.left='0%';$('selected-interval').style.width='0%';$('seek').value=0;$('playhead').style.left='0%';
  for(const node of $('timeline').querySelectorAll('.event-marker,.coverage-segment'))node.remove();const duration=state.review.review.duration_sec||1;
  const intervals=state.review.review.coverage?.length?state.review.review.coverage:[{start_sec:0,end_sec:duration,status:'unknown'}];
  for(const interval of intervals){const status=['sampled','incomplete','unknown'].includes(interval.status)?interval.status:'unknown';const segment=element('div',`coverage-segment coverage-${status}`);segment.style.left=`${100*interval.start_sec/duration}%`;segment.style.width=`${100*(interval.end_sec-interval.start_sec)/duration}%`;segment.setAttribute('role','img');segment.setAttribute('aria-label',`${time(interval.start_sec)}–${time(interval.end_sec)}: ${status==='sampled'?tr('выборочный анализ, видимость не оценена'):status==='incomplete'?tr('неполный анализ'):tr('состояние покрытия неизвестно')}`);$('timeline').prepend(segment);}
  state.result.violations.forEach((event,i)=>{if(reviewEvent(i).localization_status==='unresolved')return;const marker=element('button','event-marker');marker.type='button';marker.style.left=`${Math.min(99,100*event.timecode_sec/duration)}%`;const label=reviewEvent(i).localization_status==='unresolved'?tr`Фрагмент ${i+1}: точное время не установлено`:tr`Фрагмент ${i+1} для просмотра, ${time(event.timecode_sec)}`;marker.setAttribute('aria-label',label);marker.title=label;marker.addEventListener('click',()=>selectEvent(i));$('timeline').append(marker);});
}
function selectEvent(index,{seek=true}={}){
  const keyboardSelection=document.activeElement?.matches('.event-item');
  if(state.selected!==null){state.drafts[state.selected]={start:$('annotation-start').value,end:$('annotation-end').value,reviewer:$('annotation-reviewer').value,type:$('annotation-type').value,fact:$('annotation-fact').value,rule:$('annotation-rule').value,confirm:$('annotation-confirm').checked,visibility:$('annotation-visibility').value,applicability:$('annotation-applicability').checked};}
  state.selected=index;const event=state.result.violations[index],meta=reviewEvent(index),duration=state.review.review.duration_sec||1;
  if(seek&&meta.localization_status!=='unresolved')$('player').currentTime=event.timecode_sec;$('event-detail').hidden=false;$('detail-title').textContent=tr`Фрагмент ${index+1}: ${displayEventTitle(event,meta)}`;
  $('detail-time').textContent=meta.localization_status==='unresolved'?tr('Момент события не установлен. Показан контекст видео.'):tr`${time(event.timecode_sec)} · участок ${time(meta.start_sec??event.timecode_sec)}–${time(meta.end_sec??event.timecode_sec)}`;
  const priority=meta.priority||'unspecified';$('detail-priority').className=`badge ${priority}`;$('detail-priority').textContent=ru.priority[priority]||ru.priority.unspecified;
  $('detail-description').textContent=localizedEventDescription(event,meta);$('priority-reason').textContent=meta.priority==='unspecified'?'':meta.priority_reason||'';
  renderCompliance(index);
  const interval=$('selected-interval');interval.hidden=meta.localization_status==='unresolved';interval.style.left=`${100*(meta.start_sec??event.timecode_sec)/duration}%`;interval.style.width=`${Math.max(.5,100*((meta.end_sec??event.timecode_sec)-(meta.start_sec??event.timecode_sec))/duration)}%`;
  $('evidence').replaceChildren();event.evidence_frames.forEach((frame,i)=>{
    const figure=element('figure'),button=element('button','frame-button');button.type='button';
    const img=element('img');img.src=`data:image/jpeg;base64,${frame.image_base64}`;img.alt=tr`Оригинальный кадр, ${preciseTime(frame.timestamp_sec)}`;
    button.append(img);button.addEventListener('click',()=>{$('full-frame').src=img.src;$('frame-dialog').showModal();});
    const position=frame.timestamp_sec<event.timecode_sec-.05?0:frame.timestamp_sec>event.timecode_sec+.05?2:1;
    figure.append(button,element('figcaption','',`${meta.localization_status==='unresolved'?tr('Контекст видео'):ru.frames[position]} · ${preciseTime(frame.timestamp_sec)}`));$('evidence').append(figure);
  });
  const draft=state.drafts[index];
  $('annotation-start').value=draft?.start??(meta.start_sec??event.timecode_sec).toFixed(2);$('annotation-end').value=draft?.end??(meta.end_sec??event.timecode_sec).toFixed(2);
  $('annotation-start').max=duration;$('annotation-end').max=duration;$('annotation-type').value=draft?.type??'';$('annotation-fact').value=draft?.fact??'';$('annotation-reviewer').value=draft?.reviewer??'';$('annotation-rule').value=draft?.rule??'';$('annotation-confirm').checked=draft?.confirm??false;$('annotation-feedback').textContent='';renderEvents();
  $('annotation-visibility').value=draft?.visibility??'unknown';$('annotation-applicability').checked=draft?.applicability??false;
  if(keyboardSelection)$('event-list').querySelector(`[data-event-index="${index}"]`)?.focus({preventScroll:true});
}
$('event-sort').addEventListener('change',renderEvents);
$('seek').addEventListener('input',()=>{$('player').currentTime=Number($('seek').value);});
$('player').addEventListener('timeupdate',syncPlayerTime);
$('player').addEventListener('error',()=>{$('video-error').hidden=false;});
$('new-check').addEventListener('click',()=>{state.generation++;$('player').pause();$('player').removeAttribute('src');if(state.blob){URL.revokeObjectURL(state.blob);state.blob=null;}message('');screen('upload');$('video-file').focus();});
$('close-frame').addEventListener('click',()=>$('frame-dialog').close());
$('annotation-form').addEventListener('submit',async e=>{
  e.preventDefault();const rule=$('annotation-rule').value;
  const confirmed=$('annotation-confirm').checked,applicability=$('annotation-applicability').checked,visibility=$('annotation-visibility').value;
  const annotation={start_sec:Number($('annotation-start').value),end_sec:Number($('annotation-end').value),reviewer:$('annotation-reviewer').value.trim(),type:$('annotation-type').value.trim(),observed_fact:$('annotation-fact').value.trim(),visibility,rule_applicability_confirmed:applicability,origin:'human',confirmed,rule_id:rule||null,rule_status:rule?(confirmed&&applicability&&visibility==='clear'?'confirmed':'proposed'):'unresolved'};
  try{await request(`/api/v1/jobs/${state.jobId}/annotations`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(annotation)});$('annotation-feedback').textContent=confirmed?tr('Подтверждённое наблюдение сохранено. Ответ модели остаётся исходным.'):tr('Черновик сохранён отдельно от подтверждённой разметки.');await renderHumanObservations();await loadCompliance();if(state.selected!==null)renderCompliance(state.selected);}
  catch(error){$('annotation-feedback').textContent=error.message;}
});

function renderActionTypes(){
  const list=$('annotation-action-types');list.replaceChildren();
  for(const item of actionTypes){if(item.id==='unknown')continue;const label=item.labels?.[language()]||item.labels?.ru||item.labels?.en;if(typeof label!=='string'||!label.trim())continue;const option=element('option');option.value=label;list.append(option);}
}
async function loadActionTypes(){
  try{const catalog=await(await request('/api/v1/action-types',{retries:0,timeoutMs:4000})).json();actionTypes=Array.isArray(catalog.classes)?catalog.classes:[];}catch{actionTypes=[];}
  renderActionTypes();
}

async function renderHumanObservations(){
  const list=$('human-observations');list.replaceChildren();
  try{
    const {annotations}=await(await request(`/api/v1/jobs/${state.jobId}/annotations`)).json();
    if(!annotations.length){list.append(element('li','',tr('Разметка ещё не сохранена.')));return;}
    for(const item of annotations){
      const status=item.confirmed?tr('Подтверждено проверяющим'):tr('Черновик');
      const rule=item.rule_status==='confirmed'?tr('Применимость правила подтверждена проверяющим.'):item.rule_id?tr('Правило предложено, применимость не подтверждена.'):tr('Нормативное основание не установлено.');
      list.append(element('li','',`${status} · ${time(item.start_sec)}–${time(item.end_sec)} · ${item.type}: ${item.observed_fact} ${rule}`));
    }
  }catch{list.append(element('li','',tr('Не удалось загрузить разметку. Повторите проверку соединения.')));}
}

async function downloadArtifact(kind){
  const button=$(kind==='pdf'?'download-report':'download-passport'),generation=state.generation;
  button.disabled=true;$('report-feedback').textContent=tr('Подготовка документа…');
  try{
    const response=await request(`/api/v1/jobs/${state.jobId}/${kind==='pdf'?'report.pdf':'passport'}?language=${language()}`);
    if(generation!==state.generation)return;
    const url=URL.createObjectURL(await response.blob()),link=element('a');
    link.href=url;link.download=`video-audit-${state.jobId}.${kind==='pdf'?'pdf':'json'}`;
    document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),2000);
    $('report-feedback').textContent=tr('Документ подготовлен. Выводы требуют подтверждения уполномоченным сотрудником.');
  }catch(error){if(generation===state.generation)$('report-feedback').textContent=error.message;}
  finally{button.disabled=false;}
}
$('download-report').addEventListener('click',()=>downloadArtifact('pdf'));
$('download-passport').addEventListener('click',()=>downloadArtifact('passport'));

function renderProgress(progress,stage){
  state.progress={progress,stage};
  const label=ru.stages[progress?.stage||stage]||tr('Обработка видео');
  $('processing-stage').textContent=label;
  const bar=$('processing-progress'),detail=$('progress-detail');
  const measured=progress?.scope==='stage'&&Number.isFinite(progress.percent)&&Number.isFinite(progress.completed)&&Number.isFinite(progress.total)&&progress.total>0;
  if(measured){const percent=Math.max(0,Math.min(99,Math.floor(progress.percent)));bar.value=percent;bar.setAttribute('aria-valuetext',`${label}: ${percent}%`);detail.textContent=`${label}: ${percent}% · ${progress.completed} / ${progress.total}`;}
  else{bar.removeAttribute('value');bar.removeAttribute('aria-valuetext');detail.textContent=tr('Ожидаем измерения текущего этапа');}
}
async function loadCompliance(){state.compliance=[];try{const data=await(await request(`/api/v1/jobs/${state.jobId}/compliance`,{retries:0})).json();state.compliance=data.events||[];}catch{/* Optional metadata must not block the jury verdict. */}}
function renderCompliance(index){
  const node=$('rule-description');node.replaceChildren();
  const meta=reviewEvent(index),item=state.compliance.find(row=>row.event_id===(meta.event_id||meta.id||`event-${index+1}`));
  const status=item?.status||'unresolved';node.append(element('span',`badge ${status==='confirmed'?'low':'unspecified'}`,status==='confirmed'?tr('Подтверждено проверяющим'):status==='proposed'?(item.origin==='human'?tr('Предложено проверяющим'):tr('Предложено моделью')):tr('Основание не установлено')));
  if(item?.association_status==='ambiguous'){node.append(element('p','',tr('Наблюдение пересекает несколько событий; связь с конкретным событием не установлена.')));return;}
  if(item?.observed_fact)node.append(element('p','',item.observed_fact));
  if(item?.type)node.append(element('p','',`${tr('Тип действия')}: ${item.type.startsWith('Визуальный сигнал')?tr('Тип действия не определён'):item.type}`));
  const rule=item?.rule;
  if(!rule){node.append(element('p','',item?.observation_status==='confirmed'?tr('Действие подтверждено проверяющим; нормативное основание не установлено.'):tr('Конкретное действие и условия применения нормы требуют подтверждения.')));return;}
  node.append(element('strong','',rule.title),element('p','',`${rule.document}${rule.section?` · ${rule.section}`:''}`),element('p','source-language',tr('Нормативные сведения приведены на языке источника.')));
  if(/^https?:\/\//i.test(rule.url||'')){const link=element('a','',tr('Открыть первоисточник'));link.href=rule.url;link.target='_blank';link.rel='noopener noreferrer';node.append(link);}
  const details=element('dl');
  for(const [name,value] of [[tr('Связь с действием'),item.message?tr(item.message):null],[tr('Условия применимости'),rule.applicability],[tr('Ограничения'),rule.limits],[tr('Источник проверен'),localDate(rule.verified_on)],[tr('Проверяющий'),item.reviewer]])if(value){details.append(element('dt','',name),element('dd','',value));}
  node.append(details);
}
function localizedEventDescription(event,meta){
  if(meta.localization_status==='unresolved')return tr('Бинарный сигнал относится к видео целиком. Контекстные кадры не устанавливают момент события.');
  if(event.type?.startsWith('Визуальный сигнал'))return tr('Временной фрагмент выделен моделью. Наблюдаемое действие и его тип требуют проверки.');
  return event.description;
}
function refreshLanguage(){
  heroCarousel?.refresh();
  renderActionTypes();
  const savedScroll={top:globalThis.scrollY||0,left:globalThis.scrollX||0};
  if(state.overview)renderOverview(state.overview);else renderLockedMetrics();
  $('access-status').textContent=state.authorized?tr('Подключено'):tr('Не подключено');
  if(state.file)$('file-size').textContent=tr`${(state.file.size/1024/1024).toFixed(1)} МБ`;
  sidebarToggle.setAttribute('aria-label',sidebarOpen?tr('Свернуть боковую панель'):tr('Открыть боковую панель'));
  sidebarToggle.title=sidebarOpen?tr('Свернуть боковую панель'):tr('Открыть боковую панель');
  if(state.currentView==='rules')void loadRules();if(state.currentView==='system')void loadSystem();
  if(state.progress)renderProgress(state.progress.progress,state.progress.stage);
  if(state.startedAt&&state.currentView==='processing')$('elapsed').textContent=tr`Прошло ${time((Date.now()-state.startedAt)/1000)}`;
  if(state.result){renderResultCopy();renderEvents();renderTimeline();renderMotion(state.review.review||{});if(state.selected!==null)selectEvent(state.selected,{seek:false});syncPlayerTime();void renderHumanObservations();}
  globalThis.scrollTo?.({...savedScroll,behavior:'instant'});
}
initPreferences(refreshLanguage);
heroCarousel=initHeroCarousel();
initHero({onDragChange:value=>heroCarousel?.block('drag',value)});
renderLockedMetrics();
