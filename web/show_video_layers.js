/* Connect the approved live compositor to its project effect. No generation service. */
(() => {
  const prefix = document.currentScript.dataset.prefix;
  let chosenFile = null, chosenLibraryPath=null;
  $('#saveLayers').disabled = true;
  $('#upload').addEventListener('change', event => {
    chosenFile = event.target.files[0] || null;chosenLibraryPath=null;
    $('#quickBackground option[value=video]').disabled = false; $('#videoBackground').disabled=false;
  });
  const libraryButton=document.createElement('button');libraryButton.textContent='From library…';libraryButton.type='button';
  $('#upload').parentElement.before(libraryButton);
  $('#quickBackground').addEventListener('change',()=>{chosenLibraryPath=null;});
  libraryButton.onclick=async()=>{
    const picker=document.createElement('dialog');picker.style.cssText='width:min(800px,90vw);max-height:85vh;overflow:auto;background:#fff;color:#17251d;border-radius:16px';
    picker.innerHTML='<header style="position:sticky;top:0;background:white"><h2>Background library</h2><button type="button">Close</button></header><div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:12px"></div>';
    document.body.append(picker);picker.querySelector('header button').onclick=()=>picker.close();picker.onclose=()=>picker.remove();picker.showModal();
    try{
      const response=await fetch(prefix+'/library');if(!response.ok)throw Error('The background library could not be loaded.');
      const data=await response.json(),grid=picker.querySelector('div');
      for(const item of data.items){
        const button=document.createElement('button'),media=document.createElement(item.media_kind==='video'?'video':'img');
        const src=prefix+'/library-media?path='+encodeURIComponent(item.path);
        if(item.media_kind!=='video')media.loading='lazy';
        media.src=src;media.style.cssText='width:100%;height:90px;object-fit:cover';if(item.media_kind==='video'){media.muted=true;media.preload='metadata';}
        button.append(media,document.createTextNode(item.name));button.onclick=async()=>{chosenFile=null;chosenLibraryPath=item.path;picker.close();if(item.media_kind==='video')await selectVideo(src,item.name);else await selectPhoto(src,item.name);};grid.append(button);
      }
      if(!data.items.length)grid.textContent='No backgrounds yet. Add a photo or video to Library first.';
    }catch(e){picker.querySelector('div').textContent=e.message;}
  };
  const colourTools=document.createElement('div');colourTools.className='panel';
  colourTools.innerHTML='<h2>Foreground colour & light</h2>'+[['brightness','Brightness',0,3,1],['saturation','Saturation',0,3,1],['hue','Hue',-180,180,0]].map(([name,label,min,max,value])=>`<label>${label}<input data-layer-colour="${name}" type="range" min="${min}" max="${max}" step="0.05" value="${value}"></label>`).join('');
  document.querySelector('aside').append(colourTools);
  const colours=()=>Object.fromEntries([...colourTools.querySelectorAll('input')].map(el=>[el.dataset.layerColour,Number(el.value)]));
  // Filter only the foreground before applying its alpha, never the background.
  const baseDraw=draw;draw=function(){
    const c=colours();lc.filter=`brightness(${c.brightness}) saturate(${c.saturation}) hue-rotate(${c.hue}deg)`;
    baseDraw();lc.filter='none';
    if(ready&&mode!=='original'){
      ctx.save();ctx.strokeStyle='#ffdc77';ctx.lineWidth=2;ctx.setLineDash([8,6]);
      ctx.strokeRect(x+(960-960*scale)/2,y+540-540*scale,960*scale,540*scale);ctx.restore();
    }
  };
  colourTools.addEventListener('input',()=>draw());
  let layerSetupStarted=false;
  const setupLayers=async()=>{
    if(layerSetupStarted)return;layerSetupStarted=true;
    const p = config.placement || {};
    for(const field of colourTools.querySelectorAll('input'))field.value=p[field.dataset.layerColour]??field.value;
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
    $('footer').textContent = `Live preview · ${config.duration.toFixed(1)} seconds · placement applies to the full timeline effect · your voice stays linked · backgrounds are muted and loop to fit`;
    $('#status').textContent = 'Drag yourself to place your moving video. Choose a background, then Use in scene to save.';
    $('#saveLayers').disabled = false;
    draw();
  };
  window.addEventListener('layers-ready',setupLayers);if(ready)setupLayers();
  $('#saveLayers').onclick = async event => {
    event.target.disabled = true;
    $('#status').textContent='Saving video layers in your project…';
    host.pause(); behind.pause();
    try {
      const src = backKind==='video' ? behind.src : photo?.src;
      const filename = src ? new URL(src).pathname.split('/').pop() : '';
      const preset = config.backgrounds.some(item => item.file===filename) ? filename : null;
      const form = new FormData();
      form.append('settings', JSON.stringify({person_style:mode,person_scale:scale,person_offset:[x/960,y/540],preset,library_path:chosenLibraryPath,...colours()}));
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
