/* Context owns its tools. Editable text keeps the browser/native text menu. */
(() => {
  'use strict';
  const prior=context;
  context=function(x,y){
    prior(x,y);
    const menu=document.querySelector('#context');
    const buttons=[...menu.children];
    const groups=new Map([['Cuts and audio',[]],['Effects and animation',[]],['Edit selected element',[]]]);
    for(const button of buttons){
      const label=button.textContent;
      if(/^(Copy|Cut ·|Paste|Duplicate|Delete|Undo|Redo)/.test(label))continue;
      const group=/Split|Ripple|Unlink|audio/i.test(label)?'Cuts and audio':/Animation|Masks|effect|draw|paint|spiral|math|steam/i.test(label)?'Effects and animation':'Edit selected element';
      groups.get(group).push(button);
    }
    for(const [name,items] of groups){
      if(!items.length)continue;
      const group=document.createElement('details'), title=document.createElement('summary'), body=document.createElement('div');
      group.className='mouseGroup';title.textContent=name+' ▸';body.append(...items);group.append(title,body);menu.append(group);
    }
    menu.classList.toggle('flyLeft',x+540>innerWidth);
    menu.style.maxHeight='80vh';menu.style.overflow='visible';
    menu.style.top=Math.max(8,Math.min(y,innerHeight-menu.offsetHeight-8))+'px';
  };
  const css=document.createElement('style');css.textContent=`
  #context .mouseGroup{position:relative}#context .mouseGroup>summary{cursor:pointer;padding:9px;list-style:none}
  #context .mouseGroup>div{display:none;position:absolute;left:100%;top:0;width:260px;max-height:70vh;overflow:auto;background:#203026;border:1px solid #758267;border-radius:8px;padding:8px;overscroll-behavior:contain}
  #context .mouseGroup:hover>div,#context .mouseGroup[open]>div,#context .mouseGroup:focus-within>div{display:grid;gap:4px}
  #context.flyLeft .mouseGroup>div{left:auto;right:100%}
  .studioSide,#timeline,.inspector,#context{overscroll-behavior:contain}
  @media(max-width:700px){#context .mouseGroup>div{position:static;width:auto}}
  `;document.head.append(css);
  canvas.addEventListener('wheel',event=>{
    if(!recipe)return;
    event.preventDefault();event.stopPropagation();pausePreview();
    const selected=current();
    if((event.ctrlKey||event.metaKey)&&selected&&['cut','zoom'].includes(selected.type)){
      checkpoint();selected.scale=Math.max(1,Math.min(6,(selected.scale||1)+(event.deltaY<0?.05:-.05)));store();inspect();
    } else seek(Math.max(0,Math.min(D,playhead+(event.deltaY>0?.2:-.2))));
  },{passive:false});
})();
