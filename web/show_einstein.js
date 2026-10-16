// A real image-provider dialog. Status messages describe actual jobs, not fake AI replies.
(() => {
  let dialog, context, timer, job;
  const esc = text => String(text ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function status(text) { dialog.querySelector('[data-ai-status]').textContent=text; }
  function readyControls() {
    const busy=job && ['queued','generating'].includes(job.status);
    dialog.querySelector('[data-ai-generate]').disabled=!!busy;
    dialog.querySelector('[data-ai-claude]').disabled=!!busy;
    dialog.querySelector('[data-ai-apply]').hidden=job?.status!=='ready';
    dialog.querySelector('[data-ai-refine-label]').hidden=job?.status!=='ready';
  }
  async function poll() {
    clearTimeout(timer);
    if(!job)return;
    try {
      job=await api(`/show/ai/images/${job.id}`);
      status(job.error || job.message || job.status);
      readyControls();
      const image=dialog.querySelector('[data-ai-result]');
      image.hidden=job.status!=='ready'; if(job.url)image.src=job.url;
      if(['queued','generating'].includes(job.status))timer=setTimeout(poll,2000);
    } catch(error){status(error.message);}
  }
  function ensure() {
    if(dialog)return;
    dialog=document.createElement('dialog');dialog.id='showEinsteinDialog';dialog.setAttribute('aria-label','Create with Einstein');
    dialog.innerHTML=`<header><img src="/tutorial-einstein.png" alt="Einstein"><div><h2>Create with Einstein</h2><p>What should happen when you say this?</p></div><button type="button" data-ai-close aria-label="Close Einstein">×</button></header>
      <blockquote data-ai-phrase></blockquote><label data-ai-history-label hidden>Previous image versions<select data-ai-history aria-label="Previous image versions"></select></label><p class="show-hint">Explain the joke, who appears, and how it should look. I will make an image first; you choose whether to use it. Then select the words where it should appear, and drag it on the video to position it.</p>
      <label>Your idea<textarea rows="4" maxlength="4000" data-ai-direction aria-label="Your idea for Einstein" placeholder="An adult version of me slouching lazily on a sofa, rough wax-crayon lines, on the left…"></textarea></label>
      <div class="show-fields"><label>Drawing style<select data-ai-style><option value="crayon">Rough wax crayons</option><option value="pencil">Messy pencil</option><option value="marker">Clumsy felt-tip pen</option></select></label><label>Position<select data-ai-position><option>left</option><option>right</option><option>top-left</option><option>top-right</option><option>bottom-left</option><option>bottom-right</option></select></label></div>
      <label><input type="checkbox" data-ai-face> Use my face from this moment</label><p class="show-hint">If selected, one video frame is sent to ChatGPT as an identity reference. Your video is not uploaded.</p>
      <label data-ai-refine-label hidden><input type="checkbox" data-ai-refine checked> Keep this image as a reference when I ask for changes</label><div class="show-actions"><button type="button" data-ai-generate class="primary">Generate with ChatGPT</button><button type="button" data-ai-claude class="primary">Draw with Claude</button><button type="button" data-ai-local>Try the local painter instead</button></div>
      <p class="show-hint">ChatGPT uses Codex signed in on this computer and its usage limits. Claude uses your Claude Code subscription: it draws a rough crayon doodle stroke by stroke, and it can draw itself on the video. The local painter is a separate model. None is ever substituted silently for another.</p>
      <p role="status" data-ai-status></p><img data-ai-result hidden alt="Generated image to review before using it" class="ai-result">
      <div class="show-actions"><button type="button" data-ai-apply class="primary" hidden>Use image &amp; choose words</button></div>`;
    document.body.append(dialog);
    dialog.querySelector('[data-ai-close]').onclick=()=>dialog.close();
    dialog.addEventListener('close',()=>clearTimeout(timer));
    dialog.addEventListener('click',e=>{if(e.target===dialog){const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)dialog.close();}});
    dialog.querySelector('[data-ai-history]').onchange=async event=>{
      if(!event.target.value)return;
      job={id:event.target.value};localStorage.setItem('zucker.show.last-ai-image',job.id);await poll();
      dialog.querySelector('[data-ai-direction]').value=job.direction;
      dialog.querySelector('[data-ai-style]').value=job.style;
      dialog.querySelector('[data-ai-position]').value=job.position;
      dialog.querySelector('[data-ai-face]').checked=job.use_face;
    };
    const generate=async engine=>{
      const direction=dialog.querySelector('[data-ai-direction]').value.trim();
      if(!direction){status('Describe your idea first.');return;}
      dialog.querySelector('[data-ai-generate]').disabled=true;dialog.querySelector('[data-ai-claude]').disabled=true;
      status(engine==='claude'?'Asking Claude to draw it (about half a minute)…':'Sending this image request to ChatGPT…');
      try {
        job=await api('/show/ai/images',{method:'POST',body:JSON.stringify({scene_id:context.scene.id,selected_text:context.picked.text,at:context.picked.start,
          duration:Math.max(.1,context.picked.end-context.picked.start),direction,style:dialog.querySelector('[data-ai-style]').value,
          position:dialog.querySelector('[data-ai-position]').value,use_face:dialog.querySelector('[data-ai-face]').checked,replace_id:context.replaceId,engine,previous_id:job?.status==='ready' && dialog.querySelector('[data-ai-refine]').checked ? job.id : null})});
        localStorage.setItem('zucker.show.last-ai-image',job.id); await poll();
      } catch(error){status(error.message);dialog.querySelector('[data-ai-generate]').disabled=false;dialog.querySelector('[data-ai-claude]').disabled=false;}
    };
    dialog.querySelector('[data-ai-generate]').onclick=()=>generate('chatgpt');
    dialog.querySelector('[data-ai-claude]').onclick=()=>generate('claude');
    dialog.querySelector('[data-ai-apply]').onclick=async()=>{
      const button=dialog.querySelector('[data-ai-apply]');button.disabled=true;
      try {const item=await api(`/show/ai/images/${job.id}/apply`,{method:'POST',body:'{}'});await ShowReview.imageApplied(item,job.scene_id);dialog.close();}
      catch(error){status(error.message);}finally{button.disabled=false;}
    };
    dialog.querySelector('[data-ai-local]').onclick=async()=>{
      if(dialog.querySelector('[data-ai-face]').checked){status('The local painter cannot use your face reference. Use ChatGPT for this request.');return;}
      const direction=dialog.querySelector('[data-ai-direction]').value.trim();if(!direction){status('Describe your idea first.');return;}
      await ShowReview.localImageIdea(context.scene.id,context.picked,direction,dialog.querySelector('[data-ai-style]').value);dialog.close();
    };
  }
  async function open(scene,picked,idea='',replaceId=null,settings={}) {
    ensure();clearTimeout(timer);context={scene,picked,replaceId};job=null;
    dialog.querySelector('[data-ai-phrase]').textContent=picked.text || scene.text || scene.name;
    dialog.querySelector('[data-ai-direction]').value=idea;
    dialog.querySelector('[data-ai-style]').value=settings.style || 'crayon';
    dialog.querySelector('[data-ai-position]').value=settings.position || 'left';
    dialog.querySelector('[data-ai-face]').checked=/my face|mi cara|version of me/i.test(idea);
    dialog.querySelector('[data-ai-result]').hidden=true;
    readyControls();status('Describe the image you want. Generating does not replace anything until you use the result.');
    dialog.showModal();dialog.querySelector('[data-ai-direction]').focus();
    try {
      const history=(await api(`/show/ai/images?scene_id=${encodeURIComponent(scene.id)}`)).items.filter(item=>(item.replace_id||null)===(replaceId||null) && Math.abs(item.at-picked.start)<.1);
      dialog.querySelector('[data-ai-history-label]').hidden=!history.length;
      dialog.querySelector('[data-ai-history]').innerHTML='<option value="">Choose a previous result…</option>'+history.map((item,index)=>`<option value="${item.id}">${history.length-index} · ${esc(item.status)} · ${esc(item.direction.slice(0,90))}</option>`).join('');
      if(history.length && !localStorage.getItem('zucker.show.last-ai-image'))localStorage.setItem('zucker.show.last-ai-image',history[0].id);
    } catch(_){}
    const lastId=localStorage.getItem('zucker.show.last-ai-image');
    if(lastId) {
      try {
        const previous=await api(`/show/ai/images/${lastId}`);
        if(previous.scene_id===scene.id && (previous.replace_id || null)===(replaceId || null) && Math.abs(previous.at-picked.start)<.1) {
          job=previous;
          dialog.querySelector('[data-ai-direction]').value=previous.direction;
          dialog.querySelector('[data-ai-style]').value=previous.style;
          dialog.querySelector('[data-ai-position]').value=previous.position;
          dialog.querySelector('[data-ai-face]').checked=previous.use_face;
          await poll();
        }
      } catch(_) { /* A request from another project is not imported. */ }
    }
  }
  window.ShowEinstein={open};
})();
