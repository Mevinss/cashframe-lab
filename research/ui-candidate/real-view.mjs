import {createApi,ApiError,decodeJpeg,validateMov} from './api.mjs';
import {RealSession,correlateEvents} from './real.mjs';
import {formatTime} from './demo.mjs';
const $=id=>document.getElementById(id);
const el=(tag,cls,text)=>{const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=text;return node;};
const stages={validating:'Проверка видео',loading_model:'Загрузка локальной модели',extracting_features:'Извлечение признаков',scoring_windows:'Оценка временных окон',refining_intervals:'Уточнение интервалов',evidence:'Подготовка evidence',done:'Анализ завершён',accepted:'Задание принято'};
const safeError=error=>error instanceof ApiError?error.message:new ApiError().message;
export function installRealView(){
  const api=createApi();let active=false,providedKey='',view='inspection',selected=0,events=[],duration=null,lastResult=null,mediaUrl=null;
  let frameUrls=[],downloadUrls=[],previewBusy=false,saveBusy=false,connectBusy=false,downloadBusy=false,epoch=0;
  const session=new RealSession(api,{onChange:render});
  function revokeFrames(){for(const url of frameUrls)URL.revokeObjectURL(url);frameUrls=[];}
  function cleanup(){epoch++;api.clear();providedKey='';$('r-key').value='';$('r-file').value='';$('r-annotation').reset();$('r-video').pause();$('r-video').removeAttribute('src');$('r-video').load();if(mediaUrl)URL.revokeObjectURL(mediaUrl);mediaUrl=null;revokeFrames();for(const url of downloadUrls)URL.revokeObjectURL(url);downloadUrls=[];$('r-frame-dialog').close();$('r-full-frame').removeAttribute('src');lastResult=null;events=[];selected=0;duration=null;previewBusy=false;saveBusy=false;connectBusy=false;downloadBusy=false;
    for(const id of ['r-evidence','r-events','r-markers','r-annotations','r-limitations'])$(id).replaceChildren();
    for(const id of ['r-description','r-score','r-metadata','r-optional','r-annotation-info','r-download-info','r-file-info'])$(id).textContent='';
    $('r-preview-info').textContent='Превью не загружено.';$('r-readiness').textContent='Готовность не проверялась. Readiness выполняет пробное вычисление модели.';
  }
  function keyFromInput(){const value=$('r-key').value.trim();if(value){api.setKey(value);providedKey=value;$('r-key').value='';}if(!providedKey)throw new ApiError(401);return providedKey;}
  function controls(){
    $('r-start').disabled=!active||session.busy||connectBusy;$('r-connect').disabled=!active||session.busy||connectBusy;
    $('r-stop').disabled=!session.busy;$('r-resume').disabled=!session.data.jobId||session.busy||session.data.status==='done';
    $('r-save').disabled=saveBusy||!duration||session.data.status!=='done';$('r-preview').disabled=previewBusy||session.data.status!=='done';
    $('r-pdf').disabled=$('r-passport').disabled=downloadBusy||session.data.status!=='done';
  }
  function navigate(name){view=name;render(session.data);}
  function render(data){
    if(!active)return;
    const done=Boolean(data.result&&data.status==='done');
    $('r-result').hidden=!(done&&view==='inspection');$('r-annotation-section').hidden=!(done&&view==='inspection'&&duration);
    $('r-reports').hidden=view!=='reports';$('r-about').hidden=view!=='method';$('r-upload').hidden=!(view==='upload'||view==='inspection'&&!done);
    $('r-title').textContent={inspection:'Проверка записи',upload:'Новая запись',reports:'Отчёт и паспорт',method:'Реальное подключение'}[view]||'Проверка записи';
    const titles={idle:'Реальный backend · готовность не проверена',readiness:'Проверка готовности backend',uploading:'Передача MOV локальному backend',processing:'Обработка реального задания',done:data.result?.verdict==='violation'?'Есть модельные сигналы для проверки':'Модель не выделила сигналы',failed:'Анализ завершился ошибкой',error:'Запрос не выполнен',stopped:'Ожидание остановлено','not-ready':'Backend не готов'};
    $('r-status').textContent=titles[data.status]||'Нет результата';
    $('r-status-copy').textContent=data.error||(done?'Это вывод модели, не подтверждённое нарушение. Отсутствие сигнала не гарантирует отсутствие события.':stages[data.review?.stage]||'Выбор файла не отправляет его. Для загрузки нажмите отдельную кнопку.');
    $('r-message').className=`verdict ${data.error?'error':'neutral'}`;
    if(data.readiness===false)$('r-readiness').textContent='503: runtime не готов. Возможны отсутствующие модели, FFmpeg или серверный ключ. API не раскрывает точную причину.';
    if(data.result!==lastResult){
      lastResult=data.result;selected=0;revokeFrames();$('r-annotation').reset();
      if(mediaUrl){URL.revokeObjectURL(mediaUrl);mediaUrl=null;}$('r-video').pause();$('r-video').removeAttribute('src');$('r-video').load();
      if(data.result){
        events=correlateEvents(data.result,data.review);duration=Number.isFinite(data.review?.review?.duration_sec)&&data.review.review.duration_sec>0?data.review.review.duration_sec:null;
        $('r-seek').max=duration||1;$('r-seek').disabled=!duration;$('r-seek').value=0;$('r-clock').textContent=duration?`${formatTime(0)} / ${formatTime(duration)}`:'Длительность недоступна';
        $('r-timeline-note').textContent=duration?'Интервалы доступны только при однозначном соответствии review и результата.':'Review недоступен: интервалы и разметка не могут быть достоверно определены.';
        renderEvents();renderSelected();
      }
    }
    $('r-annotation-section').hidden=!(done&&view==='inspection'&&duration);
    $('r-optional').textContent=data.optionalErrors.join(' ');
    const review=data.review?.review;
    $('r-metadata').textContent=review?`Длительность: ${duration?formatTime(duration):'неизвестна'} · Статус локализации и версии берутся из review · ${stages[data.review.stage]||'Этап backend получен'}`:'Метаданные review недоступны.';
    $('r-limitations').replaceChildren();for(const text of review?.limitations||[])if(typeof text==='string')$('r-limitations').append(el('li','',text));
    const previousRule=$('r-rule').value;$('r-rule').replaceChildren(el('option','','Не установлена'));$('r-rule').firstChild.value='';
    for(const rule of data.rules){if(typeof rule.id==='string'&&rule.verified===true){const option=el('option','',`${rule.section||''} ${rule.title||rule.id}`);option.value=rule.id;$('r-rule').append(option);}}
    if([...$('r-rule').options].some(option=>option.value===previousRule))$('r-rule').value=previousRule;
    renderAnnotations();controls();
  }
  function renderEvents(){
    $('r-events').replaceChildren();$('r-markers').replaceChildren();$('r-count').textContent=events.length;
    if(!events.length)$('r-events').append(el('p','empty-events','Backend вернул пустой список событий. Новые события не создаются.'));
    events.forEach((event,index)=>{
      const button=el('button','event-card');button.type='button';button.setAttribute('aria-pressed',String(index===selected));button.append(el('span','mono',event.localized?formatTime(event.timecode_sec):'Локализация не подтверждена'),el('strong','',event.type),el('span','event-meta','Автоматическое предложение · не подтверждено человеком'));
      button.addEventListener('click',()=>{selected=index;renderEvents();renderSelected();if(event.localized)seek(event.timecode_sec);$('r-events').children[index]?.focus({preventScroll:true});});$('r-events').append(button);
      if(event.localized&&duration){const marker=el('button','interval-marker');marker.type='button';marker.style.left=`${event.meta.start_sec/duration*100}%`;marker.style.width=`${(event.meta.end_sec-event.meta.start_sec)/duration*100}%`;marker.setAttribute('aria-label',`${event.type}, ${formatTime(event.timecode_sec)}, модельный кандидат`);marker.addEventListener('click',()=>{selected=index;renderEvents();renderSelected();seek(event.timecode_sec);});$('r-markers').append(marker);}
    });
  }
  function seek(time){if(!duration)return;const value=Math.max(0,Math.min(duration,time));$('r-seek').value=value;$('r-clock').textContent=`${formatTime(value)} / ${formatTime(duration)}`;if(mediaUrl)$('r-video').currentTime=value;}
  function renderSelected(){
    // Never carry a previous event's human confirmation or observation to another.
    $('r-annotation').reset();$('r-annotation-info').textContent='Новая форма выбранного события. Несохранённые поля предыдущего события не переносятся.';
    const event=events[selected];$('r-detail').hidden=!event;revokeFrames();$('r-evidence').replaceChildren();if(!event)return;
    $('r-detail-title').textContent=event.type;$('r-localization').textContent=event.localized?`${formatTime(event.meta.start_sec)}–${formatTime(event.meta.end_sec)} · кандидат`:'Время действия не установлено';$('r-description').textContent=event.description;
    $('r-score').textContent=Number.isFinite(event.meta?.score)?String(event.meta.score):'Нет достоверно связанной оценки';
    event.evidence_frames.forEach(frame=>{
      const figure=el('div'),button=el('button','evidence-button'),image=el('img');button.type='button';
      try{const url=URL.createObjectURL(decodeJpeg(frame.image_base64));frameUrls.push(url);image.src=url;image.alt=`JPEG backend, ${formatTime(frame.timestamp_sec)}`;image.addEventListener('error',()=>{image.remove();button.replaceChildren(el('p','fine-print','JPEG не удалось отобразить'));});button.append(image);button.addEventListener('click',()=>{$('r-full-frame').src=url;$('r-frame-caption').textContent=`${event.localized?'Кадр backend':'Контекст, не локализация'} · ${formatTime(frame.timestamp_sec)}`;$('r-frame-dialog').showModal();seek(frame.timestamp_sec);});}
      catch{button.disabled=true;button.append(el('p','fine-print','Некорректный JPEG в ответе backend'));}
      figure.append(button,el('p','fine-print',`${event.localized?'Timestamp кадра':'Контекст видео'} · ${formatTime(frame.timestamp_sec)}`));$('r-evidence').append(figure);
    });
    // Never invent an action interval for unresolved/ambiguous proposals.
    if(event.localized){$('r-start-sec').value=event.meta.start_sec;$('r-end-sec').value=event.meta.end_sec;}else{$('r-start-sec').value='';$('r-end-sec').value='';}
    $('r-start-sec').max=$('r-end-sec').max=duration||'';
  }
  function renderAnnotations(){
    const root=$('r-annotations');root.replaceChildren();
    if(!session.data.annotations.length)root.append(el('p','fine-print','Серверная разметка отсутствует или ещё не получена.'));
    for(const row of session.data.annotations){const entry=el('div','saved-review');entry.append(el('strong','',row.confirmed===true?'Наблюдение подтверждено человеком':'Черновик'),el('p','',`${typeof row.type==='string'?row.type:''}: ${typeof row.observed_fact==='string'?row.observed_fact:''}`),el('p','fine-print',row.rule_status==='confirmed'?'Применимость нормы подтверждена проверяющим; это не автоматический юридический вывод.':row.rule_id?'Норма предложена; применимость не подтверждена.':'Нормативное основание не установлено.'));root.append(entry);}
  }
  $('r-connect').addEventListener('click',async()=>{if(connectBusy||!active)return;const gen=epoch;connectBusy=true;controls();try{api.setKey(keyFromInput());$('r-readiness').textContent='Ожидание readiness (до 70 секунд)…';const ready=await api.checkReadiness();if(gen!==epoch)return;$('r-readiness').textContent=ready.ready?'Runtime готов. Эта проверка не подтверждает правильность введённого ключа; он проверяется защищённым API.':'503: runtime не готов. Возможны отсутствующие артефакты, FFmpeg или серверный ключ; точная причина API не раскрывается.';}catch(error){if(gen===epoch)$('r-readiness').textContent=safeError(error);}finally{if(gen===epoch){connectBusy=false;controls();}}});
  $('r-disconnect').addEventListener('click',()=>{cleanup();session.switchMode('real');});
  $('r-file').addEventListener('change',()=>{try{const file=validateMov($('r-file').files[0]);$('r-file-info').textContent=`MOV · ${(file.size/1024/1024).toFixed(1)} МиБ. Ещё не отправлен.`;}catch(error){$('r-file-info').textContent=safeError(error);}});
  $('r-start').addEventListener('click',async()=>{if(!active||session.busy)return;try{const key=keyFromInput(),file=validateMov($('r-file').files[0]);await session.start(file,key,{explicit:true});}catch(error){$('r-status-copy').textContent=safeError(error);}controls();});
  $('r-stop').addEventListener('click',()=>session.stop());
  $('r-resume').addEventListener('click',async()=>{try{await session.resume(keyFromInput());}catch(error){$('r-status-copy').textContent=safeError(error);}controls();});
  $('r-seek').addEventListener('input',()=>seek(Number($('r-seek').value)));
  $('r-video').addEventListener('timeupdate',()=>{if(duration){$('r-seek').value=$('r-video').currentTime;$('r-clock').textContent=`${formatTime($('r-video').currentTime)} / ${formatTime(duration)}`;}});
  $('r-video').addEventListener('error',()=>{if(active&&mediaUrl)$('r-preview-info').textContent='Браузер не воспроизводит превью. Сохранённый результат анализа не потерян.';});
  $('r-preview').addEventListener('click',async()=>{if(previewBusy||!active||session.data.status!=='done')return;const gen=epoch;previewBusy=true;controls();try{const data=await api.getVideoPreview(session.data.jobId);if(gen!==epoch)return;if(mediaUrl)URL.revokeObjectURL(mediaUrl);mediaUrl=URL.createObjectURL(data.blob);$('r-video').src=mediaUrl;$('r-preview-info').textContent='Защищённое превью получено из локального backend.';}catch(error){if(gen===epoch)$('r-preview-info').textContent=`${safeError(error)} Результат анализа сохранён.`;}finally{if(gen===epoch){previewBusy=false;controls();}}});
  $('r-annotation').addEventListener('submit',async event=>{event.preventDefault();if(!active||saveBusy||session.data.status!=='done')return;
    const input={start_sec:$('r-start-sec').value===''?NaN:Number($('r-start-sec').value),end_sec:$('r-end-sec').value===''?NaN:Number($('r-end-sec').value),reviewer:$('r-reviewer').value.trim(),observed_fact:$('r-fact').value.trim(),type:$('r-type').value.trim(),visibility:$('r-visibility').value,rule_id:$('r-rule').value||null,rule_status:$('r-rule-status').value,rule_applicability_confirmed:$('r-applicability').checked,confirmed:$('r-confirmed').checked,origin:'human'};
    const gen=epoch;saveBusy=true;controls();try{const response=await api.saveAnnotation(session.data.jobId,input,{duration,intent:'save-annotation'});if(gen!==epoch)return;$('r-annotation-info').textContent=response.confirmed?'Человеческое наблюдение сохранено. Модельный ответ не изменён.':'Черновик сохранён. Нарушение не подтверждено.';try{const annotations=await api.getAnnotations(session.data.jobId);if(gen!==epoch)return;session.data.annotations=annotations.annotations;renderAnnotations();}catch{if(gen===epoch)$('r-annotation-info').textContent+=' Не удалось обновить список; POST повторно не отправлялся.';}}
    catch(error){if(gen===epoch)$('r-annotation-info').textContent=safeError(error)+' Если соединение прервалось после отправки, сначала обновите список: запись могла сохраниться.';}
    finally{if(gen===epoch){saveBusy=false;controls();}}
  });
  $('r-refresh-annotations').addEventListener('click',async()=>{const gen=epoch;try{const data=await api.getAnnotations(session.data.jobId);if(gen!==epoch)return;session.data.annotations=data.annotations;renderAnnotations();}catch(error){if(gen===epoch)$('r-annotation-info').textContent=safeError(error);}});
  async function download(kind){
    if(!active||downloadBusy||session.data.status!=='done')return;const gen=epoch;downloadBusy=true;controls();
    try{const data=await(kind==='pdf'?api.downloadReport(session.data.jobId):api.downloadPassport(session.data.jobId));if(gen!==epoch)return;
      const hash=kind==='pdf'?data.reportSha256:data.passportSha256;let integrity='Контрольная сумма сервером не предоставлена.';
      if(hash){if(!/^[a-f0-9]{64}$/i.test(hash)||!globalThis.crypto?.subtle)throw new ApiError(0,'schema');const digest=await crypto.subtle.digest('SHA-256',await data.blob.arrayBuffer());const actual=[...new Uint8Array(digest)].map(x=>x.toString(16).padStart(2,'0')).join('');if(actual!==hash.toLowerCase())throw new ApiError(0,'schema');integrity=`SHA-256 проверена: ${hash}`;}
      if(gen!==epoch)return;const url=URL.createObjectURL(data.blob);downloadUrls.push(url);const link=el('a');link.href=url;link.download=`video-audit-${session.data.jobId}.${kind==='pdf'?'pdf':'json'}`;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);$('r-download-info').textContent=`Документ получен из backend. ${integrity} Выводы требуют проверки.`;
    }catch(error){if(gen===epoch)$('r-download-info').textContent=safeError(error);}finally{if(gen===epoch){downloadBusy=false;controls();}}
  }
  $('r-pdf').addEventListener('click',()=>download('pdf'));$('r-passport').addEventListener('click',()=>download('passport'));
  $('r-close-frame').addEventListener('click',()=>$('r-frame-dialog').close());
  window.addEventListener('pagehide',()=>{cleanup();session.switchMode('demo');});
  return {navigate,switchMode(mode){active=false;cleanup();session.switchMode(mode);active=mode==='real';$('real-root').hidden=!active;if(active)render(session.data);}};
}
