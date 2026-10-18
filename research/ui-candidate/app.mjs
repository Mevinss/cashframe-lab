import {installChecklist} from './rule-checklist.mjs';
import {installRealView} from './real-view.mjs';
import {DURATION, STAGES, SCENARIOS, createState, eventsFor, selectedEvent, chooseEvent,
  chooseScenario, seek, advanceStage, reviewKey, saveReview, validateFileMetadata, formatTime} from './demo.mjs';

const $ = id => document.getElementById(id);
let state = createState(), playTimer = null, toastTimer = null, currentMode='demo';
const realUi=installRealView();
installChecklist();
const views = {inspection:'Проверка записи', upload:'Новая запись', reports:'Отчёт и паспорт', method:'О прототипе'};
const mobileNavigation = window.matchMedia('(max-width: 640px)');
const node = (tag, className, text) => {const el=document.createElement(tag);if(className)el.className=className;if(text!==undefined)el.textContent=text;return el;};
function notify(text) {$('toast').textContent=text;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,5000);}
function pause() {clearInterval(playTimer);playTimer=null;state={...state,playing:false};$('play').textContent='▶';$('play').setAttribute('aria-pressed','false');$('play').setAttribute('aria-label','Запустить движение демо-курсора');}
function syncNavigation() {$('sidebar').inert=mobileNavigation.matches&&!$('sidebar').classList.contains('open');}
function closeMenu() {$('sidebar').classList.remove('open');$('menu').setAttribute('aria-expanded','false');syncNavigation();}
function view(name, focus=true) {
  if (!Object.hasOwn(views,name)) name='inspection';
  if(currentMode==='real'){for(const key of Object.keys(views))$(`${key}-view`).hidden=true;for(const button of document.querySelectorAll('[data-view]')){if(button.dataset.view===name)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');}realUi.navigate(name);$('breadcrumb').textContent=views[name];document.title=`${views[name]} · Локальный backend`;closeMenu();return;}
  captureDraft();pause();state={...state,view:name};
  for (const key of Object.keys(views)) $(`${key}-view`).hidden=key!==name;
  for (const button of document.querySelectorAll('[data-view]')) {
    if(button.dataset.view===name)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');
  }
  $('breadcrumb').textContent=views[name];document.title=`${views[name]} · Видеоаудит · Демо`;
  closeMenu();if(name==='reports')renderReviews();
  if(focus){const heading=$(`${name}-title`);heading.tabIndex=-1;heading.focus({preventScroll:true});window.scrollTo(0,0);}
}
for(const button of document.querySelectorAll('[data-view],[data-go]'))button.addEventListener('click',()=>{location.hash=button.dataset.view||button.dataset.go;view(button.dataset.view||button.dataset.go);});
window.addEventListener('hashchange',()=>view(location.hash.slice(1),false));
$('menu').addEventListener('click',()=>{const open=$('sidebar').classList.toggle('open');$('menu').setAttribute('aria-expanded',String(open));syncNavigation();if(open)$('sidebar').querySelector('nav button').focus();});
mobileNavigation.addEventListener('change',closeMenu);
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&$('sidebar').classList.contains('open')){closeMenu();$('menu').focus();}});
document.addEventListener('click',event=>{if(!event.target.closest('#sidebar')&&!event.target.closest('#menu'))closeMenu();});

