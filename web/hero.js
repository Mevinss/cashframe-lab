export function boundedPosition(x,y,width,height){return {x:Math.max(-Math.min(55,width*.17),Math.min(Math.min(55,width*.17),x)),y:Math.max(-Math.min(32,height*.12),Math.min(Math.min(32,height*.12),y))};}
import {tr} from './i18n.js';

export function initHeroCarousel(){
  const banner=document.getElementById('hero-carousel'),track=document.getElementById('hero-slide-track'),controls=document.getElementById('hero-carousel-controls'),pause=document.getElementById('hero-pause');
  if(!banner||!track||!controls||!pause)return null;
  const slides=[0,1,2].map(index=>document.getElementById(`hero-slide-${index}`)),dots=[0,1,2].map(index=>document.getElementById(`hero-dot-${index}`));
  const reduced=globalThis.matchMedia?.('(prefers-reduced-motion: reduce)'),blocks=new Set();
  let index=0,paused=Boolean(reduced?.matches),timer=null;
  function schedule(){clearTimeout(timer);timer=null;if(!paused&&!blocks.size)timer=setTimeout(()=>select(index+1),8000);}
  function refresh(){
    track.style.setProperty('--slide-index',index);
    slides.forEach((slide,i)=>{slide.inert=i!==index;slide.setAttribute('aria-hidden',String(i!==index));dots[i].setAttribute('aria-current',String(i===index));});
    const label=tr(paused?'Продолжить смену слайдов':'Приостановить слайды');pause.setAttribute('aria-label',label);pause.title=label;pause.setAttribute('aria-pressed',String(paused));pause.dataset.paused=String(paused);
  }
  function select(next){index=(next+slides.length)%slides.length;refresh();schedule();}
  function block(reason,value){if(value)blocks.add(reason);else blocks.delete(reason);schedule();}
  document.getElementById('hero-prev').addEventListener('click',()=>select(index-1));
  document.getElementById('hero-next').addEventListener('click',()=>select(index+1));
  dots.forEach((dot,i)=>dot.addEventListener('click',()=>select(i)));
  pause.addEventListener('click',()=>{paused=!paused;refresh();schedule();});
  controls.addEventListener('keydown',event=>{const next={ArrowLeft:index-1,ArrowRight:index+1,Home:0,End:2}[event.key];if(next!==undefined){event.preventDefault();select(next);}});
  banner.addEventListener('pointerenter',()=>block('hover',true));banner.addEventListener('pointerleave',()=>block('hover',false));
  banner.addEventListener('focusin',()=>block('focus',true));banner.addEventListener('focusout',event=>block('focus',banner.contains(event.relatedTarget)));
  document.addEventListener('visibilitychange',()=>block('hidden',document.hidden));
  reduced?.addEventListener?.('change',event=>{if(event.matches){paused=true;refresh();schedule();}});
  if(globalThis.IntersectionObserver){const observer=new IntersectionObserver(entries=>block('offscreen',!entries[0].isIntersecting));observer.observe(banner);}
  block('hidden',Boolean(document.hidden));refresh();
  return {block,refresh};
}

export function initHero({onDragChange=()=>{}}={}){
  const stage=document.getElementById('hero-stage'),object=document.getElementById('hero-object'),reset=document.getElementById('hero-reset');
  if(!stage||!object||!reset)return;
  const reduced=globalThis.matchMedia?.('(prefers-reduced-motion: reduce)');
  let position={x:0,y:0},pointer=null;
  function paint(tiltX=-8,tiltY=-12){object.style.setProperty('--drag-x',`${position.x}px`);object.style.setProperty('--drag-y',`${position.y}px`);object.style.setProperty('--tilt-x',`${reduced?.matches?0:tiltX}deg`);object.style.setProperty('--tilt-y',`${reduced?.matches?0:tiltY}deg`);}
  function move(x,y){const box=stage.getBoundingClientRect();position=boundedPosition(x,y,box.width,box.height);paint();}
  function restore(){position={x:0,y:0};paint();}
  object.addEventListener('pointerdown',event=>{if(event.button!==0)return;onDragChange(true);pointer={id:event.pointerId,x:event.clientX,y:event.clientY,origin:{...position},touch:event.pointerType==='touch',active:event.pointerType!=='touch'};if(pointer.active){object.setPointerCapture(event.pointerId);object.classList.add('is-dragging');}});
  object.addEventListener('pointermove',event=>{
    if(pointer&&event.pointerId===pointer.id){const dx=event.clientX-pointer.x,dy=event.clientY-pointer.y;
      if(!pointer.active){if(Math.abs(dy)>Math.abs(dx)&&Math.abs(dy)>6){pointer=null;onDragChange(false);return;}if(Math.abs(dx)<6)return;pointer.active=true;object.setPointerCapture(event.pointerId);object.classList.add('is-dragging');}
      move(pointer.origin.x+dx,pointer.origin.y+dy);return;
    }
    if(event.pointerType==='touch'||reduced?.matches)return;
    const box=stage.getBoundingClientRect();paint(-8-(event.clientY-box.top-box.height/2)/32,-12+(event.clientX-box.left-box.width/2)/25);
  });
  function release(){pointer=null;onDragChange(false);object.classList.remove('is-dragging');paint();}
  object.addEventListener('pointerup',release);object.addEventListener('pointercancel',release);object.addEventListener('lostpointercapture',release);object.addEventListener('pointerleave',()=>{if(!pointer)paint();});
  object.addEventListener('keydown',event=>{const step=event.shiftKey?20:8;const directions={ArrowLeft:[-step,0],ArrowRight:[step,0],ArrowUp:[0,-step],ArrowDown:[0,step]};if(directions[event.key]){event.preventDefault();const [x,y]=directions[event.key];move(position.x+x,position.y+y);}if(event.key==='Home'||event.key==='Escape'){event.preventDefault();restore();}});
  reset.addEventListener('click',restore);reduced?.addEventListener?.('change',()=>paint());paint();
}
