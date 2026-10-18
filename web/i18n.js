import {translations} from './translations.js';
import {ru as sourceCopy} from './i18n.ru.js';

let currentLanguage='ru',currentTheme='light';
const staticNodes=[],staticAttributes=[];
export const language=()=>currentLanguage;
export const hasTranslation=source=>Boolean(translations[source]);
export const locale=()=>({ru:'ru-RU',kk:'kk-KZ',en:'en-GB'}[currentLanguage]);
export function tr(source,...values){
  const tagged=Array.isArray(source),key=tagged?source.map((part,index)=>part+(index<values.length?`{${index}}`:'')).join(''):source;
  const translated=currentLanguage==='ru'?key:(translations[key]?.[currentLanguage]||translations[key]?.en||key);
  return tagged?translated.replace(/\{(\d+)\}/g,(_,index)=>String(values[Number(index)]??'')):translated;
}
const proxies=new WeakMap();
function localize(value){if(typeof value==='string')return tr(value);if(!value||typeof value!=='object')return value;if(!proxies.has(value))proxies.set(value,new Proxy(value,{get:(target,key)=>localize(Reflect.get(target,key))}));return proxies.get(value);}
export const ru=localize(sourceCopy);
function rememberStatic(){
  if(!document.createTreeWalker)return;
  const walker=document.createTreeWalker(document.documentElement,4);let node;
  while((node=walker.nextNode())){const text=node.data.trim();if(translations[text])staticNodes.push({node,source:text,last:node.data,prefix:node.data.match(/^\s*/)[0],suffix:node.data.match(/\s*$/)[0]});}
  for(const node of document.querySelectorAll('[aria-label],[aria-roledescription],[title],[placeholder],[alt]'))for(const name of ['aria-label','aria-roledescription','title','placeholder','alt']){const source=node.getAttribute(name);if(translations[source])staticAttributes.push({node,name,source,last:source});}
}
export function applyStaticTranslations(){
  for(const item of staticNodes)if(item.node.data===item.last){item.last=item.prefix+tr(item.source)+item.suffix;item.node.data=item.last;}
  for(const item of staticAttributes)if(item.node.getAttribute(item.name)===item.last){item.last=tr(item.source);item.node.setAttribute(item.name,item.last);}
  document.documentElement.lang=currentLanguage;document.title=tr('Видеоаудит');
}
function save(name,value){try{globalThis.localStorage?.setItem(`videoaudit.${name}`,value);}catch{/* Preferences are optional. */}}
export function setLanguage(value){if(!['ru','kk','en'].includes(value))return;currentLanguage=value;save('language',value);applyStaticTranslations();}
function setTheme(value){currentTheme=value;document.documentElement.dataset.theme=value;save('theme',value);const button=document.getElementById('theme-toggle');button.setAttribute('aria-label',tr(value==='dark'?'Включить светлую тему':'Включить тёмную тему'));button.setAttribute('aria-pressed',String(value==='dark'));}
export function initPreferences(onChange){
  rememberStatic();
  try{const savedLanguage=globalThis.localStorage?.getItem('videoaudit.language'),savedTheme=globalThis.localStorage?.getItem('videoaudit.theme');if(['ru','kk','en'].includes(savedLanguage))currentLanguage=savedLanguage;if(['light','dark'].includes(savedTheme))currentTheme=savedTheme;}catch{/* Private mode may deny storage. */}
  applyStaticTranslations();setTheme(currentTheme);
  const select=document.getElementById('language-select');select.value=currentLanguage;
  select.addEventListener('change',()=>{setLanguage(select.value);setTheme(currentTheme);onChange();});
  document.getElementById('theme-toggle').addEventListener('click',()=>setTheme(currentTheme==='dark'?'light':'dark'));
}