function renderClock() {
  $('player-time').replaceChildren(node('span','',formatTime(state.time)),node('span','muted',' / 02:00'));
  $('frame-time').textContent=formatTime(state.time);$('seek').value=state.time;
  $('seek').setAttribute('aria-valuetext',`${formatTime(state.time)} из 02:00, синтетическая шкала`);
  $('playhead').style.left=`${state.time/DURATION*100}%`;
}
function renderEvents() {
  const list=$('event-list'),markers=$('timeline-markers');list.replaceChildren();markers.replaceChildren();
  const events=eventsFor(state.scenario);$('event-count').textContent=events.length;
  if(!events.length)list.append(node('p','empty-events','Нет предложенных событий. Это не подтверждение отсутствия нарушения.'));
  for(const event of events){
    const button=node('button','event-card');button.type='button';button.dataset.eventId=event.id;button.setAttribute('aria-pressed',String(state.selected===event.id));
    const top=node('span','event-top');top.append(node('span','mono',event.localization_status==='unresolved'?'Время не установлено':formatTime(event.timecode_sec)),node('span','event-number',event.id==='signal-02'?'02':'01'));
    const meta=node('span','event-meta');meta.append(node('i','status-dot'),node('span','',event.localization_status==='unresolved'?'Неразрешённая локализация':'Предложение модели · не подтверждено'));
    button.append(top,node('strong','',event.label+' · тип не установлен'),meta);list.append(button);
    if(event.localization_status!=='unresolved'){
      const marker=node('button','interval-marker');marker.type='button';marker.dataset.eventId=event.id;
      marker.style.left=`${event.start_sec/DURATION*100}%`;marker.style.width=`${(event.end_sec-event.start_sec)/DURATION*100}%`;
      marker.setAttribute('aria-label',`${event.label}, ${formatTime(event.start_sec)}–${formatTime(event.end_sec)}, предложение модели`);
      marker.setAttribute('aria-pressed',String(state.selected===event.id));markers.append(marker);
    }
  }
}
function renderEvidence(event) {
  const list=$('evidence-list');list.replaceChildren();
  event.evidence.forEach((timestamp,index)=>{
    const button=node('button','evidence-button');button.type='button';button.setAttribute('aria-label',`Открыть синтетическую заглушку ${formatTime(timestamp)}`);
    const image=node('div','evidence-image'),img=node('img');img.src='./scene.svg';img.alt='Абстрактная демо-заглушка, не доказательство';image.append(img,node('span','','СИНТЕТИЧЕСКИЙ МАКЕТ'));
    const caption=node('span','evidence-caption');caption.append(node('span','',event.localization_status==='unresolved'?'Контекст':index===0?'До интервала':index===1?'В интервале':'После интервала'),node('span','mono',formatTime(timestamp)));
    button.append(image,caption);button.addEventListener('click',()=>{state=seek(state,timestamp);renderClock();$('frame-dialog-title').textContent=`Демо-кадр · ${formatTime(timestamp)}`;$('frame-dialog').showModal();});list.append(button);
  });
}
function readForm() {return {start:$('review-start').value===''?NaN:Number($('review-start').value),end:$('review-end').value===''?NaN:Number($('review-end').value),reviewer:$('reviewer').value,observation:$('observation').value,visibility:$('visibility').value,confirmed:$('confirmed').checked};}
function captureDraft() {
  if(state.view==='inspection'&&selectedEvent(state)&&!$('event-detail').hidden)state={...state,drafts:{...state.drafts,[reviewKey(state)]:readForm()}};
}
function reviewSummary() {
  const record=state.reviews[reviewKey(state)];
  $('review-summary').textContent=record?(record.confirmed?'Подтверждено человеком · только демо':'Сохранён учебный черновик'):'Наблюдение не подтверждено';
}
function renderDetail() {
  const event=selectedEvent(state);$('event-detail').hidden=!event;if(!event)return;
  const unresolved=event.localization_status==='unresolved';
  $('detail-title').textContent=`${event.label} · ${unresolved?'контекст видео':formatTime(event.timecode_sec)}`;
  $('detail-status').textContent=unresolved?'Время не установлено':'Предложение модели';
  $('localization-fact').textContent=unresolved?'Не определена':`${formatTime(event.start_sec)} — ${formatTime(event.end_sec)}`;
  $('score-fact').textContent=`${event.score.toFixed(2)} · условная оценка`;
  $('interval-label').textContent=unresolved?'Показан контекст, не локализация':'Интервалы предложений модели';
  renderEvidence(event);
  const draft=state.drafts[reviewKey(state)]||state.reviews[reviewKey(state)];
  $('review-form').reset();$('review-start').value=draft?draft.start:event.start_sec;$('review-end').value=draft?draft.end:event.end_sec;
  if(draft){$('reviewer').value=draft.reviewer;$('observation').value=draft.observation;$('visibility').value=draft.visibility;$('confirmed').checked=draft.confirmed;}
  $('review-feedback').textContent='Ничего не отправляется на сервер';reviewSummary();
}
function renderScenario() {
  const scenario=SCENARIOS[state.scenario];$('scenario').value=state.scenario;
  $('verdict').className=`verdict ${scenario.tone}`;$('verdict-symbol').textContent=scenario.symbol;$('verdict-title').textContent=scenario.title;$('verdict-copy').textContent=scenario.copy;
  $('verdict-tag').textContent=scenario.verdict?'Вывод модели · демо':'Решения модели нет';
  const special=['pending','failed','empty'].includes(state.scenario);
  $('workspace').hidden=special;$('state-panel').hidden=!special;$('progress-steps').hidden=state.scenario!=='pending';$('advance-stage').hidden=state.scenario!=='pending';$('retry').hidden=state.scenario!=='failed';$('empty-upload').hidden=state.scenario!=='empty';
  $('state-glyph').textContent=scenario.symbol;$('state-title').textContent=scenario.title;$('state-copy').textContent=scenario.copy;
  const steps=$('progress-steps');steps.replaceChildren();
  STAGES.forEach((stage,index)=>{const item=node('li',index===state.stage?'active':index<state.stage?'completed':'',stage);if(index===state.stage)item.setAttribute('aria-current','step');steps.append(item);});
  $('advance-stage').textContent=state.stage===STAGES.length-1?'Показать синтетический результат':'Следующий учебный этап';
  renderEvents();renderDetail();renderClock();
  if(state.scenario==='negative')$('interval-label').textContent='Предложенных интервалов нет';
}
function changeScenario(name) {captureDraft();pause();state=chooseScenario(state,name);renderScenario();}
$('scenario').addEventListener('change',event=>changeScenario(event.target.value));
function eventSelection(event) {
  const button=event.target.closest('[data-event-id]');if(!button)return;
  const inMarkers=Boolean(button.closest('#timeline-markers'));captureDraft();pause();state=chooseEvent(state,button.dataset.eventId);renderEvents();renderDetail();renderClock();
  const root=inMarkers?$('timeline-markers'):$('event-list');root.querySelector(`[data-event-id="${state.selected}"]`)?.focus({preventScroll:true});
}
$('event-list').addEventListener('click',eventSelection);$('timeline-markers').addEventListener('click',eventSelection);
$('seek').addEventListener('input',event=>{state=seek(state,Number(event.target.value));renderClock();});
$('back').addEventListener('click',()=>{state=seek(state,state.time-5);renderClock();});$('forward').addEventListener('click',()=>{state=seek(state,state.time+5);renderClock();});
$('play').addEventListener('click',()=>{
  if(state.playing){pause();return;}if(state.time>=DURATION)state=seek(state,0);
  state={...state,playing:true};$('play').textContent='Ⅱ';$('play').setAttribute('aria-pressed','true');$('play').setAttribute('aria-label','Остановить движение демо-курсора');
  playTimer=setInterval(()=>{state=seek(state,state.time+.25);renderClock();if(state.time>=DURATION)pause();},250);
});
document.addEventListener('visibilitychange',()=>{if(document.hidden)pause();});
$('advance-stage').addEventListener('click',()=>{state=advanceStage(state);renderScenario();if(state.scenario==='positive')$('verdict-title').scrollIntoView({block:'nearest'});});
$('retry').addEventListener('click',()=>changeScenario('pending'));
$('close-frame').addEventListener('click',()=>$('frame-dialog').close());
$('frame-dialog').addEventListener('click',event=>{if(event.target===$('frame-dialog')){const r=event.target.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)event.target.close();}});
$('review-form').addEventListener('submit',event=>{
  event.preventDefault();try{state=saveReview(state,readForm());reviewSummary();$('review-feedback').textContent='Сохранено в памяти вкладки · демо. Норма не подтверждена.';notify('Учебное наблюдение сохранено. Модельный сигнал не изменён.');}
  catch(error){$('review-feedback').textContent=error.message;}
});
$('review-form').addEventListener('input',()=>{$('review-feedback').textContent='Есть несохранённые изменения · демо';});
function renderReviews() {
  const container=$('saved-reviews');container.replaceChildren();const records=Object.values(state.reviews);
  if(!records.length){container.append(node('p','','Учебных наблюдений пока нет. Выберите событие и заполните форму проверки человеком.'));return;}
  for(const record of records){const entry=node('div','saved-review');entry.append(node('strong','',`${record.label} · ${formatTime(record.start)}–${formatTime(record.end)}`),node('span','tag',record.confirmed?'Подтверждение · только демо':'Учебный черновик'),node('p','',record.observation),node('p','fine-print',`${record.reviewer} · нормативное основание не установлено`));container.append(entry);}
}
$('video-file').addEventListener('change',event=>{const result=validateFileMetadata(event.target.files[0]);$('file-feedback').textContent=result.message;$('clear-file').disabled=!event.target.files.length;});
$('clear-file').addEventListener('click',()=>{$('video-file').value='';$('file-feedback').textContent='Файл не выбран';$('clear-file').disabled=true;});
$('demo-run').addEventListener('click',()=>{changeScenario('pending');location.hash='inspection';view('inspection');});
window.addEventListener('pagehide',pause);
$('app-mode').addEventListener('change',()=>{
  captureDraft();pause();clearTimeout(toastTimer);$('toast').hidden=true;$('frame-dialog').close();currentMode=$('app-mode').value;document.body.dataset.mode=currentMode;realUi.switchMode(currentMode);
  $('video-file').value='';$('file-feedback').textContent='Файл не выбран';
  $('mode-banner').replaceChildren(node('strong','',currentMode==='demo'?'Демо — синтетические данные':'Реальный backend — локальное подключение'),node('span','',currentMode==='demo'?'Видео не обрабатывается. Кадры, события и подтверждения — учебные.':'Нет подмены демо-данными. Загрузка и сохранение — только по явному действию.'));
  document.querySelector('.outline-badge').textContent=currentMode==='demo'?'ОФЛАЙН-ДЕМО':'ЛОКАЛЬНЫЙ API';
  if(currentMode==='demo'){renderScenario();}view(location.hash.slice(1)||'inspection',false);
});
renderScenario();view(location.hash.slice(1)||'inspection',false);
