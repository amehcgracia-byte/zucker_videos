/* Independent source sections keep the existing workshop recipe intact. */
(function(){
const opening=new URLSearchParams(location.search).get('section')==='opening';
window.ShowSource={isOpening:opening,path:name=>(opening?'opening/':'')+name,key:opening?'show-editing-opening-v1':'show-editing-desk-v2'};
const row=document.createElement('div');row.className='controls sourceSections';const label=document.createElement('label');label.style.cssText='display:flex;gap:8px;align-items:center;margin:4px 0;font-size:12px';label.textContent='Source section';const select=document.createElement('select');select.setAttribute('aria-label','Source section');for(const [value,title] of [['opening','Beginning · 0:00–1:28 · 88 s'],['workshop','Previous workshop · 7:46–8:30 · 44 s']]){const o=document.createElement('option');o.value=value;o.textContent=title;select.append(o)}select.value=opening?'opening':'workshop';select.onchange=()=>{const url=new URL(location.href);if(select.value==='opening')url.searchParams.set('section','opening');else url.searchParams.delete('section');location.href=url};label.append(select);row.append(label);document.querySelector('.screen').before(row);
})();
