import { ANNOTATION_TEMPLATES, annotationTemplate } from './annotation-templates.js';

const copy={
  ru:{back:'К видеоаудиту',language:'Язык',theme:'Сменить тему',heading:'Разметка исходных видео',intro:'Подтвердите видимое действие и его интервал. Исходные точки помогают найти эпизод; тип модель из них не получает.',key:'Ключ доступа',connect:'Подключить',filter:'Показать',all:'Все',positive:'События',negative:'Нормальные',pending:'Без полного просмотра',speed:'Скорость 0,5× / 1×',reviewer:'Проверяющий',events:'Наблюдаемые события',add:'Добавить событие',eventHelp:'Записывайте конкретное действие. Обычная операция сама по себе не нарушение. Если тип не установлен, оставьте его пустым.',full:'Видео просмотрено полностью, все видимые события внесены',confirm:'Подтверждаю внесённые наблюдения по исходному видео',save:'Сохранить разметку',privacy:'Сохраняется локально. Тестовые видео исключены.',start:'Начало, с',end:'Конец, с',fromVideo:'Из видео',type:'Тип события — одинаковое название для одного класса',fact:'Что видно и почему это может быть нарушением',visibility:'Видимость действия',clear:'Достаточная',partial:'Частичная — тип не подтверждаю',insufficient:'Недостаточная — тип не подтверждаю',remove:'Удалить событие',event:'Событие',normal:'метка: норма',signal:'метка: нарушение',saved:'Разметка сохранена. Исходные видео и Excel не изменены.',loading:'Загружаем видео…',loaded:'Видео готово. Отметьте границы по плееру.',error:'Не удалось выполнить запрос. Проверьте ключ и соединение.',invalid:'Проверьте границы, автора, видимость и описание. Для отрицательного видео события не добавляются.',conflict:'Разметка изменилась. Переподключитесь перед сохранением.',discard:'Несохранённая разметка будет потеряна. Перейти к другому видео?',count:'подтверждённых интервалов',videoError:'Браузер не смог открыть MOV. Откройте исходный файл в локальном плеере; времена и разметку можно сохранить здесь.'},
  kk:{back:'Бейнеаудитке оралу',language:'Тіл',theme:'Тақырыпты ауыстыру',heading:'Бастапқы бейнелерді белгілеу',intro:'Көрінетін әрекет пен оның аралығын растаңыз. Бастапқы уақыт белгілері оқиғаны табуға көмектеседі, бірақ түрін анықтамайды.',key:'Қол жеткізу кілті',connect:'Қосылу',filter:'Көрсету',all:'Барлығы',positive:'Оқиғалар',negative:'Қалыпты',pending:'Толық қаралмаған',speed:'Жылдамдық 0,5× / 1×',reviewer:'Тексеруші',events:'Бақыланған оқиғалар',add:'Оқиға қосу',eventHelp:'Нақты көрінетін әрекетті жазыңыз. Қалыпты операция өздігінен бұзушылық емес. Түрі белгісіз болса, бос қалдырыңыз.',full:'Бейне толық қаралды, барлық көрінетін оқиғалар енгізілді',confirm:'Бастапқы бейне бойынша енгізілген бақылауларды растаймын',save:'Белгілеуді сақтау',privacy:'Жергілікті сақталады. Тест бейнелері алынып тасталған.',start:'Басы, с',end:'Соңы, с',fromVideo:'Бейнеден алу',type:'Оқиға түрі — бір классқа бір атау',fact:'Не көрінеді және неліктен бұл бұзушылық болуы мүмкін',visibility:'Әрекеттің көрінуі',clear:'Жеткілікті',partial:'Ішінара — түрін растамаймын',insufficient:'Жеткіліксіз — түрін растамаймын',remove:'Оқиғаны жою',event:'Оқиға',normal:'белгі: қалыпты',signal:'белгі: бұзушылық',saved:'Белгілеу сақталды. Бастапқы бейнелер мен Excel өзгертілген жоқ.',loading:'Бейне жүктелуде…',loaded:'Бейне дайын. Ойнатқышта аралықты белгілеңіз.',error:'Сұрау орындалмады. Кілт пен байланысты тексеріңіз.',invalid:'Аралықты, авторды, көрінуді және сипаттаманы тексеріңіз. Қалыпты бейнеге оқиға қоспаңыз.',conflict:'Белгілеу өзгерді. Сақтау алдында қайта қосылыңыз.',discard:'Сақталмаған белгілеу жоғалады. Басқа бейнеге өту керек пе?',count:'расталған аралық',videoError:'Браузер MOV файлын аша алмады. Оны жергілікті ойнатқышта ашып, уақыт пен белгілеуді осы жерде сақтаңыз.'},
  en:{back:'Back to video audit',language:'Language',theme:'Switch theme',heading:'Review source videos',intro:'Confirm the visible action and its interval. Original timestamps help locate an episode; they do not supply its class.',key:'Access key',connect:'Connect',filter:'Show',all:'All',positive:'Events',negative:'Normal',pending:'Not fully reviewed',speed:'Speed 0.5× / 1×',reviewer:'Reviewer',events:'Observed events',add:'Add event',eventHelp:'Describe the actual visible action. An ordinary operation is not a violation by itself. Leave the type blank if unknown.',full:'I watched the entire video and recorded all visible events',confirm:'I confirm these observations from the source video',save:'Save annotations',privacy:'Saved locally. Test videos are excluded.',start:'Start, s',end:'End, s',fromVideo:'From video',type:'Event type — use the same name for the same class',fact:'What is visible and why it may indicate a violation',visibility:'Action visibility',clear:'Sufficient',partial:'Partial — type unconfirmed',insufficient:'Insufficient — type unconfirmed',remove:'Remove event',event:'Event',normal:'label: normal',signal:'label: violation',saved:'Annotations saved. Source videos and Excel are unchanged.',loading:'Loading video…',loaded:'Video ready. Set interval boundaries using the player.',error:'Request failed. Check the access key and connection.',invalid:'Check the interval, reviewer, visibility and description. Do not add events to negative videos.',conflict:'Annotations changed. Reconnect before saving.',discard:'Unsaved annotations will be lost. Open another video?',count:'confirmed intervals',videoError:'This browser could not play MOV. Open the source in a local player; timestamps and annotations can still be saved here.'}
};
Object.assign(copy.ru,{target:'Это событие возможного нарушения, а не только обычное действие',targetHelp:'Без этой отметки сохранится наблюдение, которое не попадёт в обучение детектора нарушений.',conditions:'Перед выбором категории проверьте все условия:',source:'Нормативный источник',observations:'наблюдений'});
Object.assign(copy.kk,{target:'Бұл тек қалыпты әрекет емес, ықтимал бұзушылық оқиғасы',targetHelp:'Бұл белгісіз тек бақылау сақталады, ол бұзушылық детекторын оқытуға қосылмайды.',conditions:'Санатты таңдаудан бұрын барлық шартты тексеріңіз:',source:'Нормативтік дереккөз',observations:'бақылау'});
Object.assign(copy.en,{target:'This is a possible violation event, rather than an ordinary action only',targetHelp:'Without this selection, the observation is saved but excluded from violation-detector training.',conditions:'Check every condition before choosing this category:',source:'Regulatory source',observations:'observations'});
Object.assign(copy.ru,{template:'Шаблон — проверьте по видео',noTemplate:'Без шаблона — заполнить вручную',neutralTemplates:'Нейтральные наблюдения',candidateTemplates:'Признаки возможного нарушения',templateHelp:'Заготовка текста для проверки по видео. Ваши правки сохраняются.',replaceTemplate:'Заменить описание шаблоном'});
Object.assign(copy.kk,{template:'Үлгі — бейне бойынша тексеріңіз',noTemplate:'Үлгісіз — қолмен толтыру',neutralTemplates:'Бейтарап бақылаулар',candidateTemplates:'Ықтимал бұзушылық белгілері',templateHelp:'Бейне бойынша тексеруге арналған мәтін үлгісі. Өз түзетулеріңіз сақталады.',replaceTemplate:'Сипаттаманы үлгімен ауыстыру'});
Object.assign(copy.en,{template:'Template — verify against the video',noTemplate:'No template — enter manually',neutralTemplates:'Neutral observations',candidateTemplates:'Possible violation indicators',templateHelp:'A writing preset to verify against the video. Your edits are preserved.',replaceTemplate:'Replace description with template'});
Object.assign(copy.ru,{reviewer:'Проверяющий (имя или обозначение)',reviewerPlaceholder:'Например, Проверяющий 1',reviewerInvalid:'Введите имя или обозначение проверяющего, например «Проверяющий 1».'});
Object.assign(copy.kk,{reviewer:'Тексеруші (аты немесе белгісі)',reviewerPlaceholder:'Мысалы, Тексеруші 1',reviewerInvalid:'Тексерушінің атын немесе белгісін енгізіңіз, мысалы «Тексеруші 1».'});
Object.assign(copy.en,{reviewer:'Reviewer (name or label)',reviewerPlaceholder:'For example, Reviewer 1',reviewerInvalid:'Enter the reviewer’s name or label, for example “Reviewer 1”.'});
const $=id=>document.getElementById(id), t=key=>copy[lang][key];
let lang='ru',theme='light',key='',data=null,selected=null,blob=null,dirty=false,selection=0,saving=false,noticeKey=null;
let violationCatalog=[],ruleRegistry=[];
const category=value=>violationCatalog.find(item=>item.id===value||Object.values(item.labels).includes(value));
function typeOptions(){const list=$('review-type-list');list.replaceChildren();for(const item of violationCatalog){const option=document.createElement('option');option.value=item.labels[lang];list.append(option);}}
function ruleHint(node){const hint=node.querySelector('[data-rule-hint]'),item=category(node.querySelector('[data-field="type_id"]').value);hint.replaceChildren();hint.hidden=!item;if(!item)return;const heading=document.createElement('strong');heading.textContent=t('conditions');const list=document.createElement('ul');for(const value of item.requirements[lang]){const row=document.createElement('li');row.textContent=value;list.append(row);}hint.append(heading,list);for(const id of item.candidate_rule_ids){const rule=ruleRegistry.find(row=>row.id===id);if(rule&&rule.url.startsWith('https://')){const link=document.createElement('a');link.textContent=`${t('source')} · ${rule.section||rule.title}`;link.href=rule.url;link.target='_blank';link.rel='noopener noreferrer';hint.append(link);}}const limits=document.createElement('p');limits.textContent=item.limits[lang];hint.append(limits);}
function templateOptions(node){
  const select=node.querySelector('[data-template]'),selectedTemplate=select.value;
  select.replaceChildren(new Option(t('noTemplate'),''));
  for(const [kind,label] of [['neutral','neutralTemplates'],['candidate','candidateTemplates']]){
    const group=document.createElement('optgroup');group.label=t(label);
    for(const item of ANNOTATION_TEMPLATES.filter(item=>item.kind===kind))group.append(new Option(item.labels[lang],item.id));
    select.append(group);
  }
  select.value=selectedTemplate;
}
function templateButton(node){
  const item=annotationTemplate(node.querySelector('[data-template]').value),fact=node.querySelector('[data-field="observed_action"]');
  node.querySelector('[data-replace-template]').disabled=!item||fact.value===item.descriptions[lang];
}
function applyTemplate(node,force=false){
  const item=annotationTemplate(node.querySelector('[data-template]').value);
  if(item){
    node.querySelector('[data-field="type_id"]').value=category(item.type_id)?.labels[lang]||item.labels[lang];
    const fact=node.querySelector('[data-field="observed_action"]');
    if(force||!fact.value.trim()||fact.value===node.dataset.templateText){fact.value=item.descriptions[lang];node.dataset.templateText=fact.value;}
  }
  node.dataset.ruleId='';node.querySelector('[data-target]').checked=false;$('review-confirm').checked=false;dirty=true;
  ruleHint(node);templateButton(node);
}
const disabledBeforeSave=new Map();
function setSaving(value){saving=value;if(value){for(const node of document.querySelectorAll('button,input,select,textarea')){disabledBeforeSave.set(node,node.disabled);node.disabled=true;}}else{for(const [node,disabled] of disabledBeforeSave)node.disabled=disabled;disabledBeforeSave.clear();}}
try{lang=localStorage.getItem('videoaudit.language')||'ru';theme=localStorage.getItem('videoaudit.theme')||'light';}catch{}
if(!copy[lang])lang='ru';
function notice(value){noticeKey=Object.keys(copy[lang]).find(key=>copy[lang][key]===value)||null;$('review-notice').textContent=value;}
function localize(root=document){for(const node of root.querySelectorAll('[data-copy]'))node.textContent=t(node.dataset.copy);for(const node of root.querySelectorAll('[data-placeholder]'))node.placeholder=t(node.dataset.placeholder);document.documentElement.lang=lang;}
function preferences(){document.documentElement.dataset.theme=theme;$('review-language').value=lang;localize();try{localStorage.setItem('videoaudit.language',lang);localStorage.setItem('videoaudit.theme',theme);}catch{}}
preferences();
$('review-language').addEventListener('change',()=>{const message=noticeKey;lang=$('review-language').value;preferences();typeOptions();if(message)notice(t(message));if(data)renderSources();for(const [i,node] of [...$('review-events').children].entries()){node.querySelector('legend').textContent=`${t('event')} ${i+1}`;const field=node.querySelector('[data-field="type_id"]'),item=category(field.value)||annotationTemplate(field.value);if(item)field.value=item.labels[lang];templateOptions(node);templateButton(node);ruleHint(node);}const source=currentSource();if(source)$('source-meta').textContent=`${clock(source.duration_sec)} · ${source.label?t('signal'):t('normal')} · ${source.partition}`;});
$('review-theme').addEventListener('click',()=>{theme=theme==='dark'?'light':'dark';preferences();});
async function request(path,options={}){
  const response=await fetch(path,{...options,headers:{'X-API-Key':key,...options.headers},cache:'no-store'});
  if(!response.ok){
    let field=null;
    if(response.status===422){try{const body=await response.json();if(body.detail&&typeof body.detail==='object'&&body.detail.field==='reviewer')field='reviewer';}catch{}}
    const error=new Error(field==='reviewer'?t('reviewerInvalid'):response.status===409?t('conflict'):response.status===422?t('invalid'):t('error'));
    error.field=field;throw error;
  }
  return response;
}
function clock(value){const seconds=Math.max(0,Number(value)||0);return `${Math.floor(seconds/60)}:${(seconds%60).toFixed(2).padStart(5,'0')}`;}
function currentSource(){return data?.sources.find(source=>source.source_sha256===selected);}
function renderSources(){
  const filter=$('review-filter').value,list=$('source-list');list.replaceChildren();
  for(const source of data.sources){if(filter==='positive'&&!source.label||filter==='negative'&&source.label||filter==='pending'&&source.coverage_complete)continue;const button=document.createElement('button'),name=document.createElement('span'),state=document.createElement('small');button.type='button';button.setAttribute('aria-current',String(selected===source.source_sha256));name.textContent=source.video_id;state.textContent=source.coverage_complete?'100%':`${source.partition==='train'?'train':'val'}`;button.append(name,state);button.addEventListener('click',()=>void openSource(source));list.append(button);}
  $('review-count').textContent=`${data.observed_intervals??data.confirmed_intervals} ${t('observations')} · ${data.confirmed_intervals} ${t('count')}`;
}
$('review-filter').addEventListener('change',renderSources);
function addEvent(row={}){
  const node=$('review-event-template').content.firstElementChild.cloneNode(true);
  node.dataset.eventId=row.event_id||'';node.dataset.ruleId=row.rule_id||'';
  node.querySelector('legend').textContent=`${t('event')} ${$('review-events').children.length+1}`;
  for(const field of node.querySelectorAll('[data-field]')){if(row[field.dataset.field]!=null)field.value=row[field.dataset.field];if(field.type==='number')field.max=currentSource().duration_sec;}
  const typeField=node.querySelector('[data-field="type_id"]'),item=category(typeField.value)||annotationTemplate(typeField.value);if(item)typeField.value=item.labels[lang];node.querySelector('[data-target]').checked=row.annotation_status==='confirmed';typeField.addEventListener('input',()=>ruleHint(node));ruleHint(node);
  templateOptions(node);templateButton(node);
  node.querySelector('[data-template]').addEventListener('change',()=>applyTemplate(node));
  node.querySelector('[data-replace-template]').addEventListener('click',()=>applyTemplate(node,true));
  node.querySelector('[data-field="observed_action"]').addEventListener('input',()=>templateButton(node));
  typeField.addEventListener('input',()=>{node.dataset.ruleId='';node.querySelector('[data-target]').checked=false;$('review-confirm').checked=false;node.querySelector('[data-template]').value='';templateButton(node);});
  node.querySelector('[data-target]').addEventListener('change',()=>{if(!node.querySelector('[data-target]').checked)node.dataset.ruleId='';});
  for(const button of node.querySelectorAll('[data-mark]'))button.addEventListener('click',()=>{node.querySelector(`[data-field="${button.dataset.mark}"]`).value=$('review-video').currentTime.toFixed(2);dirty=true;});
  node.querySelector('[data-remove]').addEventListener('click',()=>{node.remove();dirty=true;});
  node.addEventListener('input',()=>{dirty=true;});localize(node);$('review-events').append(node);
}
$('add-review-event').addEventListener('click',()=>{addEvent();dirty=true;});
async function openSource(source){
  if(saving)return;
  if(dirty&&!confirm(t('discard')))return;
  const token=++selection;selected=source.source_sha256;dirty=false;$('review-events').replaceChildren();$('review-confirm').checked=false;$('review-full').checked=source.coverage_complete;
  $('source-title').textContent=source.video_id;$('source-meta').textContent=`${clock(source.duration_sec)} · ${source.label?t('signal'):t('normal')} · ${source.partition}`;renderSources();
  for(const row of source.events.filter(row=>['draft','confirmed'].includes(row.annotation_status)&&row.start_sec!==null)){addEvent(row);if(row.reviewer&&!$('reviewer-name').value)$('reviewer-name').value=row.reviewer;}
  $('add-review-event').disabled=!source.label;$('review-points').replaceChildren();
  for(const point of source.points_sec){const button=document.createElement('button');button.type='button';button.textContent=clock(point);button.addEventListener('click',()=>{$('review-video').currentTime=Math.max(0,point-2);});$('review-points').append(button);}
  $('review-video').pause();$('review-video').removeAttribute('src');$('review-video').load();if(blob){URL.revokeObjectURL(blob);blob=null;}notice(t('loading'));
  try{const response=await request(`/api/v1/dataset-review/${selected}/video`);const video=await response.blob();if(token!==selection)return;blob=URL.createObjectURL(video);$('review-video').src=blob;notice(t('loaded'));}catch(error){if(token===selection)notice(error.message);}
}
$('review-video').addEventListener('timeupdate',()=>{$('review-time').textContent=clock($('review-video').currentTime);});
$('review-video').addEventListener('error',()=>{if(blob)notice(t('videoError'));});
$('review-slow').addEventListener('click',()=>{const video=$('review-video');video.playbackRate=video.playbackRate===.5?1:.5;});
$('review-access').addEventListener('submit',async event=>{event.preventDefault();if(dirty&&!confirm(t('discard')))return;key=$('review-key').value.trim();try{data=await(await request('/api/v1/dataset-review')).json();try{violationCatalog=(await(await request('/api/v1/violation-types')).json()).classes;ruleRegistry=(await(await request('/api/v1/rules')).json()).rules;typeOptions();}catch{/* Observation entry remains available without the optional category catalog. */}$('review-workspace').hidden=false;dirty=false;renderSources();const source=currentSource()||data.sources.find(s=>s.label&&!s.coverage_complete)||data.sources[0];if(source)await openSource(source);}catch(error){notice(error.message);}});
$('source-review').addEventListener('input',()=>{dirty=true;});
$('reviewer-name').addEventListener('input',()=>{$('reviewer-name').removeAttribute('aria-invalid');$('reviewer-name').removeAttribute('aria-errormessage');});
$('source-review').addEventListener('submit',async event=>{
  event.preventDefault();const source=currentSource();if(saving||!source||!$('review-confirm').checked)return;const events=[...$('review-events').children].map(node=>{const row=Object.fromEntries([...node.querySelectorAll('[data-field]')].map(field=>[field.dataset.field,field.type==='number'?Number(field.value):field.value.trim()]));row.type_id=category(row.type_id)?.id||annotationTemplate(row.type_id)?.type_id||row.type_id;row.target_confirmed=node.querySelector('[data-target]').checked;if(node.dataset.eventId)row.event_id=node.dataset.eventId;if(node.dataset.ruleId)row.rule_id=node.dataset.ruleId;return row;});
  const submittedSource=selected,submittedSelection=selection;setSaving(true);
  try{data=await(await request(`/api/v1/dataset-review/${submittedSource}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:data.revision,reviewer:$('reviewer-name').value.trim(),coverage_complete:$('review-full').checked,events})})).json();if(selected!==submittedSource||selection!==submittedSelection)return;dirty=false;$('review-confirm').checked=false;const saved=currentSource().events.filter(row=>['draft','confirmed'].includes(row.annotation_status)&&row.start_sec!==null);for(const [index,node] of [...$('review-events').children].entries())node.dataset.eventId=saved[index]?.event_id||'';notice(t('saved'));}catch(error){notice(error.message);if(error.field==='reviewer'){$('reviewer-name').setAttribute('aria-invalid','true');$('reviewer-name').setAttribute('aria-errormessage','review-notice');}}finally{setSaving(false);renderSources();if($('reviewer-name').getAttribute('aria-invalid')==='true')$('reviewer-name').focus();}
});
window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
