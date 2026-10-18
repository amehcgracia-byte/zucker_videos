/* Clay is a real, reviewable pose animation. ChatGPT creates the six poses;
 * the local compositor holds/cues them. This does not claim a video-model API. */
(() => {
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const MODES={bubble:'Thought bubble',full:'Full clay shot',pip:'Small clay window',blend:'Blend over me'};
  let dialog,context,job,timer;
  const find=s=>dialog.querySelector(s);
  function status(text){find('[data-clay-status]').textContent=text;}
  function ensure(){
    if(dialog)return;
    dialog=document.createElement('dialog');dialog.id='showClayEditor';dialog.setAttribute('aria-label','Clay story');
    dialog.innerHTML=`<header><div><strong>Clay story · Simón</strong><small>A new story, the same child-built puppet and green cap.</small></div><button type="button" data-clay-close aria-label="Close clay story">×</button></header>
      <div class="show-clay-editor-body"><blockquote data-clay-words></blockquote>
      <label>Display <select data-clay-mode>${Object.entries(MODES).map(([value,title])=>`<option value="${value}">${title}</option>`).join('')}</select></label>
      <section data-clay-existing hidden><p>Drag the Clay story bar or either edge in the scene editor. The story plays once, fitting its gestures and held pauses to the bar.</p>
      <label>Size <input type="range" data-clay-size min=".1" max="1" step=".01"></label>
      <label>Across <input type="range" data-clay-x min="0" max="1" step=".01"></label>
      <label>Down <input type="range" data-clay-y min="0" max="1" step=".01"></label>
      <label>Opacity <input type="range" data-clay-opacity min="0" max="1" step=".01"></label>
      <button type="button" data-clay-save>Apply settings & preview in scene</button></section>
      <details data-clay-create open><summary>Create a new story</summary><label>Your story<textarea data-clay-story rows="4" maxlength="4000" placeholder="Simón is on a sofa with a friend. He tries to stand up, changes his mind and sits down again. Hold the reaction before the final gesture."></textarea></label>
      <p class="show-hint">ChatGPT creates six sequential clay poses using Simón’s identity reference. This app animates them with held pauses, not a repeating GIF. Uses your signed-in Codex allowance. Review the silent draft before adding it; the scene keeps your original voice.</p>
      <button type="button" class="primary" data-clay-generate>Create pose animation with ChatGPT</button>
      <details><summary>Or reuse the countryside sample</summary><p>This reuses the approved animation; it does not generate a new story.</p><button type="button" data-clay-sample>Use existing sample</button></details></details>
      <p role="status" data-clay-status></p><video data-clay-draft controls playsinline hidden style="width:100%;max-height:45vh"></video>
      <button type="button" class="primary" data-clay-approve hidden>Approve & add to scene</button></div>`;
    document.body.append(dialog);
    find('[data-clay-close]').onclick=()=>dialog.close();dialog.addEventListener('close',()=>{clearTimeout(timer);find('video').pause();});
    find('[data-clay-generate]').onclick=async()=>{
      const direction=find('[data-clay-story]').value.trim();if(!direction){status('Describe where Simón is, who is there, and what happens.');return;}
      find('[data-clay-generate]').disabled=true;find('[data-clay-approve]').hidden=true;find('[data-clay-draft]').hidden=true;
      try {job=await api('/show/ai/images',{method:'POST',body:JSON.stringify({scene_id:context.scene.id,direction,purpose:'clay',engine:'chatgpt',style:'crayon',selected_text:context.picked.text,
        replace_clay_id:context.placement?.id,at:context.picked.start,duration:Math.min(30,context.picked.end-context.picked.start)})});localStorage.setItem('zucker.show.clay-draft',job.id);await poll();}
      catch(e){status(e.message);find('[data-clay-generate]').disabled=false;}
    };
    find('[data-clay-approve]').onclick=async()=>{
      find('[data-clay-approve]').disabled=true;
      try {const data=await api('/show/clay/drafts/'+job.id+'/apply',{method:'POST',body:JSON.stringify({mode:find('[data-clay-mode]').value})});await ShowReview.clayChanged(data);dialog.close();}
      catch(e){status(e.message);}finally{find('[data-clay-approve]').disabled=false;}
    };
    find('[data-clay-sample]').onclick=async()=>{
      find('[data-clay-sample]').disabled=true;
      try {const data=await api('/show/clay/sample',{method:'POST',body:JSON.stringify({project_id:ShowReview.projectId(),scene_id:context.scene.id,at:context.picked.start,end:context.picked.end,mode:find('[data-clay-mode]').value})});await ShowReview.clayChanged(data);dialog.close();}
      catch(e){status(e.message);}finally{find('[data-clay-sample]').disabled=false;}
    };
    find('[data-clay-save]').onclick=async()=>{
      find('[data-clay-save]').disabled=true;
      try {const data=await api('/show/clay');const p=data.placements.find(p=>p.id===context.placement.id);if(!p)throw Error('This clay placement was removed.');
        p.mode=find('[data-clay-mode]').value;for(const key of ['size','x','y','opacity'])p[key]=Number(find('[data-clay-'+key+']').value);
        const updated=await api('/show/clay/placements',{method:'PUT',body:JSON.stringify({revision:data.revision,placements:data.placements})});await ShowReview.clayChanged(updated);dialog.close();}
      catch(e){status(e.message);}finally{find('[data-clay-save]').disabled=false;}
    };
  }
  async function poll(){
    clearTimeout(timer);if(!job||!dialog.open)return;
    try {job=await api('/show/ai/images/'+job.id);status(job.error||job.message||job.status);
      const busy=['queued','generating'].includes(job.status);find('[data-clay-generate]').disabled=busy;
      if(job.status==='ready'){
        const video=find('[data-clay-draft]');video.src='/api/v1/show/clay/drafts/'+job.id+'/preview';video.hidden=false;find('[data-clay-approve]').hidden=false;
      }else if(busy)timer=setTimeout(poll,2000);
    }catch(e){status(e.message);find('[data-clay-generate]').disabled=false;}
  }
  async function open(scene,picked=null,placement=null){
    ensure();clearTimeout(timer);job=null;
    const data=await api('/show/clay');
    const source=placement?.edited||[];
    picked ||= {start:Math.max(scene.start,source[0]?.[0]??scene.start),end:Math.min(scene.end,source.at(-1)?.[1]??scene.start+6),text:scene.text};
    if(!placement)picked={...picked,end:Math.min(scene.end,Math.max(picked.end,picked.start+6))};
    context={scene,picked,placement};find('[data-clay-words]').textContent=picked.text||'Selected scene';
    find('[data-clay-existing]').hidden=!placement;find('[data-clay-create]').open=!placement;
    find('[data-clay-mode]').value=placement?.mode||'bubble';find('[data-clay-story]').value='';find('[data-clay-generate]').disabled=false;
    find('[data-clay-approve]').hidden=true;find('[data-clay-approve]').textContent=placement?'Approve & replace this story':'Approve & add to scene';find('[data-clay-draft]').hidden=true;
    if(placement)for(const key of ['size','x','y','opacity'])find('[data-clay-'+key+']').value=placement[key];
    status(placement?'Change its layout here. Its timing is editable on the timeline.':'Preview first, then approve. Creating a draft does not change the scene.');
    if(!dialog.open)dialog.showModal();
    // A closed dialog never loses a finished or running generation.
    const last=localStorage.getItem('zucker.show.clay-draft');
    if(last){try{const prior=await api('/show/ai/images/'+last);if(prior.purpose==='clay'&&prior.scene_id===scene.id&&(prior.replace_clay_id||null)===(placement?.id||null)&&Math.abs(prior.at-picked.start)<.1){job=prior;find('[data-clay-story]').value=prior.direction;await poll();}}catch(_) {}}
  }
  window.ShowClayEditor={open};
})();
