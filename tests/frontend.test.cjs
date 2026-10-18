const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

function app({reducedMotion=false,pageHidden=false}={}){
  const nodes=new Map(),storage=new Map(),carouselTimers=new Map();let timerId=0,onIntersection;
  function node(tag='div'){
    const classes=new Set();
    const n={tagName:tag.toUpperCase(),hidden:true,style:{setProperty(name,value){this[name]=value;}},dataset:{},children:[],value:'',listeners:{},attributes:{},textContent:'',currentTime:0,
      classList:{add(...values){values.forEach(value=>classes.add(value));},remove(...values){values.forEach(value=>classes.delete(value));},contains:value=>classes.has(value)},
      addEventListener(type,callback){this.listeners[type]=callback;},setAttribute(name,value){this.attributes[name]=String(value);},getAttribute(name){return this.attributes[name]??null;},removeAttribute(name){delete this.attributes[name];if(name==='value')this.value='';},
      replaceChildren(...items){this.children=[];this.append(...items);},append(...items){for(const item of items){item.parent=this;this.children.push(item);}},prepend(item){item.parent=this;this.children.unshift(item);},
      querySelectorAll(selector){return this.children.filter(item=>selector.split(',').some(part=>item.className?.split(' ').includes(part.replace('.',''))));},querySelector(){return null;},
      contains(item){return item===this||this.children.some(child=>child.contains(item));},focus(){},reset(){},pause(){},remove(){if(this.parent)this.parent.children=this.parent.children.filter(item=>item!==this);},click(){},
      getBoundingClientRect(){return {left:0,top:0,width:360,height:280};},setPointerCapture(){},showModal(){},close(){}};
    Object.defineProperty(n,'firstElementChild',{get(){return this.children[0];}});return n;
  }
  const document={hidden:pageHidden,listeners:{},addEventListener(type,callback){this.listeners[type]=callback;},getElementById(id){if(!nodes.has(id))nodes.set(id,node());return nodes.get(id);},createElement:node,body:node(),documentElement:node(),querySelectorAll(){return [];}};
  const context=vm.createContext({document,Response,TextDecoder,AbortController,URL,FormData,console,setTimeout:(callback,delay)=>{if(delay===8000){const id=`carousel-${++timerId}`;carouselTimers.set(id,callback);return id;}return setTimeout(callback,delay);},clearTimeout:id=>{carouselTimers.delete(id);clearTimeout(id);},setInterval,clearInterval,
    IntersectionObserver:class{constructor(callback){onIntersection=callback;}observe(){}},localStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)},matchMedia:()=>({matches:reducedMotion,addEventListener(){}}),fetch:async()=>new Response('{}')});
  const source=path=>fs.readFileSync(path,'utf8').replace(/^import[^\n]+\n/gm,'').replace(/\bexport /g,'');
  vm.runInContext(source('web/translations.js'),context);
  vm.runInContext(fs.readFileSync('web/i18n.ru.js','utf8').replace('export const ru','const sourceCopy'),context);
  vm.runInContext(source('web/i18n.js'),context);vm.runInContext(source('web/hero.js'),context);vm.runInContext(source('web/app.js'),context);
  return {context,nodes,storage,carouselTimers,setBannerVisible(value){onIntersection([{isIntersecting:value}]);},advanceCarousel(){const entry=carouselTimers.entries().next().value;assert.ok(entry,'carousel should be scheduled');carouselTimers.delete(entry[0]);entry[1]();},run:code=>vm.runInContext(code,context)};
}
function text(node){return [node.textContent,...node.children.map(text)].join(' ');}
const event=`{type:'Визуальный сигнал',timecode_sec:0,description:'unlocalized fixture',evidence_frames:[{timestamp_sec:0,image_base64:'synthetic-fixture'}]}`;
function seed(a,verdict='violation',events=event,meta="{localization_status:'unresolved',id:'event-1'}"){
  a.run(`state.jobId='fixture';state.review={filename:'synthetic.mov',review:{duration_sec:65.3,events:[${meta}]}};state.result={status:'done',verdict:'${verdict}',violations:[${events}]};request=async()=>{const error=new Error('optional unavailable');error.status=404;throw error;}`);
}

