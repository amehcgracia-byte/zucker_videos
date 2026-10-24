/* Shared pointer controls for image and clip framing; no media is rewritten. */
window.ShowMediaPreview = (() => {
  function localPoint(box,p,x,y){
    const a=(p.rotate||0)*Math.PI/180,dx=x-box.left-box.w/2,dy=y-box.top-box.h/2;
    return [(dx*Math.cos(a)+dy*Math.sin(a))/((p.flip?-1:1)*(p.stretch_x??1))+box.w/2,
      (-dx*Math.sin(a)+dy*Math.cos(a))/(p.stretch??1)+box.h/2];
  }
  function hitTest(box,p,x,y){
    const [lx,ly]=localPoint(box,p,x,y),c=p.crop||[0,0,0,0];
    const l=c[0]*box.w,r=(1-c[2])*box.w,t=c[1]*box.h,b=(1-c[3])*box.h;
    const mx=9/Math.abs(p.stretch_x??1),my=9/Math.abs(p.stretch??1);
    if(lx<l-mx||lx>r+mx||ly<t-my||ly>b+my)return null;
    return {local:[lx,ly],resize:Math.abs(lx-r)<mx*1.5&&Math.abs(ly-b)<my*1.5,
      edge:Math.abs(lx-l)<mx?'left':Math.abs(lx-r)<mx?'right':Math.abs(ly-t)<my?'top':Math.abs(ly-b)<my?'bottom':null};
  }
  function mount(host, {src, video=false, centered=false, read, change, onError}) {
    const canvas=document.createElement('canvas');canvas.width=640;canvas.height=360;
    canvas.style.cssText='width:100%;aspect-ratio:16/9;display:block;background:#202823;touch-action:none;cursor:grab';
    canvas.setAttribute('aria-label','Media preview: drag to move, edges to crop, bottom-right corner to resize, Command or Control to rotate');
    host.append(canvas);
    const ctx=canvas.getContext('2d'),media=document.createElement(video?'video':'img');
    media.src=src;media.crossOrigin='anonymous';if(video){media.muted=true;media.loop=true;media.playsInline=true;media.preload='auto';}
    const controls=document.createElement('button');controls.type='button';controls.textContent='Play / pause clip';
    if(video){host.append(controls);controls.onclick=()=>media.paused?media.play().catch(e=>onError(e.message)):media.pause();}
    const buffer=document.createElement('canvas');buffer.width=640;buffer.height=360;const bc=buffer.getContext('2d');
    let stopped=false,raf=null,box=null,drag=null,ready=false;
    function draw(){
      if(stopped)return;
      ctx.clearRect(0,0,640,360);ctx.fillStyle='#26372c';ctx.fillRect(0,0,640,360);
      if(ready){
        const p=read(),iw=media.videoWidth||media.naturalWidth,ih=media.videoHeight||media.naturalHeight;
        let source=media,w=p.scale/100*640,h=w*ih/iw,left=p.x/100*640,top=p.y/100*360;
        if(video){
          bc.clearRect(0,0,640,360);
          const cover=Math.max(640/iw,360/ih),cw=iw*cover,ch=ih*cover;
          bc.filter=iw<ih?'blur(18px)':'none';bc.drawImage(media,(640-cw)/2,(360-ch)/2,cw,ch);bc.filter='none';
          if(iw<ih){const contain=Math.min(640/iw,360/ih);bc.drawImage(media,(640-iw*contain)/2,(360-ih*contain)/2,iw*contain,ih*contain);}
          source=buffer;w=p.scale/100*640;h=p.scale/100*360;left=(640-w)/2+p.x/100*640;top=360-h+p.y/100*360;
        }
        if(centered){left-=w/2;top-=h/2;}
        box={left,top,w,h};ctx.save();ctx.translate(left+w/2,top+h/2);ctx.rotate(p.rotate*Math.PI/180);ctx.scale((p.flip?-1:1)*(p.stretch_x??1),p.stretch??1);ctx.globalAlpha=p.opacity??1;
        const c=p.crop;ctx.beginPath();ctx.rect(-w/2+c[0]*w,-h/2+c[1]*h,w*(1-c[0]-c[2]),h*(1-c[1]-c[3]));ctx.clip();ctx.drawImage(source,-w/2,-h/2,w,h);ctx.restore();
        if(!video||media.paused){
          ctx.save();ctx.translate(left+w/2,top+h/2);ctx.rotate(p.rotate*Math.PI/180);ctx.scale((p.flip?-1:1)*(p.stretch_x??1),p.stretch??1);
          ctx.strokeStyle='#ffda77';ctx.lineWidth=2;ctx.strokeRect(-w/2+c[0]*w,-h/2+c[1]*h,w*(1-c[0]-c[2]),h*(1-c[1]-c[3]));
          ctx.fillStyle='#ffda77';ctx.fillRect(w/2-c[2]*w-6,h/2-c[3]*h-6,12,12);ctx.restore();
        }
      }
      if(video&&!media.paused)raf=requestAnimationFrame(draw);
    }
    media.addEventListener(video?'loadeddata':'load',()=>{ready=true;draw();});media.onerror=()=>onError('This media cannot be previewed. The original is still kept.');
    if(video){media.onplay=draw;media.onpause=draw;}
    function point(e){const r=canvas.getBoundingClientRect();return [(e.clientX-r.left)*640/r.width,(e.clientY-r.top)*360/r.height];}
    canvas.onpointerdown=e=>{if(!ready||!box)return;const [x,y]=point(e),b=box,p=read(),hit=hitTest(b,p,x,y);if(!hit)return;
      media.pause?.();e.preventDefault();canvas.setPointerCapture(e.pointerId);
      drag={x,y,box:{...b},initial:p,...hit,modified:e.ctrlKey||e.metaKey};canvas.style.cursor='grabbing';};
    canvas.onpointermove=e=>{const [x,y]=point(e);
      if(!drag){const hit=ready&&box?hitTest(box,read(),x,y):null;canvas.style.cursor=!hit?'default':hit.resize?'nwse-resize':hit.edge?(['left','right'].includes(hit.edge)?'ew-resize':'ns-resize'):'grab';return;}
      const d=drag,dx=x-d.x,dy=y-d.y,p={...d.initial,crop:[...d.initial.crop]},local=localPoint(d.box,d.initial,x,y),lx=local[0]-d.local[0],ly=local[1]-d.local[1];
      if(d.modified&&d.edge&&!d.resize){Object.assign(p,ShowGeometry.change(p,d.edge,lx/d.box.w,ly/d.box.h,true));}
      else if(d.modified){p.rotate=d.initial.rotate+(Math.atan2(y-d.box.top-d.box.h/2,x-d.box.left-d.box.w/2)-Math.atan2(d.y-d.box.top-d.box.h/2,d.x-d.box.left-d.box.w/2))*180/Math.PI;}
      else if(d.resize)p.scale=Math.max(.1,d.initial.scale*(1+lx/d.box.w));
      else if(d.edge){const updated=ShowGeometry.change({crop:p.crop},d.edge,lx/d.box.w,ly/d.box.h,false);p.crop=updated.crop;}
      else {p.x=d.initial.x+dx/640*100;p.y=d.initial.y+dy/360*100;}
      change(p);draw();};
    canvas.onpointerup=canvas.onpointercancel=()=>{drag=null;canvas.style.cursor='grab';};
    return {draw,close(){stopped=true;cancelAnimationFrame(raf);media.pause?.();media.removeAttribute('src');media.load?.();}};
  }
  return {mount,localPoint,hitTest};
})();
