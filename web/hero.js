export function boundedPosition(x,y,width,height){return {x:Math.max(-Math.min(55,width*.17),Math.min(Math.min(55,width*.17),x)),y:Math.max(-Math.min(32,height*.12),Math.min(Math.min(32,height*.12),y))};}
export function initHero(){
  const stage=document.getElementById('hero-stage'),object=document.getElementById('hero-object'),reset=document.getElementById('hero-reset');
  if(!stage||!object||!reset)return;
  const reduced=globalThis.matchMedia?.('(prefers-reduced-motion: reduce)');
  let position={x:0,y:0},pointer=null;
  function paint(tiltX=-8,tiltY=-12){object.style.setProperty('--drag-x',`${position.x}px`);object.style.setProperty('--drag-y',`${position.y}px`);object.style.setProperty('--tilt-x',`${reduced?.matches?0:tiltX}deg`);object.style.setProperty('--tilt-y',`${reduced?.matches?0:tiltY}deg`);}
  function move(x,y){const box=stage.getBoundingClientRect();position=boundedPosition(x,y,box.width,box.height);paint();}
  function restore(){position={x:0,y:0};paint();}
  object.addEventListener('pointerdown',event=>{if(event.button!==0)return;pointer={id:event.pointerId,x:event.clientX,y:event.clientY,origin:{...position},touch:event.pointerType==='touch',active:event.pointerType!=='touch'};if(pointer.active){object.setPointerCapture(event.pointerId);object.classList.add('is-dragging');}});
  object.addEventListener('pointermove',event=>{
    if(pointer&&event.pointerId===pointer.id){const dx=event.clientX-pointer.x,dy=event.clientY-pointer.y;
      if(!pointer.active){if(Math.abs(dy)>Math.abs(dx)&&Math.abs(dy)>6){pointer=null;return;}if(Math.abs(dx)<6)return;pointer.active=true;object.setPointerCapture(event.pointerId);object.classList.add('is-dragging');}
      move(pointer.origin.x+dx,pointer.origin.y+dy);return;
    }
    if(event.pointerType==='touch'||reduced?.matches)return;
    const box=stage.getBoundingClientRect();paint(-8-(event.clientY-box.top-box.height/2)/32,-12+(event.clientX-box.left-box.width/2)/25);
  });
  function release(){pointer=null;object.classList.remove('is-dragging');paint();}
  object.addEventListener('pointerup',release);object.addEventListener('pointercancel',release);object.addEventListener('lostpointercapture',release);object.addEventListener('pointerleave',()=>{if(!pointer)paint();});
  object.addEventListener('keydown',event=>{const step=event.shiftKey?20:8;const directions={ArrowLeft:[-step,0],ArrowRight:[step,0],ArrowUp:[0,-step],ArrowDown:[0,step]};if(directions[event.key]){event.preventDefault();const [x,y]=directions[event.key];move(position.x+x,position.y+y);}if(event.key==='Home'||event.key==='Escape'){event.preventDefault();restore();}});
  reset.addEventListener('click',restore);reduced?.addEventListener?.('change',()=>paint());paint();
}