test('centisecond rounding carries into the next minute',()=>{const a=app();assert.equal(a.run('preciseTime(59.9999)'),'1:00.00');});
test('verdict follows API even when count is inconsistent',()=>{
  const a=app();seed(a,'no_violation');a.run('renderResultCopy()');assert.equal(a.nodes.get('result-title').textContent,'Нарушение не обнаружено');
  a.run("state.result={verdict:'violation',violations:[]};renderResultCopy()");assert.equal(a.nodes.get('result-title').textContent,'Обнаружены признаки возможных нарушений');
  assert.match(a.run("jobLabel({status:'done',verdict:'violation',signal_count:0,created_at:0})"),/Обнаружены признаки возможных нарушений/);
  assert.match(a.run("jobLabel({status:'done',signal_count:3,created_at:0})"),/Вердикт недоступен/);
});
test('unresolved localization never draws a zero event marker or selected interval',()=>{
  const a=app();seed(a);a.nodes.get('player').currentTime=12;a.run('renderTimeline();selectEvent(0)');
  assert.equal(a.nodes.get('timeline').children.filter(item=>item.className==='event-marker').length,0);
  assert.equal(a.nodes.get('selected-interval').hidden,true);assert.equal(a.nodes.get('player').currentTime,12);
  assert.match(a.nodes.get('detail-time').textContent,/Момент события не установлен/);
  assert.match(text(a.nodes.get('evidence')),/Контекст видео/);assert.doesNotMatch(text(a.nodes.get('evidence')),/До события|После события/);
});
test('localized events retain temporal markers, seeking and original evidence captions',()=>{
  const a=app();seed(a,'violation',"{type:'fixture',timecode_sec:10,description:'test',evidence_frames:[{timestamp_sec:9,image_base64:'a'},{timestamp_sec:10,image_base64:'b'},{timestamp_sec:11,image_base64:'c'}]}","{localization_status:'localized',start_sec:9,end_sec:11}");a.run('renderTimeline();selectEvent(0)');
  assert.equal(a.nodes.get('timeline').children.filter(item=>item.className==='event-marker').length,1);assert.equal(a.nodes.get('player').currentTime,10);
  assert.equal(a.nodes.get('selected-interval').hidden,false);assert.match(text(a.nodes.get('evidence')),/До события.*Событие.*После события/);
});
test('progress displays measured stage only and clears stale values when unknown',()=>{
  const a=app();a.run("renderProgress({scope:'stage',stage:'extracting_features',percent:62,completed:82,total:132},'accepted')");
  assert.equal(a.nodes.get('processing-progress').value,62);assert.match(a.nodes.get('progress-detail').textContent,/Анализ визуальных признаков: 62% · 82 \/ 132/);
  a.run("renderProgress({scope:'stage',percent:100,completed:132,total:132},'evidence')");assert.equal(a.nodes.get('processing-progress').value,99);
  a.run("renderProgress({scope:'stage',percent:null,completed:0,total:null},'loading_model')");assert.equal(a.nodes.get('processing-progress').value,'');assert.doesNotMatch(a.nodes.get('progress-detail').textContent,/%/);
});
test('optional review and compliance failures preserve completed jury result',async()=>{
  const a=app();await a.run(`state.jobId='fixture';request=async path=>{if(path==='/api/v1/jobs/fixture')return new Response(JSON.stringify({status:'done',verdict:'no_violation',violations:[]}));const error=new Error('optional unavailable');error.status=404;throw error;};pollJob(0)`);
  assert.equal(a.nodes.get('result-view').hidden,false);assert.equal(a.nodes.get('result-title').textContent,'Нарушение не обнаружено');
});
test('confirmed compliance is matched to selected event and exposes its primary source',()=>{
  const a=app();seed(a);a.run(`state.compliance=[{event_id:'other',status:'unresolved'},{event_id:'event-1',status:'confirmed',observed_fact:'fixture observation',type:'fixture action',reviewer:'Reviewer',message:'fixture linkage',rule:{title:'Fixture rule',document:'Fixture document',section:'2',url:'https://example.org/source',applicability:'fixture conditions',limits:'fixture limits',verified_on:'2026-10-18'}}];renderCompliance(0)`);
  const panel=a.nodes.get('rule-description');assert.match(text(panel),/Подтверждено проверяющим.*fixture observation.*Fixture rule.*fixture conditions.*fixture limits/);
  assert.equal(panel.children.find(item=>item.tagName==='A').href,'https://example.org/source');
});
test('saving annotation refreshes compliance without changing jury verdict',async()=>{
  const a=app();seed(a);a.run("state.selected=0;$('annotation-start').value=0;$('annotation-end').value=1;$('annotation-reviewer').value='Reviewer';$('annotation-type').value='action';$('annotation-fact').value='fact';$('annotation-visibility').value='clear';$('annotation-confirm').checked=true;$('annotation-applicability').checked=true;$('annotation-rule').value='rule';globalThis.paths=[];request=async(path,options)=>{paths.push(path);return new Response(JSON.stringify(path.endsWith('/annotations')?{annotations:[]}:{events:[]}));}");
  await a.nodes.get('annotation-form').listeners.submit({preventDefault(){}});assert.equal(a.run('paths.includes("/api/v1/jobs/fixture/compliance")'),true);assert.equal(a.run('state.result.verdict'),'violation');
});
test('all three languages cover verdicts, context, errors, stage measurements and dates',()=>{
  const a=app();seed(a);
  for(const lang of ['ru','kk','en']){
    a.run(`setLanguage('${lang}');renderResultCopy();renderProgress({scope:'stage',stage:'evidence',percent:50,completed:1,total:2});renderEvents();selectEvent(0)`);
    const title=a.nodes.get('result-title').textContent;assert.ok(title.length>10);
    if(lang==='en'){assert.equal(title,'Signs of possible violations detected');assert.match(a.nodes.get('detail-time').textContent,/Event timing is not established/);assert.equal(a.run('ru.errors.service_restarted'),'The service restarted during processing. Upload the video again.');}
    if(lang==='kk'){assert.equal(title,'Ықтимал бұзушылық белгілері анықталды');assert.match(a.nodes.get('progress-detail').textContent,/Бастапқы кадрларды дайындау/);assert.equal(a.run('locale()'),'kk-KZ');}
  }
});
test('every static Russian interface string and accessibility label has English and Kazakh copy',()=>{
  const a=app(),html=fs.readFileSync('web/index.html','utf8'),strings=[];
  for(const match of html.matchAll(/>([^<>]+)</g))if(/[А-Яа-яЁё]/.test(match[1]))strings.push(match[1].trim());
  for(const match of html.matchAll(/(?:aria-label|aria-roledescription|title|placeholder|alt)="([^"]+)"/g))if(/[А-Яа-яЁё]/.test(match[1]))strings.push(match[1]);
  for(const source of strings){assert.ok(a.run(`Boolean(translations[${JSON.stringify(source)}]?.en)`),source);assert.ok(a.run(`Boolean(translations[${JSON.stringify(source)}]?.kk)`),source);}
});
test('settings persist only language and theme, never an access key',async()=>{
  const a=app();a.nodes.get('api-key').value='sensitive-fixture-key';a.context.fetch=async()=>new Response(JSON.stringify({retention_hours:24,available:0,processing:0,done_with_signals:0,failed:0,average_processing_sec:null,recent:[],jobs:[]}));
  await a.nodes.get('access-form').listeners.submit({preventDefault(){}});a.run("setLanguage('en')");a.nodes.get('theme-toggle').listeners.click();assert.deepEqual([...a.storage.keys()].sort(),['videoaudit.language','videoaudit.theme']);assert.ok(![...a.storage.values()].includes('sensitive-fixture-key'));assert.equal(a.context.document.documentElement.dataset.theme,'dark');
});
test('decorative composition is bounded and keyboard reset works',()=>{
  const a=app();a.run('moveFixture=boundedPosition(900,-900,360,280)');assert.equal(a.run('moveFixture.x'),55);assert.equal(a.run('moveFixture.y'),-32);
  const object=a.nodes.get('hero-object');object.listeners.keydown({key:'ArrowRight',preventDefault(){}});assert.equal(object.style['--drag-x'],'8px');object.listeners.keydown({key:'Home',preventDefault(){}});assert.equal(object.style['--drag-x'],'0px');
});
test('vertical touch movement stays available for page scrolling',()=>{
  const a=app(),object=a.nodes.get('hero-object');object.listeners.pointerdown({button:0,pointerId:2,pointerType:'touch',clientX:100,clientY:100});object.listeners.pointermove({pointerId:2,pointerType:'touch',clientX:101,clientY:120});assert.equal(object.style['--drag-y'],'0px');
});

