(function(root){
const keys=['x','y','scale','rotation','radius','opacity','maskScale','maskRotation','maskOffsetX','maskOffsetY'];
function pose(e){return Object.fromEntries(keys.filter(k=>Number.isFinite(e[k])).map(k=>[k,e[k]]))}
function at(e,t){const list=(e.motionKeys||[]).slice().sort((a,b)=>a.time-b.time);if(!list.length)return e;let a=list[0],b=a;for(const p of list){if(p.time<=t)a=p;if(p.time>=t){b=p;break}b=p}let u=a===b?0:Math.max(0,Math.min(1,(t-a.time)/(b.time-a.time)));if(e.motionEase!=='linear')u=u*u*(3-2*u);const out={...e};for(const k of keys){const x=a[k]??e[k],y=b[k]??x;if(Number.isFinite(x)&&Number.isFinite(y))out[k]=x+(y-x)*u}return out}
function setKey(e,t){e.motionKeys=(e.motionKeys||[]).filter(k=>Math.abs(k.time-t)>.02);e.motionKeys.push({time:t,...pose(e)});e.motionKeys.sort((a,b)=>a.time-b.time)}
function preset(e,kind){const d=e.end-e.start,a=e.start,b=e.end-.04,base=pose(e),size=e.radius||.15;e.motionEase='smooth';if(e.maskAsset)e.followFace=false;
 if(kind==='across')e.motionKeys=[{time:a,...base,x:-.25,y:.45},{time:a+d*.5,...base,x:.5,y:.32},{time:b,...base,x:1.25,y:.45}];
 if(kind==='jump')e.motionKeys=[{time:a,...base},{time:a+d*.25,...base,y:(e.y??.5)-.23,rotation:-12},{time:a+d*.5,...base},{time:a+d*.75,...base,y:(e.y??.5)-.15,rotation:12},{time:b,...base}];
 if(kind==='peek')e.motionKeys=[{time:a,...base,x:1.25},{time:a+d*.3,...base,x:.96},{time:a+d*.72,...base,x:.96},{time:b,...base,x:1.25}];
 if(kind==='grow')e.motionKeys=[{time:a,...base},{time:a+d*.5,...base,...e.type==='image'?{radius:1.2}:{scale:5}},{time:b,...base}];
 return e;
}
const api={pose,at,setKey,preset};root.ShowMotionModel=api;if(typeof module!=='undefined')module.exports=api;
})(typeof window==='undefined'?globalThis:window);
