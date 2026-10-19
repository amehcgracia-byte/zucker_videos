// One music catalog; downloaded tracks retain their license and source metadata.
(() => {
  let dialog,planDialog,planDoc,owner,rows=[],signature='',poll;
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const call=(path,options={})=>api('/music'+path,options);
  const message=(host,text)=>host.querySelector('[data-music-status]').textContent=text;
  async function open(){
    if(!dialog){
      dialog=document.createElement('dialog');dialog.className='music-library-dialog';dialog.setAttribute('aria-label','Music library');
      dialog.innerHTML='<header><h2>Music library</h2><button data-close aria-label="Close music library">×</button></header><nav aria-label="Music library sections"><button data-tab="local">Local</button><button data-tab="search">Search</button></nav><section data-local><label>Library folder <input data-folder></label><button data-folder-save>Use folder</button><label>Import audio <input data-import type="file" accept="audio/*" multiple></label><label>Find by name <input data-local-query type="search"></label><div data-local-results></div></section><section data-search hidden><p>Jamendo songs: downloadable CC0/CC BY results only. Freesound: CC0 preview MP3s; original files require OAuth2. Provider API access and track permissions are separate.</p><label>Source <select data-source><option value="jamendo">Jamendo · songs</option><option value="freesound">Freesound · sounds / jingles</option></select></label><label>Music or sound <input data-query placeholder="Gentle piano, suspense, funny violin…"></label><button data-find>Search</button><div data-search-results></div></section><button data-backstage-plan>Backstage audio mix plan</button><audio data-player controls hidden></audio><p role="status" data-music-status></p>';
      document.body.append(dialog);dialog.onclick=e=>action(e).catch(error=>message(dialog,error.message));
      dialog.querySelector('[data-local-query]').oninput=()=>local().catch(error=>message(dialog,error.message));
      dialog.querySelector('[data-query]').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();search().catch(error=>message(dialog,error.message));}};
      dialog.querySelector('[data-import]').onchange=async e=>{
        try{for(const file of e.target.files){const form=new FormData();form.append('file',file);const response=await fetch('/api/v1/music/import',{method:'POST',body:form});const data=await response.json();if(!response.ok)throw Error(data.error?.message||'Could not import audio');}await local();message(dialog,'Audio imported into your music library.');}catch(error){message(dialog,error.message);}finally{e.target.value='';}
      };
    }
    if(!dialog.open)dialog.showModal();await local();
  }
  function row(r,remote){return `<article class="music-track"><strong>${esc(r.name)}</strong><span>${esc(r.artist||r.source||'Local audio')} · ${r.duration?Number(r.duration).toFixed(1)+' s':''}</span><small>${esc(r.license)}${r.preview_only?' · Preview audio':''}</small>${r.license?.includes('/licenses/by/')?'<small>Attribution required; saved in credits.txt when used.</small>':''}<div><button data-listen="${esc(remote?r.preview_url:r.url)}">Preview</button>${remote?`<button data-download="${esc(r.source+'/'+r.id)}" ${r.download_allowed?'':'disabled'}>${r.preview_only?'Download preview':'Download'}</button>`:''}</div></article>`;}
  async function local(){const data=await call('/library?q='+encodeURIComponent(dialog.querySelector('[data-local-query]').value));dialog.querySelector('[data-folder]').value=data.folder;dialog.querySelector('[data-local-results]').innerHTML=data.items.map(r=>row(r,false)).join('')||'<p>No music yet. Import your audio or use Search.</p>';}
  async function search(){message(dialog,'Searching the selected service…');const source=dialog.querySelector('[data-source]').value,q=dialog.querySelector('[data-query]').value;const data=await call('/search?source='+source+'&q='+encodeURIComponent(q));dialog.querySelector('[data-search-results]').innerHTML=data.items.map(r=>row(r,true)).join('')||'<p>No compatible tracks found. Try another search.</p>';message(dialog,`${data.items.length} results. No file has been downloaded.`);}
  async function action(event){const b=event.target.closest('button');if(!b)return;
    if(b.hasAttribute('data-close')){dialog.querySelector('[data-player]').pause();dialog.close();if(planDialog?.open&&planDoc){const data=await call('/library');planDoc.library=data.items;renderPlan();}return;}
    if(b.dataset.tab){dialog.querySelector('[data-local]').hidden=b.dataset.tab!=='local';dialog.querySelector('[data-search]').hidden=b.dataset.tab!=='search';return;}
    if(b.hasAttribute('data-folder-save')){await call('/library',{method:'PUT',body:JSON.stringify({folder:dialog.querySelector('[data-folder]').value})});await local();return;}
    if(b.hasAttribute('data-backstage-plan'))return openPlan();
    if(b.hasAttribute('data-find'))return search();
    if(b.dataset.listen){const player=dialog.querySelector('[data-player]');player.src=b.dataset.listen;player.hidden=false;try{await player.play();}catch{message(dialog,'Press Play on the audio player to listen.');}return;}
    if(b.dataset.download){b.disabled=true;message(dialog,'Downloading audio and license information…');try{await call('/download/'+b.dataset.download,{method:'POST',body:'{}'});message(dialog,'Downloaded to your music library, with license and attribution.');await local();}finally{b.disabled=false;}}
  }
  const modes={narrador_solo:'Original voice only',musica_bajo_narrador:'Music under voice · ducking',solo_video:'Original video sound only',musica_solo:'Music only'};
  async function openPlan(){
    if(!planDialog){planDialog=document.createElement('dialog');planDialog.className='music-plan-dialog';planDialog.setAttribute('aria-label','Backstage audio mix plan');document.body.append(planDialog);}
    if(!planDialog.open)planDialog.showModal();
    planDialog.innerHTML='<header><h2>Backstage audio mix plan</h2><button data-plan-close>×</button></header><p>Reading the paper edit…</p>';
    try{const data=await call('/plan');owner=data.project_id;signature=data.signature;planDoc=data;
      rows=structuredClone(data.plan.segments?.length?data.plan.segments:data.segments.map(s=>({segment_id:s.id,mode:'narrador_solo',track_id:null,gain_db:-18,fade_in_s:Math.min(.5,s.duration_sec/4),fade_out_s:Math.min(.5,s.duration_sec/4)})));
      renderPlan();
    }catch(error){planDialog.innerHTML=`<header><h2>Audio mix plan</h2><button data-plan-close>×</button></header><p>${esc(error.message)}</p>`;}
    planDialog.onclick=e=>planAction(e).catch(error=>message(planDialog,error.message));
  }
  function renderPlan(){
    planDialog.innerHTML=`<header><h2>Backstage audio mix plan</h2><button data-plan-close aria-label="Close audio mix plan">×</button></header><p>The AI proposes a plan; review every segment before applying it. Your paper edit is kept. In Backstage the narrator is the original camera audio.</p><div class="music-plan-actions"><button data-plan-generate>Suggest mix with AI</button><button data-open-library>Music library</button><button data-plan-save>Save draft</button><button data-plan-apply>Apply reviewed plan</button></div><p>${planDoc.plan.status==='approved'?'Approved plan saved for export.':'Draft · not applied to export.'}</p><div data-plan-rows>${rows.map((r,i)=>{const s=planDoc.segments.find(s=>s.id===r.segment_id);return `<article class="music-track" data-row="${i}"><strong>${esc(s?.filename||r.segment_id)} · ${Number(s?.duration_sec||0).toFixed(1)} s</strong><p>${esc(s?.text_original||s?.text||s?.english_text||'No transcript')}</p><label>Mix <select data-mode>${Object.entries(modes).map(([id,label])=>`<option value="${id}" ${r.mode===id?'selected':''}>${label}</option>`).join('')}</select></label><label>Track <select data-track><option value="">No music</option>${planDoc.library.map(t=>`<option value="${esc(t.id)}" ${r.track_id===t.id?'selected':''}>${esc(t.name)}</option>`).join('')}</select></label><label>Music gain (dB) <input data-gain type="number" min="-60" max="6" step="1" value="${r.gain_db}"></label><label>Fade in (s) <input data-in type="number" min="0" max="${Math.min(10,(s?.duration_sec||0)/2)}" step=".1" value="${r.fade_in_s}"></label><label>Fade out (s) <input data-out type="number" min="0" max="${Math.min(10,(s?.duration_sec||0)/2)}" step=".1" value="${r.fade_out_s}"></label></article>`;}).join('')}</div><p role="status" data-music-status></p>`;
    planDialog.onchange=e=>{const el=e.target.closest('[data-row]');if(!el)return;const r=rows[Number(el.dataset.row)];Object.assign(r,{mode:el.querySelector('[data-mode]').value,track_id:el.querySelector('[data-track]').value||null,gain_db:Number(el.querySelector('[data-gain]').value),fade_in_s:Number(el.querySelector('[data-in]').value),fade_out_s:Number(el.querySelector('[data-out]').value)});};
  }
  async function planAction(event){const b=event.target.closest('button');if(!b)return;
    if(b.hasAttribute('data-plan-close')){clearTimeout(poll);planDialog.close();return;}
    if(b.hasAttribute('data-open-library'))return open();
    if(b.hasAttribute('data-plan-save')||b.hasAttribute('data-plan-apply')){
      planDoc.plan=await call('/plan',{method:'PUT',body:JSON.stringify({project_id:owner,signature,revision:planDoc.plan.revision,segments:rows,approve:b.hasAttribute('data-plan-apply')})});renderPlan();message(planDialog,planDoc.plan.status==='approved'?'Plan applied for export. Used tracks are copied into the project; credits.txt is ready.':'Draft saved; export still uses its current approved settings.');return;
    }
    if(b.hasAttribute('data-plan-generate')){b.disabled=true;await call('/plan/propose',{method:'POST',body:JSON.stringify({project_id:owner})});await watch();}
  }
  async function watch(){
    if(!planDialog.open)return;
    const job=await call('/plan/job');
    if(job.status==='working'){message(planDialog,'Preparing your draft mix plan…');poll=setTimeout(()=>watch().catch(e=>message(planDialog,e.message)),1000);return;}
    if(job.status==='failed'){message(planDialog,job.error);planDialog.querySelector('[data-plan-generate]').disabled=false;return;}
    await openPlan();message(planDialog,'AI draft ready. Review the tracks and levels before Apply reviewed plan.');
  }
  document.getElementById('musicLibraryOpen')?.addEventListener('click',()=>open().catch(error=>showToast(error.message,true)));
  document.getElementById('backstageAudioMix')?.addEventListener('click',()=>openPlan().catch(error=>showToast(error.message,true)));
  window.MusicLibrary={open,openPlan};
})();