test('carousel cycles, wraps and exposes only the selected explanatory panel',()=>{
  const a=app();a.advanceCarousel();
  assert.equal(a.nodes.get('hero-slide-1').getAttribute('aria-hidden'),'false');
  assert.equal(a.nodes.get('hero-slide-0').inert,true);
  assert.equal(a.nodes.get('hero-dot-1').getAttribute('aria-current'),'true');
  a.advanceCarousel();a.advanceCarousel();assert.equal(a.nodes.get('hero-dot-0').getAttribute('aria-current'),'true');
  a.nodes.get('hero-prev').listeners.click();assert.equal(a.nodes.get('hero-dot-2').getAttribute('aria-current'),'true');
  a.nodes.get('hero-dot-1').listeners.click();assert.equal(a.nodes.get('hero-dot-1').getAttribute('aria-current'),'true');
});

test('carousel pauses independently for interaction, navigation, hidden page and explicit pause',()=>{
  const a=app(),banner=a.nodes.get('hero-carousel'),pause=a.nodes.get('hero-pause');
  banner.listeners.pointerenter();assert.equal(a.carouselTimers.size,0);
  banner.listeners.focusin();banner.listeners.pointerleave();assert.equal(a.carouselTimers.size,0);
  banner.listeners.focusout({relatedTarget:null});assert.equal(a.carouselTimers.size,1);
  pause.listeners.click();assert.equal(a.carouselTimers.size,0);assert.equal(pause.getAttribute('aria-pressed'),'true');
  a.run("screen('processing');screen('overview')");assert.equal(a.carouselTimers.size,0);
  pause.listeners.click();assert.equal(a.carouselTimers.size,1);
  a.run("screen('result')");assert.equal(a.carouselTimers.size,0);a.run("screen('overview')");assert.equal(a.carouselTimers.size,1);
  a.context.document.hidden=true;a.context.document.listeners.visibilitychange();assert.equal(a.carouselTimers.size,0);
  a.context.document.hidden=false;a.context.document.listeners.visibilitychange();assert.equal(a.carouselTimers.size,1);
});

