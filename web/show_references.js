// Internet references are explicit choices. Searching never uploads the user's footage.
(() => {
  let dialog,context,items=[],selected=null;
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const message=text=>dialog.querySelector('[data-ref-status]').textContent=text;
  function ensure(){
    if(dialog)return;
    dialog=document.createElement('dialog');dialog.id='showReferenceDialog';dialog.setAttribute('aria-label','Search references');
    dialog.innerHTML=`<header><h2>Search references</h2><button data-ref-close aria-label="Close references">×</button></header>
      <blockquote data-ref-words></blockquote><nav><button data-ref-tab="photos">Photos</button><button data-ref-tab="video">Video reference</button><button data-ref-tab="library">Video library</button><button data-ref-tab="generate">Create video · external</button></nav>
      <section data-ref-section="photos"><p>Search Wikimedia Commons by name, person, object or place. Only the search text is sent. The source and licence stay with the image.</p>
      <form data-ref-search><input aria-label="Search reference photos" maxlength="160" placeholder="Bill Gates, bullring, sofa…"><button>Search photos</button></form><div data-ref-results class="show-ref-results"></div>
      <div data-ref-choice hidden><img data-ref-chosen alt="Chosen reference"><label>Use as<select data-ref-mode><option value="whole">Whole photo</option><option value="person">Person / remove background</option><option value="head">Face / head mask</option><option value="background">Background only</option><option value="silhouette">Black silhouette</option></select></label><button data-ref-use>Import into project…</button></div></section>
      <section data-ref-section="video" hidden><p>Open a search in your default browser, paste the video link here. The timeline bar controls how long it plays. Save the link as a reference, or download those seconds to put the clip in the edit.</p>
      <form data-ref-video-search><input aria-label="Search reference videos" maxlength="160" placeholder="Movie, scene, person…"><button>Search videos in browser</button></form>
      <label>Video link (YouTube, Vimeo and supported sites)<input data-ref-url type="url" placeholder="https://www.youtube.com/watch?v=…"></label>
      <details><summary>Source position (optional)</summary><div class="show-fields"><label>Start in the online video (seconds)<input data-ref-start type="number" min="0" step="any" value="0"></label></div><input data-ref-end type="hidden" value="30"></details>
      <div class="show-actions"><button data-ref-watch>Open video in browser</button><button data-ref-save>Save link &amp; interval</button><button data-ref-download class="primary">Download clip &amp; place it</button></div><div data-ref-saved></div>
      <p>Up to 30 seconds are kept in this project. Drag the timeline edges to choose the length that plays. Supported sites include YouTube, Vimeo, Dailymotion, Twitch, TikTok, Instagram, Reddit, Archive.org, Pexels and Pixabay; availability depends on the site. Use clips you are allowed to publish.</p>
      <div data-clip-settings hidden><h3>Edit this clip</h3><video data-clip-preview controls playsinline preload="metadata" style="width:100%;max-height:260px"></video>
      <p>The playhead chooses the source picture. The effect's timeline edges choose its duration.</p><button data-clip-use-playhead>Start at this picture</button>
      <label>Background<select data-clip-matte><option value="auto">Detect green screen automatically</option><option value="none">Keep the complete video</option><option value="green">Remove green screen</option><option value="person">Cut out the moving person (macOS)</option></select></label>
      <label><input type="checkbox" data-clip-audio> Use the clip's audio</label><p>With a cutout, your original video and voice continue underneath. With a full-frame clip and its audio, your narration pauses.</p>
      <button data-clip-save class="primary">Apply clip settings</button><button data-clip-layers>Edit moving person &amp; background…</button></div></section>
      <section data-ref-section="library" hidden><h3>Video library</h3><label><input data-ref-transparent type="checkbox" checked> Cutouts / transparent backgrounds only</label><div data-ref-video-library></div><p>Original files stay in this project. A cutout is a reusable recipe; it does not duplicate the video.</p></section>
      <section data-ref-section="generate" hidden><h3>Create a video outside the editor</h3><p>No video generator is connected. Describe the shot, copy the prompt to your chosen video generator, then bring back the result. Do not expect ChatGPT image generation to return a video.</p>
      <label>What should happen?<textarea data-ref-idea rows="3" placeholder="A handmade clay version of me remembering a tiny village…"></textarea></label>
      <button data-ref-prompt>Prepare video prompt</button><textarea data-ref-prompt-text aria-label="External video prompt" rows="10" readonly></textarea><button data-ref-copy>Copy prompt</button></section>
      <p data-ref-status role="status"></p>`;
    document.body.append(dialog);
    dialog.querySelector('[data-ref-close]').onclick=()=>dialog.close();
    dialog.querySelectorAll('[data-ref-tab]').forEach(button=>button.onclick=()=>tab(button.dataset.refTab));
    dialog.querySelector('[data-ref-search]').onsubmit=async event=>{
      event.preventDefault();const q=event.target.querySelector('input').value.trim();message('Searching reference photos…');
      try{items=(await api('/show/references/images?q='+encodeURIComponent(q))).items;selected=null;dialog.querySelector('[data-ref-choice]').hidden=true;
        dialog.querySelector('[data-ref-results]').innerHTML=items.map((item,index)=>`<button type="button" data-ref-pick="${index}"><img src="${esc(item.thumb)}" alt="" loading="lazy"><span>${esc(item.name)}</span><small>${esc(item.license)}</small></button>`).join('');message(items.length?'Choose a photo, then choose how to use it.':'No photos found. Try a different name.');
      }catch(error){message(error.message);}
    };
    dialog.querySelector('[data-ref-results]').onclick=event=>{const button=event.target.closest('[data-ref-pick]');if(!button)return;selected=items[Number(button.dataset.refPick)];dialog.querySelector('[data-ref-chosen]').src=selected.thumb;dialog.querySelector('[data-ref-choice]').hidden=false;message(selected.author+' · '+selected.license);};
    dialog.querySelector('[data-ref-use]').onclick=async event=>{
      if(!selected)return;event.target.disabled=true;const owner=context;message('Saving the chosen reference in this project…');
      try{const result=await api('/show/references/images',{method:'POST',body:JSON.stringify({project_id:owner.projectId,title:selected.title})});
        if(context!==owner)return;
        await ShowReview.refreshLibrary();const mode=dialog.querySelector('[data-ref-mode]').value;
        if(mode==='whole'){await ShowReview.addToScene(result.path,owner.placement);dialog.close();}
        else{dialog.close();await ShowPhotoStudio.openPhoto(result.photo,{placement:owner.placement,silhouette:mode==='silhouette',background:mode==='background',head:mode==='head'});}
      }catch(error){message(error.message);}finally{event.target.disabled=false;}
    };
    dialog.querySelector('[data-ref-video-search]').onsubmit=async event=>{event.preventDefault();try{await api('/show/references/open',{method:'POST',body:JSON.stringify({kind:'search',query:event.target.querySelector('input').value})});message('Search opened in your default browser. Paste the chosen video link here.');}catch(error){message(error.message);}};
    dialog.querySelector('[data-ref-watch]').onclick=async()=>{try{await api('/show/references/open',{method:'POST',body:JSON.stringify({kind:'video',url:dialog.querySelector('[data-ref-url]').value})});}catch(error){message(error.message);}};
    const saveReference=()=>api('/show/references/video',{method:'POST',body:JSON.stringify({project_id:context.projectId,scene_id:context.scene.id,selected_text:context.picked.text,at:context.picked.start,url:dialog.querySelector('[data-ref-url]').value,start:Number(dialog.querySelector('[data-ref-start]').value),end:Number(dialog.querySelector('[data-ref-start]').value)+30})});
    dialog.querySelector('[data-ref-download]').onclick=async event=>{event.target.disabled=true;try{const ref=await saveReference();ShowReview.referenceSaved(ref);message('Downloading only the chosen seconds…');
      const result=await api(`/show/references/video/${encodeURIComponent(ref.id)}/download`,{method:'POST',body:JSON.stringify({replace_id:context.clip?.id,duration:context.clip?.duration||Math.max(.2,context.picked.end-context.picked.start)})});dialog.close();if(context.clip)await ShowReview.clipUpdated(result.proposal);else ShowReview.clipAdded(result);}catch(error){message(error.message);}finally{event.target.disabled=false;}};
    dialog.querySelector('[data-clip-use-playhead]').onclick=()=>{
      if(context.clip){context.clipStart=dialog.querySelector('[data-clip-preview]').currentTime;message('Source picture selected. Apply clip settings to save it; the timeline still controls length.');}
    };
    dialog.querySelector('[data-clip-save]').onclick=async event=>{
      if(!context.clip)return;event.target.disabled=true;
      try{const item=await api('/show/clips/'+encodeURIComponent(context.clip.id)+'/settings',{method:'POST',body:JSON.stringify({clip_start:context.clipStart??context.clip.params.clip_start??0,matte:dialog.querySelector('[data-clip-matte]').value,clip_audio:dialog.querySelector('[data-clip-audio]').checked})});
        await ShowReview.clipUpdated(item);context.clip=item;message('Clip settings saved. Its timeline bar controls the duration.');
      }catch(error){message(error.message);}finally{event.target.disabled=false;}
    };
    dialog.querySelector('[data-clip-layers]').onclick=()=>{const item=context.clip;if(!item)return;dialog.close();ShowReview.openVideoLayers(item.id,item.params.person_style||'person');};
    dialog.querySelector('[data-ref-transparent]').onchange=()=>loadVideoLibrary();
    dialog.querySelector('[data-ref-save]').onclick=async()=>{try{const ref=await api('/show/references/video',{method:'POST',body:JSON.stringify({project_id:context.projectId,scene_id:context.scene.id,selected_text:context.picked.text,at:context.picked.start,url:dialog.querySelector('[data-ref-url]').value,start:Number(dialog.querySelector('[data-ref-start]').value),end:Number(dialog.querySelector('[data-ref-start]').value)+30})});ShowReview.referenceSaved(ref);dialog.querySelector('[data-ref-saved]').textContent=`Saved reference · ${ref.start}–${ref.end} s · ${ref.url}`;message('Link and interval saved in this project. No video has been downloaded or placed.');}catch(error){message(error.message);}};
    dialog.querySelector('[data-ref-prompt]').onclick=()=>{
      const direction=dialog.querySelector('[data-ref-idea]').value.trim();if(!direction){message('Describe the shot first.');return;}
      dialog.querySelector('[data-ref-prompt-text]').value=`Create one short video insert for a comedy show.\nSpoken phrase: ${context.picked.text}\nTarget duration: ${Math.max(.1,context.picked.end-context.picked.start).toFixed(2)} seconds.\nRequested action: ${direction}\nMake the action support the spoken phrase, with an anticipation, a clear action and a quiet hold. Do not loop mechanically. Keep character identity, clothing and proportions consistent. If an identity photo is provided, use the same consenting adult. Match the surrounding scene's camera angle and light. No unwanted captions, logos or sticker borders. Preserve the original narration separately; deliver the visual insert without replacing it.\nReferences to attach: the selected reference and, when needed, my own photo.\nReturn the resulting video file to Show Studio for placement on these words.`;
      message('Prompt prepared locally. No generation or upload has taken place.');
    };
    dialog.querySelector('[data-ref-copy]').onclick=async()=>{try{await navigator.clipboard.writeText(dialog.querySelector('[data-ref-prompt-text]').value);message('Prompt copied.');}catch(error){message(error.message);}};
  }
  function tab(name){dialog.querySelectorAll('[data-ref-section]').forEach(section=>section.hidden=section.dataset.refSection!==name);message('');if(name==='library')loadVideoLibrary();}
  function open(scene,picked,projectId,section='photos'){
    ensure();dialog.querySelector('h2').textContent='Search references';context={scene,picked,projectId,placement:{sceneId:scene.id,at:picked.start,duration:picked.end-picked.start}};
    dialog.querySelector('[data-ref-words]').textContent=picked.text;dialog.querySelector('[data-ref-search] input').value=picked.text;dialog.querySelector('[data-ref-video-search] input').value=picked.text;
    dialog.querySelector('[data-clip-settings]').hidden=true;dialog.querySelector('[data-ref-url]').value='';dialog.querySelector('[data-ref-start]').value=0;
    tab(section);dialog.showModal();
  }
  async function loadVideoLibrary(){
    const list=dialog.querySelector('[data-ref-video-library]');list.textContent='Loading project videos…';
    try{const records=(await api('/show/video-library')).items;const only=dialog.querySelector('[data-ref-transparent]').checked;
      list.replaceChildren();for(const item of records.filter(v=>!only||v.transparent)){const button=document.createElement('button');button.textContent=item.name+' · '+(item.transparent?'cutout':'complete video');button.onclick=async()=>{try{const result=await api('/show/video-library/'+encodeURIComponent(item.id)+'/place',{method:'POST',body:JSON.stringify({scene_id:context.scene.id,at:context.picked.start,duration:context.picked.end-context.picked.start})});dialog.close();ShowReview.clipAdded(result);}catch(error){message(error.message);}};list.append(button);}
      if(!list.childElementCount)list.textContent='No matching videos yet. Download a clip and choose its background removal.';
    }catch(error){list.textContent=error.message;}
  }
  function openClip(scene,item,projectId){
    open(scene,{text:item.params.selected_text||scene.text,start:item.at,end:item.at+item.duration},projectId,'video');
    context.clip=item;context.clipStart=item.params.clip_start||0;
    dialog.querySelector('[data-ref-url]').value=item.params.url||'';dialog.querySelector('[data-ref-start]').value=item.params.source_start||0;
    dialog.querySelector('[data-clip-settings]').hidden=false;
    const video=dialog.querySelector('[data-clip-preview]');video.src='/api/v1/show/clips/'+encodeURIComponent(item.id)+'/media';video.onloadedmetadata=()=>video.currentTime=context.clipStart;
    dialog.querySelector('[data-clip-matte]').value=item.params.matte||'none';dialog.querySelector('[data-clip-audio]').checked=item.params.clip_audio!==false;
    dialog.querySelector('h2').textContent='Video clip settings';
  }
  window.ShowReferences={open,openClip};
})();
