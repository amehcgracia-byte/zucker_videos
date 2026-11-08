// One voice recorder/drop target shared by photo characters and scene effects.
// Dispatches an actual project audio reference, never a transcription in its place.
(() => {
  let active = null;
  function stop(discard = false) {
    if (!active) return;
    active.discard ||= discard;
    if (active.recorder.state !== 'inactive') active.recorder.stop();
    active.stream.getTracks().forEach(track => track.stop());
  }
  function mount(host) {
    host.dataset.voiceMounted = 'true';
    host.classList.add('show-voice-input');
    host.innerHTML = '<strong>Voice-over</strong><div><button type="button" data-rec>🎙 Record now</button> <button type="button" data-stop disabled>■ Stop</button> <label>Import audio <input type="file" accept="audio/*,.webm,.m4a,.mp3,.wav,.ogg" data-import></label></div><small>Or drop an audio file here. Up to 3 minutes. Recording starts only after microphone permission.</small><p role="status" data-status></p><audio controls hidden></audio>';
    const rec=host.querySelector('[data-rec]'), end=host.querySelector('[data-stop]'), status=host.querySelector('[data-status]'), player=host.querySelector('audio');
    let url=null;
    async function use(blob, name, project) {
      if (!host.isConnected || project !== activeProjectId) return;
      status.textContent='Saving voice in this project…'; rec.disabled=true;
      try {
        const form=new FormData(); form.append('file',blob,name);form.append('project_id',project);
        const response=await fetch('/api/v1/show/voices',{method:'POST',body:form});
        const result=await response.json();
        if(!response.ok) throw Error(result.error?.message || result.message || 'Could not save audio');
        if (!host.isConnected || project !== activeProjectId) return;
        if(url) URL.revokeObjectURL(url); url=URL.createObjectURL(blob);player.src=url;player.hidden=false;
        host.dispatchEvent(new CustomEvent('show-voice-ready',{bubbles:true,detail:result}));
        status.textContent='Voice saved and assigned here. You can listen or replace it.';
      } catch(error){status.textContent=error.message;}finally{rec.disabled=false;}
    }
    rec.onclick=async()=>{
      rec.disabled=true;
      const project=activeProjectId;
      let stream;
      try {
        if(active) throw Error('Stop the other recording first.');
        if(!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) throw Error('Microphone unavailable. Import an audio file instead.');
        stream=await navigator.mediaDevices.getUserMedia({audio:true});
        if(!host.isConnected || project!==activeProjectId){stream.getTracks().forEach(t=>t.stop());return;}
        const mime=['audio/mp4','audio/webm','audio/ogg'].find(x=>MediaRecorder.isTypeSupported(x));
        const recorder=new MediaRecorder(stream,mime?{mimeType:mime}:{}), chunks=[];
        const recording={host,project,stream,recorder,discard:false,timer:null};active=recording;
        recorder.ondataavailable=e=>{if(e.data.size)chunks.push(e.data)};
        recorder.onstop=()=>{
          clearTimeout(recording.timer);stream.getTracks().forEach(t=>t.stop());if(active===recording)active=null;
          end.disabled=true;rec.disabled=false;rec.textContent='🎙 Record again';
          if(!recording.discard && chunks.length){const type=recorder.mimeType||mime||'audio/webm';use(new Blob(chunks,{type}),'Voice-over.'+(type.includes('mp4')?'m4a':type.includes('ogg')?'ogg':'webm'),project);}
          else status.textContent='Recording cancelled.';
        };
        recorder.onerror=()=>{status.textContent='Recording failed. You can import audio instead.';stop(true)};
        recorder.start();rec.textContent='● REC';end.disabled=false;status.textContent='● REC — recording your voice…';
        recording.timer=setTimeout(()=>stop(),180000);
      } catch(error){stream?.getTracks().forEach(t=>t.stop());status.textContent=error.message;rec.disabled=false;}
    };
    end.onclick=()=>{if(active?.host===host)stop()};
    const importFile=file=>{if(!file)return; if(active?.host===host)stop(true);use(file,file.name,activeProjectId)};
    host.querySelector('[data-import]').onchange=e=>importFile(e.target.files[0]);
    host.ondragover=e=>{e.preventDefault();e.stopPropagation();e.dataTransfer.dropEffect='copy'};
    host.ondrop=e=>{e.preventDefault();e.stopPropagation();importFile(e.dataTransfer.files[0])};
  }
  const observer=new MutationObserver(()=>{
    document.querySelectorAll('[data-voice-input]:not([data-voice-mounted])').forEach(mount);
    if(active && (!active.host.isConnected || active.project!==activeProjectId || active.host.closest('[hidden]')))stop(true);
  });
  observer.observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['hidden']});
  window.addEventListener('pagehide',()=>stop(true));
})();
