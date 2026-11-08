/* Deterministic pigment and gesture geometry. No generated/reconstructed pixels. */
(function(root){
'use strict';
function hash(n){let v=(n|0)^0x9e3779b9;v=Math.imul(v^(v>>>16),0x21f0aaad);v=Math.imul(v^(v>>>15),0x735a2d97);return((v^(v>>>15))>>>0)/4294967296}
function resample(points,spacing=1.5){if(!points.length)return[];const out=[points[0].slice()];let left=spacing;for(let i=1;i<points.length;i++){let a=points[i-1].slice(),b=points[i],distance=Math.hypot(b[0]-a[0],b[1]-a[1]);while(distance>=left&&distance>0){const u=left/distance;a=[a[0]+(b[0]-a[0])*u,a[1]+(b[1]-a[1])*u];out.push(a);distance=Math.hypot(b[0]-a[0],b[1]-a[1]);left=spacing}left-=distance}return out}
function shape(points,kind,radius=60,progress=1,turns=2.5){if(!points.length)return[];const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]),cx=(Math.min(...xs)+Math.max(...xs))/2,cy=(Math.min(...ys)+Math.max(...ys))/2;const spread=Math.max(Math.max(...xs)-Math.min(...xs),Math.max(...ys)-Math.min(...ys))/2,r=points.length>1?Math.max(18,spread):radius;const count=240,out=[];for(let i=0;i<=Math.floor(count*Math.max(0,Math.min(1,progress)));i++){const q=i/count,a=-Math.PI/2+q*Math.PI*2*(kind==='spiral'?turns:1),rr=r*(kind==='spiral'?1-q:1),wobble=1+.025*Math.sin(a*3+.8)+.018*Math.sin(a*7);out.push([cx+Math.cos(a)*rr*wobble,cy+Math.sin(a)*rr*wobble*.91])}return out}
const stamps=new Map();
function pigment(ctx,points,e,opacity=1){const width=Math.max(1,Number(e.width)||5),brush=e.brush||'crayon',color=e.color||'#e7b64c',key=[brush,color,Math.round(width*2)].join(':');let stamp=stamps.get(key);if(!stamp){stamp=document.createElement('canvas');stamp.width=stamp.height=64;const c=stamp.getContext('2d');c.fillStyle=color;const grain=brush==='marker'?580:brush==='pencil'?135:95;for(let i=0;i<grain;i++){const a=hash(i*4)*Math.PI*2,r=Math.sqrt(hash(i*4+1))*29,x=32+Math.cos(a)*r,y=32+Math.sin(a)*r;c.globalAlpha=(brush==='marker'?.22:.3)+hash(i*4+2)*.5;c.fillRect(x,y,brush==='marker'?3:1+hash(i*4+3)*2,brush==='pencil'?1:2)}stamps.set(key,stamp);if(stamps.size>60)stamps.delete(stamps.keys().next().value)}const pts=resample(points,Math.max(.7,width*.22));ctx.save();ctx.globalAlpha*=opacity*(brush==='marker'?.7:.8);for(let i=0;i<pts.length;i++){const [x,y]=pts[i],pressure=.82+.18*Math.sin(x*.087+y*.061),size=width*2.3*pressure;ctx.drawImage(stamp,x-size/2,y-size/2,size,size)}ctx.restore()}
function reveal(path,progress){const points=resample(path,1.4);return points.slice(0,Math.max(1,Math.ceil(points.length*Math.max(0,Math.min(1,progress)))))}
function partMatrix(anchor,target,scale=1,degrees=0){const angle=degrees*Math.PI/180,c=Math.cos(angle)*scale,s=Math.sin(angle)*scale;return[c,s,-s,c,target[0]-c*anchor[0]+s*anchor[1],target[1]-s*anchor[0]-c*anchor[1]]}
function timedTrails(runs,frame,fps,width,height,tailSeconds=4){
 const out=[];for(const run of runs){if(!run.length||frame<run[0][0]||frame>run.at(-1)[0]+fps*tailSeconds)continue;
 const points=run.filter(p=>p[0]<=frame&&p[0]>=frame-fps*tailSeconds).map(p=>[p[1]*1280/width,p[2]*720/height]);
 if(points.length>1)out.push({points,opacity:Math.max(0,Math.min(1,(run.at(-1)[0]+fps*tailSeconds-frame)/fps))});
 }return out;
}
function panCamera(map,dx,dy){const s=map.scale,hw=640/s,hh=360/s;return {x:Math.max(hw,Math.min(1280-hw,map.x+hw-dx/s))/1280,y:Math.max(hh,Math.min(720-hh,map.y+hh-dy/s))/720}}
const api={panCamera,timedTrails,hash,resample,shape,pigment,reveal,partMatrix};root.ShowPaint=api;if(typeof module!=='undefined')module.exports=api;
})(typeof window==='undefined'?globalThis:window);