test('carousel keyboard arrows change panels while sculpture arrows only move the object',()=>{
  const a=app(),controls=a.nodes.get('hero-carousel-controls'),object=a.nodes.get('hero-object');
  controls.listeners.keydown({key:'ArrowLeft',preventDefault(){}});assert.equal(a.nodes.get('hero-dot-2').getAttribute('aria-current'),'true');
  object.listeners.keydown({key:'ArrowRight',preventDefault(){}});assert.equal(object.style['--drag-x'],'8px');assert.equal(a.nodes.get('hero-dot-2').getAttribute('aria-current'),'true');
  controls.listeners.keydown({key:'Home',preventDefault(){}});assert.equal(a.nodes.get('hero-dot-0').getAttribute('aria-current'),'true');
});

test('carousel does not start in a hidden page or continue outside the viewport',()=>{
  const a=app({pageHidden:true});assert.equal(a.carouselTimers.size,0);
  a.context.document.hidden=false;a.context.document.listeners.visibilitychange();assert.equal(a.carouselTimers.size,1);
  a.setBannerVisible(false);assert.equal(a.carouselTimers.size,0);a.setBannerVisible(true);assert.equal(a.carouselTimers.size,1);
});

test('reduced motion starts carousel paused and explicit playback remains available',()=>{
  const a=app({reducedMotion:true});assert.equal(a.carouselTimers.size,0);
  const pause=a.nodes.get('hero-pause');assert.equal(pause.getAttribute('aria-pressed'),'true');
  pause.listeners.click();a.advanceCarousel();assert.equal(a.nodes.get('hero-dot-1').getAttribute('aria-current'),'true');
  for(const [lang,label] of [['en','Pause slides'],['kk','Слайдтарды кідірту'],['ru','Приостановить слайды']]){a.run(`setLanguage('${lang}');refreshLanguage()`);assert.equal(pause.getAttribute('aria-label'),label);}
});

