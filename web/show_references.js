// Internet references are explicit choices. Searching never uploads the user's footage.
(() => {
  let dialog,context,items=[],selected=null;
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const message=text=>dialog.querySelector('[data-ref-status]').textContent=text;
  function ensure(){
    if(dialog)return;
    dialog=document.createElement('dialog');dialog.id='showReferenceDialog';dialog.setAttribute('aria-label','Search references');
    dialog.innerHTML=`<header><h2>Search references</h2><button data-ref-close aria-label="Close references">×</button></header>
      <blockquote data-ref-words></blockquote><nav><button data-ref-tab="photos">Photos</button><button data-ref-tab="video">Video reference</button><button data-ref-tab="generate">Create video · external</button></nav>
      <section data-ref-section="photos"><p>Search Wikimedia Commons by name, person, object or place. Only the search text is sent. The source and licence stay with the image.</p>
      <form data-ref-search><input aria-label="Search reference photos" maxlength="160" placeholder="Bill Gates, bullring, sofa…"><button>Search photos</button></form><div data-ref-results class="show-ref-results"></div>
      <div data-ref-choice hidden><img data-ref-chosen alt="Chosen reference"><label>Use as<select data-ref-mode><option value="whole">Whole photo</option><option value="person">Person / remove background</option><option value="head">Face / head mask</option><option value="background">Background only</option><option value="silhouette">Black silhouette</option></select></label><button data-ref-use>Import into project…</button></div></section>
      <section data-ref-section="video" hidden><p>Open a search in your default browser, paste the video link here and choose its seconds. Save the link as a reference, or download those seconds to put the clip in the edit.</p>
      <form data-ref-video-search><input aria-label="Search reference videos" maxlength="160" placeholder="Movie, scene, person…"><button>Search videos in browser</button></form>
      <label>YouTube / Vimeo link<input data-ref-url type="url" placeholder="https://www.youtube.com/watch?v=…"></label>
      <div class="show-fields"><label>From (seconds)<input data-ref-start type="number" min="0" step="any" value="0"></label><label>To (seconds)<input data-ref-end type="number" min="0" step="any" value="5"></label></div>
      <div class="show-actions"><button data-ref-watch>Open video in browser</button><button data-ref-save>Save link &amp; interval</button><button data-ref-download class="primary">Download these seconds &amp; put them in</button></div><div data-ref-saved></div>
      <p>Only the chosen seconds are downloaded (at most 30), into this project. The clip plays on the selected words with its own sound while you pause. Use clips you are allowed to publish.</p></section>
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
    const saveReference=()=>api('/show/references/video',{method:'POST',body:JSON.stringify({project_id:context.projectId,scene_id:context.scene.id,selected_text:context.picked.text,at:context.picked.start,url:dialog.querySelector('[data-ref-url]').value,start:Number(dialog.querySelector('[data-ref-start]').value),end:Number(dialog.querySelector('[data-ref-end]').value)})});
    dialog.querySelector('[data-ref-download]').onclick=async event=>{event.target.disabled=true;try{const ref=await saveReference();ShowReview.referenceSaved(ref);message('Downloading only the chosen seconds…');
      const result=await api(`/show/references/video/${encodeURIComponent(ref.id)}/download`,{method:'POST',body:'{}'});dialog.close();ShowReview.clipAdded(result);}catch(error){message(error.message);}finally{event.target.disabled=false;}};
    dialog.querySelector('[data-ref-save]').onclick=async()=>{try{const ref=await api('/show/references/video',{method:'POST',body:JSON.stringify({project_id:context.projectId,scene_id:context.scene.id,selected_text:context.picked.text,at:context.picked.start,url:dialog.querySelector('[data-ref-url]').value,start:Number(dialog.querySelector('[data-ref-start]').value),end:Number(dialog.querySelector('[data-ref-end]').value)})});ShowReview.referenceSaved(ref);dialog.querySelector('[data-ref-saved]').textContent=`Saved reference · ${ref.start}–${ref.end} s · ${ref.url}`;message('Link and interval saved in this project. No video has been downloaded or placed.');}catch(error){message(error.message);}};
    dialog.querySelector('[data-ref-prompt]').onclick=()=>{
      const direction=dialog.querySelector('[data-ref-idea]').value.trim();if(!direction){message('Describe the shot first.');return;}
      dialog.querySelector('[data-ref-prompt-text]').value=`Create one short video insert for a comedy show.\nSpoken phrase: ${context.picked.text}\nTarget duration: ${Math.max(.1,context.picked.end-context.picked.start).toFixed(2)} seconds.\nRequested action: ${direction}\nMake the action support the spoken phrase, with an anticipation, a clear action and a quiet hold. Do not loop mechanically. Keep character identity, clothing and proportions consistent. If an identity photo is provided, use the same consenting adult. Match the surrounding scene's camera angle and light. No unwanted captions, logos or sticker borders. Preserve the original narration separately; deliver the visual insert without replacing it.\nReferences to attach: the selected reference and, when needed, my own photo.\nReturn the resulting video file to Show Studio for placement on these words.`;
      message('Prompt prepared locally. No generation or upload has taken place.');
    };
    dialog.querySelector('[data-ref-copy]').onclick=async()=>{try{await navigator.clipboard.writeText(dialog.querySelector('[data-ref-prompt-text]').value);message('Prompt copied.');}catch(error){message(error.message);}};
  }
  function tab(name){dialog.querySelectorAll('[data-ref-section]').forEach(section=>section.hidden=section.dataset.refSection!==name);message('');}
  function open(scene,picked,projectId,section='photos'){
    ensure();context={scene,picked,projectId,placement:{sceneId:scene.id,at:picked.start,duration:picked.end-picked.start}};
    dialog.querySelector('[data-ref-words]').textContent=picked.text;dialog.querySelector('[data-ref-search] input').value=picked.text;dialog.querySelector('[data-ref-video-search] input').value=picked.text;
    tab(section);dialog.showModal();
  }
  window.ShowReferences={open};
})();
