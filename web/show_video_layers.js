/* Connect the approved live compositor to its project effect. No generation service. */
(() => {
  const prefix = document.currentScript.dataset.prefix;
  let chosenFile = null;
  $('#saveLayers').disabled = true;
  $('#upload').addEventListener('change', event => {
    chosenFile = event.target.files[0] || null;
    $('#quickBackground option[value=video]').disabled = false; $('#videoBackground').disabled=false;
  });
  window.addEventListener('layers-ready', async () => {
    const p = config.placement || {};
    scale = p.person_scale || 1;
    x = (p.person_offset?.[0] || 0)*960; y = (p.person_offset?.[1] || 0)*540;
    $('#size').value = scale*100; $('#sizeValue').textContent = Math.round(scale*100)+'%';
    const wanted = new URLSearchParams(location.search).get('style') || p.person_style || 'person';
    $(`[data-mode="${wanted==='silhouette'?'silhouette':'person'}"]`).click();
    $('#videoBackground').textContent = '▶ Use my background video';
    $('#videoBackground').disabled = !config.current_video;
    $('#quickBackground option[value=video]').textContent = 'My background video';
    $('#quickBackground option[value=video]').disabled = !config.current_video;
    if (p.picture) {
      if (config.current_video) await selectVideo(prefix+'/media/background.mp4', 'Your background video');
      else await selectPhoto(prefix+'/media/current', 'Your background picture');
    }
    $('footer').textContent = 'Live preview of up to eight seconds · placement applies to the full timeline effect · your voice stays linked · backgrounds are muted and loop to fit';
    $('#status').textContent = 'Drag yourself to place your moving video. Choose a background, then Use in scene to save.';
    $('#saveLayers').disabled = false;
    draw();
  });
  $('#saveLayers').onclick = async event => {
    event.target.disabled = true;
    $('#status').textContent='Saving video layers in your project…';
    host.pause(); behind.pause();
    try {
      const src = backKind==='video' ? behind.src : photo?.src;
      const filename = src ? new URL(src).pathname.split('/').pop() : '';
      const preset = config.backgrounds.some(item => item.file===filename) ? filename : null;
      const form = new FormData();
      form.append('settings', JSON.stringify({person_style:mode,person_scale:scale,person_offset:[x/960,y/540],preset}));
      if (chosenFile && src===objectURL) form.append('file', chosenFile);
      const response = await fetch(prefix+'/save',{method:'POST',body:form});
      const item = await response.json();
      if (!response.ok) throw Error(item.error?.message || 'The video layers could not be saved.');
      $('#status').textContent = 'Video layers saved in your project.';
      parent.postMessage({type:'show-video-layers-saved',item}, location.origin);
    } catch (e) { error(e); }
    finally { event.target.disabled=false; }
  };
})();
