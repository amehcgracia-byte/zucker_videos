(function(root){
function time(e,t,duration){if(!Number.isFinite(duration)||duration<=0)return null;const elapsed=Math.max(0,t-e.start+(e.effectOffset||0)+(e.backgroundSourceIn||0));return {target:e.backgroundLoop===false?Math.min(elapsed,Math.max(0,duration-.04)):elapsed%duration,finished:e.backgroundLoop===false&&elapsed>=duration}}
const api={time};root.ShowBackgroundModel=api;if(typeof module!=='undefined')module.exports=api;
})(typeof window==='undefined'?globalThis:window);
