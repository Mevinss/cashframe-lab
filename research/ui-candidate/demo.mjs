// Synthetic state only. No API, storage, media reads, or model calls.
export const DURATION = 120;
export const STAGES = ['Проверка формата', 'Извлечение признаков', 'Поиск сигналов', 'Подготовка результата'];
export const SCENARIOS = Object.freeze({
  positive: {title:'Есть сигналы для проверки', copy:'Положительное бинарное решение модели не подтверждает нарушение.', symbol:'!', tone:'', verdict:'violation'},
  negative: {title:'Модель не выделила сигналы', copy:'Отрицательное решение не гарантирует отсутствие нарушения. Требуется независимая проверка.', symbol:'—', tone:'neutral', verdict:'no_violation'},
  unresolved: {title:'Сигнал уровня видео · время не установлено', copy:'Локализация не определена. Отметки и кадры показывают только контекст, а не момент действия.', symbol:'?', tone:'', verdict:'violation'},
  pending: {title:'Учебная обработка', copy:'Демонстрация этапов. Вычисления модели не выполняются; этапы переключаются вручную.', symbol:'◷', tone:'neutral', verdict:null},
  failed: {title:'Демонстрация технической ошибки', copy:'Анализ не завершён. Ошибка не означает отрицательный вердикт.', symbol:'!', tone:'error', verdict:null},
  empty: {title:'Запись не выбрана', copy:'Выберите файл локально или откройте синтетический сценарий через список состояний.', symbol:'↥', tone:'neutral', verdict:null}
});

const SAMPLES = [
  {id:'signal-01', label:'Сигнал 01', start_sec:22, end_sec:28, timecode_sec:24.5, score:.73, localization_status:'model_proposal', evidence:[21,24.5,29]},
  {id:'signal-02', label:'Сигнал 02', start_sec:64, end_sec:72, timecode_sec:67.25, score:.61, localization_status:'model_proposal', evidence:[63,67.25,73]}
];
export function eventsFor(scenario) {
  const events = scenario === 'positive' ? SAMPLES : scenario === 'unresolved'
    ? [{id:'context-01', label:'Сигнал уровня видео', start_sec:0, end_sec:DURATION, timecode_sec:0, score:.68, localization_status:'unresolved', evidence:[0,60,119.75]}] : [];
  return events.map(event => ({...event, evidence:[...event.evidence]}));
}
export function createState() {
  return {view:'inspection', scenario:'positive', selected:'signal-01', time:24.5, playing:false, stage:0, reviews:{}, drafts:{}};
}
export function chooseScenario(state, scenario) {
  if (!Object.hasOwn(SCENARIOS, scenario)) throw new Error('Неизвестный учебный сценарий');
  const events = eventsFor(scenario);
  return {...state, scenario, selected:events[0]?.id ?? null, time:events[0]?.timecode_sec ?? 0, playing:false, stage:0};
}
export function selectedEvent(state) { return eventsFor(state.scenario).find(event => event.id === state.selected) ?? null; }
export function chooseEvent(state, id) {
  const event = eventsFor(state.scenario).find(item => item.id === id);
  if (!event) throw new Error('Учебное событие отсутствует');
  return {...state, selected:id, time:event.timecode_sec, playing:false};
}
export function seek(state, value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error('Некорректное время');
  return {...state, time:Math.max(0, Math.min(DURATION, value))};
}
export function advanceStage(state) {
  if (state.scenario !== 'pending') return state;
  return state.stage + 1 === STAGES.length ? chooseScenario(state, 'positive') : {...state, stage:state.stage + 1};
}
export function reviewKey(state) { return `${state.scenario}/${state.selected}`; }
export function saveReview(state, values) {
  const event = selectedEvent(state);
  if (!event) throw new Error('Сначала выберите учебное событие');
  if (![values.start,values.end].every(x=>typeof x==='number' && Number.isFinite(x)) || values.start < 0 || values.start >= values.end || values.end > DURATION)
    throw new Error('Задайте интервал: 0 ≤ начало < конец ≤ 120 секунд');
  if (typeof values.reviewer !== 'string' || !values.reviewer.trim() || values.reviewer.length > 100 ||
      typeof values.observation !== 'string' || !values.observation.trim() || values.observation.length > 500)
    throw new Error('Заполните учебное имя проверяющего и синтетическое наблюдение');
  if (!['unknown','clear','partial'].includes(values.visibility) || typeof values.confirmed !== 'boolean')
    throw new Error('Проверьте статус учебного наблюдения');
  if (values.confirmed && values.visibility !== 'clear')
    throw new Error('Для учебного подтверждения выберите ясную видимость');
  const record = {...values, reviewer:values.reviewer.trim(), observation:values.observation.trim(),
    synthetic:true, normative_status:'unresolved', label:event.label, scenario:state.scenario};
  return {...state, reviews:{...state.reviews, [reviewKey(state)]:record}, drafts:{...state.drafts, [reviewKey(state)]:record}};
}
export function validateFileMetadata(file) {
  if (!file) return {ok:false, message:'Файл не выбран'};
  if (!/\.mov$/i.test(file.name)) return {ok:false, message:'Для демонстрации выберите файл MOV. Содержимое не читалось.'};
  if (!Number.isFinite(file.size) || file.size <= 0 || file.size > 100*1024*1024)
    return {ok:false, message:'Ожидается непустой MOV размером до 100 МиБ. Содержимое не читалось.'};
  return {ok:true, message:`MOV · ${(file.size/1024/1024).toFixed(1)} МиБ. Только выбор на устройстве; обработки и загрузки нет.`};
}
export function formatTime(value) {
  const ticks=Math.round(Math.max(0, Number.isFinite(value)?value:0)*100);
  return `${String(Math.floor(ticks/6000)).padStart(2,'0')}:${String(Math.floor(ticks/100)%60).padStart(2,'0')}.${String(ticks%100).padStart(2,'0')}`;
}
