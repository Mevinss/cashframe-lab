export function initHeroCarousel(){
  const banner=document.getElementById('hero-carousel'),track=document.getElementById('hero-slide-track'),controls=document.getElementById('hero-carousel-controls');
  if(!banner||!track||!controls)return null;
  const slides=[0,1,2].map(index=>document.getElementById(`hero-slide-${index}`)),dots=[0,1,2].map(index=>document.getElementById(`hero-dot-${index}`));
  const blocks=new Set();
  let index=0,timer=null;
  function schedule(){clearTimeout(timer);timer=null;if(!blocks.size)timer=setTimeout(()=>select(index+1),5000);}
  function refresh(){
    track.style.setProperty('--slide-index',index);
    slides.forEach((slide,i)=>{slide.inert=i!==index;slide.setAttribute('aria-hidden',String(i!==index));dots[i].setAttribute('aria-current',String(i===index));});
  }
  function select(next){index=(next+slides.length)%slides.length;refresh();schedule();}
  function block(reason,value){if(value)blocks.add(reason);else blocks.delete(reason);schedule();}
  document.getElementById('hero-prev').addEventListener('click',()=>select(index-1));
  document.getElementById('hero-next').addEventListener('click',()=>select(index+1));
  dots.forEach((dot,i)=>dot.addEventListener('click',()=>select(i)));
  controls.addEventListener('keydown',event=>{const next={ArrowLeft:index-1,ArrowRight:index+1,Home:0,End:2}[event.key];if(next!==undefined){event.preventDefault();select(next);}});
  document.addEventListener('visibilitychange',()=>block('hidden',document.hidden));
  if(globalThis.IntersectionObserver){const observer=new IntersectionObserver(entries=>block('offscreen',!entries[0].isIntersecting));observer.observe(banner);}
  block('hidden',Boolean(document.hidden));refresh();
  return {block,refresh};
}
