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
    node=document.createElement('div');node.className='show-live-caption';node.hidden=true;node.tabIndex=0;node.setAttribute('aria-label','Editable caption: drag to move, corner to resize, right-click or double-click to edit its words');node.title='Drag to move · corner to resize · right-click or double-click to edit the words';
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
    node.querySelector('button').onclick=()=>{if(context?.cue)letter(context.cue,context.scene);};
    // Right-click: edit it (the words, when they were heard wrong; typeface, size, colour).
    node.addEventListener('contextmenu',event=>{
      if(!context?.cue)return;
      event.preventDefault();event.stopPropagation();
      menu(context.cue,context.scene,event.clientX,event.clientY);
    });
    node.addEventListener('dblclick',event=>{if(context?.cue&&!event.target.closest('button')){event.preventDefault();edit(context.cue);}});
  }
  function letter(cue,scene){
    const text=cue.lines.join(' '),stage=document.querySelector('#showStage').getBoundingClientRect();
    ShowEinstein.open(scene,{text,start:Math.max(scene.start,cue.start),end:Math.min(scene.end,cue.end)},
      'Rough hand-painted protest placard lettering, readable but uneven. No printed type.',null,
      {purpose:'lettering',letteringText:text,replacesCaption:cue.id,style:'marker',imageCenter:[(cue.style_override.horizontal??50)/100,(cue.style_override.vertical??74)/100],
       imageWidth:Math.max(.02,Math.min(2,(node&&!node.hidden?node.getBoundingClientRect().width:stage.width*.6)/stage.width))});
  }
  function menu(cue,scene,x,y){
    ShowMenu.open(x,y,[
      {label:'Edit this caption…',hint:'words · typeface · size',action:()=>edit(cue)},
      {label:'Draw this phrase…',action:()=>letter(cue,scene)},
      ...(cue.heard?[{label:'Back to the words heard',title:cue.heard,action:()=>save(cue,{text:''})}]:[]),
    ],{host:document.querySelector('#showStage')?.closest('dialog[open]')||document.body});
  }

  // ---- The caption editor: words, typeface, size, colour ----------------------------------
  const FONTS={'Arial Bold':['Arial',700],'Arial Black':['"Arial Black"',900],'Impact':['Impact',400],'Georgia':['Georgia',700],
    'Verdana':['Verdana',700],'Trebuchet MS':['"Trebuchet MS"',700],'Courier New':['"Courier New"',700],'Comic Sans MS':['"Comic Sans MS"',700]};
  const COLOURS=[['#ffffff','White'],['#ffe066','Yellow'],['#7fe0ff','Sky'],['#ff8fb1','Pink'],['#a6f28b','Green'],['#111111','Black']];
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let editor=null;
  function fontStyle(element,p){
    const [family,weight]=FONTS[p.font||'Arial Bold']||FONTS['Arial Bold'];
    element.style.fontFamily=`${family},sans-serif`;element.style.fontWeight=weight;
    element.style.fontStyle=p.italic?'italic':'normal';element.style.color=p.color||'#ffffff';
  }
  function edit(cue){
    editor?.remove();
    const dialog=editor=document.createElement('dialog');dialog.id='showCaptionEditor';dialog.setAttribute('aria-label','Edit caption');
    const p=cue.style_override||{},text=cue.lines.join(' ');
    dialog.innerHTML=`<form method="dialog"><h3>Edit this caption</h3>
      <label>Words<textarea data-cap-text rows="2" maxlength="240">${esc(text)}</textarea></label>
      ${cue.heard?`<small>Heard: “${esc(cue.heard)}” <button type="button" data-cap-heard>Use what was heard</button></small>`:'<small>Fix any word that was heard wrong; the caption keeps its time.</small>'}
      <div class="cap-row"><label>Typeface<select data-cap-font>${Object.keys(FONTS).map(name=>`<option ${(p.font||'Arial Bold')===name?'selected':''}>${name}</option>`).join('')}</select></label>
        <label>Size<input type="range" data-cap-size min="24" max="200" step="2" value="${Math.round(p.size||76)}"><output>${Math.round(p.size||76)}</output></label></div>
      <div class="cap-row"><span>Colour</span>${COLOURS.map(([value,name])=>`<label class="cap-colour" title="${name}"><input type="radio" name="cap-colour" value="${value}" ${(p.color||'#ffffff').toLowerCase()===value?'checked':''}><i style="background:${value}"></i></label>`).join('')}
        <label class="cap-italic"><input type="checkbox" data-cap-italic ${p.italic?'checked':''}> Italic</label></div>
      <p class="cap-sample" data-cap-sample></p>
      <div class="cap-actions"><button value="cancel">Cancel</button><button value="save" class="primary" data-cap-save>Save</button></div></form>`;
    (document.querySelector('#showStage')?.closest('dialog[open]')||document.body).append(dialog);
    const $e=selector=>dialog.querySelector(selector);
    const values=()=>({text:$e('[data-cap-text]').value.trim(),font:$e('[data-cap-font]').value,size:Number($e('[data-cap-size]').value),
      color:dialog.querySelector('input[name="cap-colour"]:checked')?.value||'#ffffff',italic:$e('[data-cap-italic]').checked});
    const sample=()=>{const v=values(),box=$e('[data-cap-sample]');box.textContent=v.text||text;fontStyle(box,v);box.style.fontSize=`${Math.max(14,Math.min(40,v.size/2.4))}px`;$e('[data-cap-size]').nextElementSibling.textContent=v.size;};
    dialog.addEventListener('input',sample);dialog.addEventListener('change',sample);sample();
    $e('[data-cap-heard]')?.addEventListener('click',()=>{$e('[data-cap-text]').value=cue.heard;sample();});
    $e('[data-cap-text]').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();$e('[data-cap-save]').click();}});
    dialog.addEventListener('close',async()=>{
      if(dialog.returnValue!=='save'){dialog.remove();return;}
      const v=values(),p=cue.style_override||{},changes={};
      if(v.text!==text)changes.text=cue.heard&&v.text===cue.heard?'':v.text;
      if(v.font!==(p.font||'Arial Bold'))changes.font=v.font;
      if(v.size!==Math.round(p.size||76))changes.size=v.size;
      if(v.color!==(p.color||'#ffffff').toLowerCase())changes.color=v.color;
      if(v.italic!==!!p.italic)changes.italic=v.italic;
      dialog.remove();
      if(Object.keys(changes).length)await save(cue,changes);
    });
    dialog.showModal();$e('[data-cap-text]').focus();$e('[data-cap-text]').select();
  }
  async function save(cue,changes){
    try{
      const result=await api(`/show/captions/${cue.id}`,{method:'PATCH',body:JSON.stringify({project_id:window.ShowReview.projectId(),revision:window.ShowReview.captionRevision(),changes})});
      Object.assign(cue,result.cue);window.ShowReview.captionSaved(result);
      if(context?.cue?.id===cue.id&&node)paint(context.cue,document.querySelector('#showStage').getBoundingClientRect().height);
      showToast(changes.text!=null?'Caption corrected: the video shows these words.':'Caption saved.');
    }catch(error){showToast(`Caption not saved: ${error.message}`,true);}
  }
  function paint(cue,height){
    const p=cue.style_override||{};
    node.style.left=`${p.horizontal??50}%`;node.style.top=`${p.vertical??74}%`;
    node.querySelector('[data-caption-text]').style.fontSize=`${Math.max(8,(p.size||76)*height/1080)}px`;
    fontStyle(node.querySelector('[data-caption-text]'),p);
    const text=node.querySelector('[data-caption-text]'),value=cue.lines.join(' ');
    if(text.textContent!==value)text.textContent=value;
  }
  function sync(ctx){
    ensure();if(dragging)return;
    const cue=ctx.scene.captions?.find(c=>c.start<=ctx.at&&ctx.at<c.end);
    node.hidden=!cue||ctx.saved||ctx.video.hidden||ctx.replaced?.includes(cue.id);
    context={...ctx,cue};if(!node.hidden)paint(cue,document.querySelector('#showStage').getBoundingClientRect().height);
  }
  window.ShowLiveCaptions={sync,menu,edit};
})();
