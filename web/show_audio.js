// Audio controls edit project recipes. Originals and approved visual choices stay intact.
(() => {
  let dialog,doc,owner,poll,buffer,audioContext,liveFrame=0,lastBeep='',liveDoc=null,playSession=0;
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const status=text=>{if(dialog)dialog.querySelector('[data-audio-status]').textContent=text;};
  const call=(path,options={})=>{if(options.body)options={...options,body:JSON.stringify({...JSON.parse(options.body),project_id:owner||ShowReview.projectId()})};return api('/show/audio'+path,options);};
  function ensure(){
    if(dialog)return;
    dialog=document.createElement('dialog');dialog.id='showAudioDialog';dialog.setAttribute('aria-label','Sound mixer');
    dialog.innerHTML='<header><h2>Sound mixer</h2><button type="button" data-audio-close aria-label="Close sound mixer">×</button></header><p>Keep your voice clear, mix imported clips underneath it, or let a clip speak on its own. Originals are never changed.</p><div data-audio-controls></div><p role="status" data-audio-status></p><div class="show-actions"><button type="button" data-audio-balance class="primary">Mix &amp; balance audio</button><button type="button" data-audio-noise>Analyze &amp; reduce noise</button><button type="button" data-audio-preview>Preview scene mix</button></div><p class="show-hint">Mix &amp; balance measures each source, sets separate levels, and lowers mixed clips while you speak. Use the effects preview to hear the complete mix; live source playback plays narration.</p>';
    document.body.append(dialog);
    dialog.querySelector('[data-audio-close]').onclick=()=>dialog.close();
    dialog.addEventListener('input',event=>{const row=event.target.closest('[data-audio-row]');if(row&&event.target.type==='range')row.querySelector('output').textContent=Math.round(Number(event.target.value)*100)+'%';
      if(event.target.matches('[data-narration]')){const v=document.querySelector('#showStageVideo');if(v&&ShowReview.playbackState().original)v.volume=Math.min(1,Number(event.target.value));}
    });
    dialog.addEventListener('change',event=>save(event).catch(error=>status(error.message)));
    dialog.querySelector('[data-audio-balance]').onclick=()=>job('balance');
    dialog.querySelector('[data-audio-noise]').onclick=()=>job('denoise');
    dialog.querySelector('[data-audio-preview]').onclick=async()=>{dialog.close();document.querySelector('#showReferenceDialog')?.close();try{await ShowReview.audioChanged(true);}catch(error){showToast(error.message,true);}};
  }
  function render(){
    const cfg=doc.settings,scroll=dialog.scrollTop,active=document.activeElement;
    const field=['data-narration','data-audio-censor','data-audio-denoise','data-audio-excluded','data-track-volume','data-track-mode','data-track-under'].find(name=>active?.hasAttribute?.(name));
    const track=active?.closest?.('[data-audio-track]')?.dataset.audioTrack;
    const selector=field?(track?`[data-audio-track="${CSS.escape(track)}"] `:'')+`[${field}]`:null;
    dialog.querySelector('[data-audio-controls]').innerHTML=`<section data-audio-row><label>Narration <input data-narration type="range" min="0" max="2" step=".01" value="${cfg.narration_volume}"></label><output>${Math.round(cfg.narration_volume*100)}%</output></section>
      <label><input data-audio-censor type="checkbox" ${cfg.censor?'checked':''}> Automatically censor strong language · Soft error</label><audio controls preload="metadata" src="/api/v1/show/audio/censor-sound" aria-label="Listen to the soft censorship sound"></audio>
      <small>${doc.words.length} detected word${doc.words.length===1?'':'s'}; only the word is replaced. Detection follows Whisper and may need corrections.</small>
      <details><summary>Detected words &amp; exceptions</summary><p>${doc.words.map(word=>`${esc(word.word)} · ${word.start.toFixed(2)}s${word.estimated?' (estimated)':''}`).join(', ')||'No matching words in the transcript.'}</p><label>Do not censor these words (comma separated)<input data-audio-excluded value="${esc(cfg.excluded_words.join(', '))}"></label></details>
      <label><input data-audio-denoise type="checkbox" ${cfg.denoise?'checked':''}> Use prepared background-noise reduction</label><p class="show-hint">${esc({reduced:'Clean audio prepared and cached. Originals are kept.',clean:'Sample is already quiet; no noise filter was applied.',uncertain:'No reliable quiet noise floor found; keeping the original to protect speech.',not_analyzed:'Not analyzed yet. Use Analyze & reduce noise.',off:'Noise reduction was off during analysis.',unavailable:'Noise analysis unavailable; using the original audio.'}[doc.noise.status]||doc.noise.status)}</p>
      <h3>Imported clips, music &amp; voices</h3>${doc.tracks.map(track=>`<section class="show-audio-track" data-audio-row data-audio-track="${esc(track.id)}"><strong>${esc(track.name)}</strong><small>${track.at.toFixed(2)}s · ${track.duration.toFixed(2)}s</small><label>Volume <input data-track-volume type="range" min="0" max="2" step=".01" value="${track.volume}"></label><output>${Math.round(track.volume*100)}%</output>${['clip','character'].includes(track.kind)?`<label>Sound <select data-track-mode><option value="own" ${track.mode==='own'?'selected':''}>Own sound · pause narration</option><option value="mix" ${track.mode==='mix'?'selected':''}>Mix with narration</option><option value="mute" ${track.mode==='mute'?'selected':''}>Narration only</option>${track.kind==='character'?`<option value="silent" ${track.mode==='silent'?'selected':''}>Mute both</option>`:''}</select></label>`:''}<label><input data-track-under type="checkbox" ${track.under_voice?'checked':''}> Keep narration in front</label></section>`).join('')||'<p>No extra audio tracks yet. Import a video or audio file from Library.</p>'}`;
    dialog.scrollTop=scroll;if(selector)dialog.querySelector(selector)?.focus({preventScroll:true});
  }
  async function refresh(){
    if(!window.ShowReview?.active())return;
    const project=ShowReview.projectId();
    const data=await call('');if(project!==ShowReview.projectId())return;
    liveDoc=data;owner=project;
    if(dialog?.open){doc=data;render();}
  }
  async function open(trackId=null){
    if(!window.ShowReview?.active()){showToast('Open a Show project and scene to use the sound mixer.');return;}
    ensure();status('Reading audio tracks…');if(!dialog.open)dialog.showModal();
    try{
      await refresh();status('Changes are saved in this project.');
      if(typeof trackId==='string'){
        const row=dialog.querySelector(`[data-audio-track="${CSS.escape(trackId)}"]`);
        if(row){row.scrollIntoView({block:'nearest'});row.querySelector('[data-track-volume]').focus({preventScroll:true});status('Selected track: '+row.querySelector('strong').textContent+'. Changes are saved in this project.');}
        else status('This effect has no active audio track. Choose or record its voice first.');
      }
    }catch(error){status(error.message);}
  }
  async function save(event){
    if(owner!==ShowReview.projectId())throw Error('Reopen the mixer in the current project.');
    const track=event.target.closest('[data-audio-track]');
    if(track){
      const item=await call('/tracks/'+encodeURIComponent(track.dataset.audioTrack),{method:'PATCH',body:JSON.stringify({volume:Number(track.querySelector('[data-track-volume]').value),mode:track.querySelector('[data-track-mode]')?.value||'mix',under_voice:track.querySelector('[data-track-under]').checked})});
      await ShowReview.audioChanged(false,item);
    } else {
      const field=event.target,changes={};
      if(field.matches('[data-narration]'))changes.narration_volume=Number(field.value);
      else if(field.matches('[data-audio-censor]'))changes.censor=field.checked;
      else if(field.matches('[data-audio-denoise]'))changes.denoise=field.checked;
      else if(field.matches('[data-audio-excluded]'))changes.excluded_words=field.value.split(',').map(s=>s.trim()).filter(Boolean);
      else return;
      await call('',{method:'PATCH',body:JSON.stringify(changes)});await ShowReview.audioChanged(false);
    }
    await refresh();status('Saved. Preview scene mix to hear all sources together.');
  }
  async function job(mode){
    status(mode==='balance'?'Measuring sources and balancing audio…':'Measuring the noise floor and preparing clean audio…');
    dialog.querySelectorAll('input,select,[data-audio-balance],[data-audio-noise]').forEach(el=>el.disabled=true);
    try{await call('/job',{method:'POST',body:JSON.stringify({mode})});await watchJob();}catch(error){status(error.message);await refresh();dialog.querySelectorAll('[data-audio-balance],[data-audio-noise]').forEach(el=>el.disabled=false);}
  }
  async function watchJob(){
    clearTimeout(poll);if(owner!==ShowReview.projectId())return;
    const current=await call('/job');
    if(current.status==='working'){status(current.mode==='denoise'?'Measuring the noise floor and preparing clean audio…':`Balancing audio… ${Math.round(current.progress*100)}%`);poll=setTimeout(()=>watchJob().catch(error=>status(error.message)),700);return;}
    await ShowReview.audioChanged(false);await refresh();
    dialog.querySelectorAll('[data-audio-balance],[data-audio-noise]').forEach(el=>el.disabled=false);
    status(current.status==='failed'?`Audio processing failed: ${current.error}`:current.mode==='balance'?`Balanced ${current.result.balanced} audio track${current.result.balanced===1?'':'s'}.${current.result.unavailable.length?' Some sources have no usable audio: '+current.result.unavailable.map(item=>item.reason).join('; '):''}`:'Noise analysis complete. Preview the scene to compare.');
  }
  // Light live censor/volume preview on the source. Rendered previews already contain the mix.
  const video=document.querySelector('#showStageVideo');
  async function startLive(){
    const session=++playSession;cancelAnimationFrame(liveFrame);lastBeep='';
    if(!liveDoc||owner!==ShowReview.projectId())await refresh().catch(()=>{});
    if(!audioContext)try{audioContext=new (window.AudioContext||window.webkitAudioContext)();await audioContext.resume();buffer=await audioContext.decodeAudioData(await (await fetch('/api/v1/show/audio/censor-sound')).arrayBuffer());}catch{}
    if(session===playSession&&!video.paused)tick(session);
  }
  function tick(session){
    if(video.paused||session!==playSession)return;
    const state=ShowReview.playbackState();
    if(state.original&&liveDoc){
      const hit=liveDoc.settings.censor&&liveDoc.words.find(word=>state.at>=word.start&&state.at<word.end);
      video.volume=hit?0:Math.min(1,liveDoc.settings.narration_volume);
      const key=hit?hit.start+':'+hit.end:'';
      if(hit&&key!==lastBeep&&buffer&&audioContext?.state==='running'){const sound=audioContext.createBufferSource();sound.buffer=buffer;const gain=audioContext.createGain();gain.gain.value=liveDoc.settings.narration_volume;sound.connect(gain);gain.connect(audioContext.destination);sound.start();}
      lastBeep=key;
    }else video.volume=1;
    liveFrame=requestAnimationFrame(()=>tick(session));
  }
  video?.addEventListener('play',()=>startLive().catch(()=>{}));
  video?.addEventListener('pause',()=>{playSession++;cancelAnimationFrame(liveFrame);if(liveDoc&&ShowReview.playbackState().original)video.volume=Math.min(1,liveDoc.settings.narration_volume);});
  const button=document.createElement('button');button.type='button';button.id='showAudioOpen';button.textContent='Sound mixer';button.hidden=true;
  document.getElementById('connectorsOpen')?.before(button);button.onclick=open;
  const box=document.querySelector('#showReviewBox');if(box)new MutationObserver(()=>{button.hidden=box.hidden;}).observe(box,{attributes:true,attributeFilter:['hidden']});
  window.ShowAudio={open,refresh};
})();