test('drag suspends carousel even after the pointer leaves the banner',()=>{
  const a=app(),object=a.nodes.get('hero-object');object.listeners.pointerdown({button:0,pointerId:2,pointerType:'mouse',clientX:100,clientY:100});
  a.nodes.get('hero-carousel').listeners.pointerleave();assert.equal(a.carouselTimers.size,0);
  object.listeners.pointerup();assert.equal(a.carouselTimers.size,1);
});

test('optional action categories use the current locale without replacing a human action',async()=>{
  const a=app();a.context.document.getElementById('annotation-type').value='Reviewer supplied action';
  a.run("request=async()=>new Response(JSON.stringify({status:'taxonomy_not_trained',classes:[{id:'counting',labels:{ru:'Пересчёт',kk:'Қайта санау',en:'Counting'},candidate_rule_ids:['cash'],requirements:[],limits:[]},{id:'unknown',labels:{ru:'Действие не определено',kk:'Әрекет анықталмады',en:'Action undetermined'}}]}))");
  await a.run('loadActionTypes()');assert.equal(a.nodes.get('annotation-action-types').children[0].value,'Пересчёт');
  a.run("setLanguage('kk');refreshLanguage()");assert.equal(a.nodes.get('annotation-action-types').children[0].value,'Қайта санау');
  a.run("setLanguage('en');refreshLanguage()");assert.equal(a.nodes.get('annotation-action-types').children[0].value,'Counting');
  assert.equal(a.nodes.get('annotation-type').value,'Reviewer supplied action');
  assert.equal(a.nodes.get('annotation-action-types').children.length,1);
});

test('missing action catalog keeps arbitrary human input available',async()=>{
  const a=app();a.context.document.getElementById('annotation-type').value='Human observation';
  a.run("request=async()=>{throw new Error('not available')}");await a.run('loadActionTypes()');
  assert.equal(a.nodes.get('annotation-action-types').children.length,0);assert.equal(a.nodes.get('annotation-type').value,'Human observation');
});
test('PDF and passport downloads request the selected language',async()=>{
  const a=app();a.run("state.jobId='fixture';setLanguage('kk');globalThis.requested=[];request=async path=>{requested.push(path);return new Response('fixture');}");await a.run("downloadArtifact('pdf')");await a.run("downloadArtifact('passport')");assert.equal(a.run('requested[0]'),'/api/v1/jobs/fixture/report.pdf?language=kk');assert.equal(a.run('requested[1]'),'/api/v1/jobs/fixture/passport?language=kk');
});
test('transient GETs retry but POSTs are never automatically duplicated',async()=>{
  const a=app();let calls=0;a.context.fetch=async()=>{calls++;if(calls===1)throw Error('offline');return new Response('{}');};await a.run("request('/test',{retries:1})");assert.equal(calls,2);
  calls=0;a.context.fetch=async()=>{calls++;throw Error('offline');};await assert.rejects(a.run("request('/test',{method:'POST'})"));assert.equal(calls,1);
});
test('hung requests are aborted',async()=>{
  const a=app();a.context.fetch=(_,options)=>new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(Error('aborted'))));await assert.rejects(a.run("request('/test',{timeoutMs:10,retries:0})"));
});

