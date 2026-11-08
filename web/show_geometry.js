// One mouse grammar for placed pictures and keyed video layers.
(() => {
  function change(initial,edge,dx,dy,modified,startAngle=0,endAngle=0){
    if(modified&&!edge)return {rotate:((initial.rotate||0)+(endAngle-startAngle)*180/Math.PI+540)%360-180};
    if(modified){const key=['left','right'].includes(edge)?'stretch_x':'stretch';return {[key]:Math.max(.1,Math.min(10,(initial[key]||1)+(edge==='left'?-dx:edge==='right'?dx:edge==='top'?-dy:dy)*2))};}
    const crop=[...(initial.crop||[0,0,0,0])],i={left:0,top:1,right:2,bottom:3}[edge];
    if(i==null)return {};
    const delta=[dx,dy,-dx,-dy][i],opposite=(i+2)%4;
    crop[i]=Math.max(0,Math.min(.98-crop[opposite],crop[i]+delta));return {crop};
  }
  function style(p){const c=p.crop||[0,0,0,0];return `clip-path:inset(${c[1]*100}% ${c[2]*100}% ${c[3]*100}% ${c[0]*100}%);transform:rotate(${p.rotate||0}deg) scale(${(p.flip?-1:1)*(p.stretch_x||1)},${p.stretch||1});opacity:${p.opacity??1};transform-origin:center;`;}
  function handles(p){const c=p.crop||[0,0,0,0];return `<span class="show-crop-frame" style="left:${c[0]*100}%;top:${c[1]*100}%;right:${c[2]*100}%;bottom:${c[3]*100}%">${['left','top','right','bottom'].map(edge=>`<span data-crop-edge="${edge}" title="Drag to crop · Command/Ctrl: stretch"></span>`).join('')}</span>`;}
  window.ShowGeometry={change,style,handles};
})();
