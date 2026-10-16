// Captions are a live layer: moving/resizing them never needs another MP4.
(() => {
  const shift = (time, spans=[]) => time + spans.reduce((n,[at,d])=>n+(at<=time?d:0),0);
  function unshift(time, spans=[]) {
    let delay=0;
    for (const [at,d] of [...spans].sort((a,b)=>a[0]-b[0])) {
      if(time<at+delay)return time-delay;
      if(time<at+delay+d)return at;
      delay+=d;
    }
    return time-delay;
  }
  window.ShowCaptionClock={shift,unshift};
  let node, context, dragging=false;
  function ensure(){
    if(node){if(!node.isConnected)document.querySelector('#showStageLayer').append(node);return;}
    node=document.createElement('div');node.className='show-live-caption';node.hidden=true;node.tabIndex=0;node.setAttribute('aria-label','Editable caption: drag to move, corner to resize');
    node.innerHTML='<span data-caption-text></span><span class="picture-corner" title="Resize caption"></span><button type="button" data-letter-caption>Draw this phrase…</button>';
    document.querySelector('#showStageLayer').append(node);
    node.onpointerdown=e=>{
      if(e.target.closest('button')||!context?.cue)return;
      e.preventDefault();e.stopPropagation();node.focus();node.setPointerCapture(e.pointerId);
      const ctx=context,cue=ctx.cue,style={...cue.style_override};
      const box=document.querySelector('#showStage').getBoundingClientRect();
      const start=[e.clientX,e.clientY],resize=e.target.classList.contains('picture-corner');
      const wasPlaying=!ctx.video.paused;ctx.video.pause();dragging=true;
      let changes={};
      node.onpointermove=ev=>{
        const dx=(ev.clientX-start[0])/box.width,dy=(ev.clientY-start[1])/box.height;
        changes=resize?{size:Math.max(12,Math.min(400,(style.size||76)+dx*1080))}
          :{horizontal:Math.max(0,Math.min(100,(style.horizontal??50)+dx*100)),vertical:Math.max(0,Math.min(100,(style.vertical??74)+dy*100))};
        cue.style_override={...style,...changes};paint(cue,box.height);
      };
      const finish=async cancelled=>{
        node.onpointermove=null;node.onpointerup=null;node.onpointercancel=null;dragging=false;
        try{
          if(cancelled)cue.style_override=style;
          else if(Object.keys(changes).length){
            const result=await api(`/show/captions/${cue.id}`,{method:'PATCH',body:JSON.stringify({project_id:ctx.projectId,revision:ctx.revision,changes})});
            Object.assign(cue,result.cue);window.ShowReview.captionSaved(result);
          }
        }catch(error){cue.style_override=style;showToast(`Caption not saved: ${error.message}`,true);}
        paint(cue,box.height);if(wasPlaying)ctx.video.play().catch(()=>{});
      };
      node.onpointerup=()=>finish(false);node.onpointercancel=()=>finish(true);
    };
    node.querySelector('button').onclick=()=>{
      if(!context?.cue)return;
      const {cue,scene}=context,text=cue.lines.join(' ');
      ShowEinstein.open(scene,{text,start:Math.max(scene.start,cue.start),end:Math.min(scene.end,cue.end)},
        'Rough hand-painted protest placard lettering, readable but uneven. No printed type.',null,
        {purpose:'lettering',letteringText:text,replacesCaption:cue.id,style:'marker',imageCenter:[(cue.style_override.horizontal??50)/100,(cue.style_override.vertical??74)/100],imageWidth:Math.max(.02,Math.min(2,node.getBoundingClientRect().width/document.querySelector('#showStage').getBoundingClientRect().width))});
    };
  }
  function paint(cue,height){
    const p=cue.style_override||{};
    node.style.left=`${p.horizontal??50}%`;node.style.top=`${p.vertical??74}%`;
    node.querySelector('[data-caption-text]').style.fontSize=`${Math.max(8,(p.size||76)*height/1080)}px`;
    const text=node.querySelector('[data-caption-text]'),value=cue.lines.join(' ');
    if(text.textContent!==value)text.textContent=value;
  }
  function sync(ctx){
    ensure();if(dragging)return;
    const cue=ctx.scene.captions?.find(c=>c.start<=ctx.at&&ctx.at<c.end);
    node.hidden=!cue||ctx.saved||ctx.video.hidden||ctx.replaced?.includes(cue.id);
    context={...ctx,cue};if(!node.hidden)paint(cue,document.querySelector('#showStage').getBoundingClientRect().height);
  }
  window.ShowLiveCaptions={sync};
})();