test('new result opens at top while locale refresh preserves playback and scroll',async()=>{
  const a=app();seed(a,'violation',"{type:'fixture',timecode_sec:10,description:'test',evidence_frames:[{timestamp_sec:10,image_base64:'a'}]}","{localization_status:'model_proposal',start_sec:9,end_sec:11}");
  a.run("globalThis.scrollY=760;globalThis.scrollX=0;globalThis.scrollCalls=[];globalThis.scrollTo=options=>scrollCalls.push(options)");
  await a.run('renderResult()');assert.equal(a.run('scrollCalls.at(-1).top'),0);
  a.nodes.get('player').currentTime=23.4;a.run("setLanguage('en');refreshLanguage()");
  assert.equal(a.nodes.get('player').currentTime,23.4);assert.equal(a.nodes.get('seek').value,23.4);assert.equal(a.run('scrollCalls.at(-1).top'),760);
});
test('specific API validation errors remain actionable in English and Kazakh',async()=>{
  const a=app();a.context.fetch=async()=>new Response(JSON.stringify({detail:'Проверьте интервал, наблюдаемый факт и автора подтверждения.'}),{status:422});
  a.run("setLanguage('en')");await assert.rejects(a.run("request('/annotations',{method:'POST'})"),/Check the time interval, visible fact and reviewer/);
  a.run("setLanguage('kk')");await assert.rejects(a.run("request('/annotations',{method:'POST'})"),/Уақыт аралығын/);
});
test('per-job budget limitation remains available and translated',()=>{
  const a=app();seed(a);a.run("state.review.review.limitations=['Дополнительное уточнение 8 кадр/с пропущено по бюджету времени; основная выборка всего видео обработана полностью.'];setLanguage('en');renderResultCopy()");
  assert.match(text(a.nodes.get('limitations-list')),/8 fps was skipped due to the time budget/);
});
test('a different event ID never supplies a rule and human proposals are attributed accurately',()=>{
  const a=app();seed(a);a.run("state.compliance=[{event_id:'unrelated',status:'confirmed',rule:{title:'Wrong rule'}}];renderCompliance(0)");assert.doesNotMatch(text(a.nodes.get('rule-description')),/Wrong rule|Подтверждено проверяющим/);
  a.run("state.compliance=[{event_id:'event-1',status:'proposed',origin:'human',rule:null}];renderCompliance(0)");assert.match(text(a.nodes.get('rule-description')),/Предложено проверяющим/);
});
test('reduced motion suppresses pointer tilt while keyboard movement remains available',()=>{
  const a=app({reducedMotion:true}),object=a.nodes.get('hero-object');object.listeners.pointermove({pointerType:'mouse',clientX:230,clientY:120});assert.equal(object.style['--tilt-y'],'0deg');object.listeners.keydown({key:'ArrowRight',preventDefault(){}});assert.equal(object.style['--drag-x'],'8px');
});

test('a confirmed human action remains confirmed without established legal grounds',()=>{
  const a=app();seed(a);a.run("state.compliance=[{event_id:'event-1',status:'unresolved',observation_status:'confirmed',origin:'human',observed_fact:'confirmed visible action',type:'fixture action',rule:null}]");
  for(const [lang,expected] of [['ru',/Действие подтверждено проверяющим; нормативное основание не установлено/],['en',/The reviewer confirmed the action; regulatory grounds are not established/],['kk',/Әрекетті тексеруші растаған; нормативтік негіз анықталмаған/]]){
    a.run(`setLanguage('${lang}');renderCompliance(0)`);const copy=text(a.nodes.get('rule-description'));assert.match(copy,expected);assert.match(copy,/confirmed visible action/);assert.doesNotMatch(copy,/Конкретное действие и условия|specific action and rule applicability need confirmation/);assert.equal(a.nodes.get('rule-description').children.some(item=>item.tagName==='A'),false);
  }
});
test('ambiguous observation association never implies a fact or source for the selected event',()=>{
  const a=app();seed(a);a.run("state.compliance=[{event_id:'event-1',status:'unresolved',association_status:'ambiguous',observed_fact:'must not be attributed',rule:{title:'must not be linked',url:'https://example.org/ambiguous'}}]");
  for(const [lang,expected] of [['ru',/Наблюдение пересекает несколько событий; связь с конкретным событием не установлена/],['en',/observation overlaps multiple events; its link to a specific event is not established/],['kk',/Бақылау бірнеше оқиғамен қиылысады; нақты оқиғамен байланысы анықталмаған/]]){
    a.run(`setLanguage('${lang}');renderCompliance(0)`);assert.match(text(a.nodes.get('rule-description')),expected);assert.doesNotMatch(text(a.nodes.get('rule-description')),/must not be attributed|must not be linked/);assert.equal(a.nodes.get('rule-description').children.some(item=>item.tagName==='A'),false);
  }
});
