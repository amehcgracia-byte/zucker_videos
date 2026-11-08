(function(root){
function build(samples,rate,fps=25){const rms=[];for(let i=0;i<samples.length;i+=Math.max(1,Math.round(rate/fps))){const end=Math.min(samples.length,i+Math.round(rate/fps));let sum=0;for(let j=i;j<end;j++)sum+=samples[j]*samples[j];rms.push(Math.sqrt(sum/Math.max(1,end-i)))}const sorted=rms.slice().sort((a,b)=>a-b),floor=Math.min(.012,sorted[Math.floor(sorted.length*.2)]||0),ceiling=Math.max(floor+.015,sorted[Math.floor(sorted.length*.95)]||.08);return{fps,rms,floor,ceiling}}
function at(data,time){if(!data||time<0)return 0;const i=Math.floor(time*data.fps);const v=(data.rms[i]||0)*.6+(data.rms[i-1]||0)*.25+(data.rms[i+1]||0)*.15;return Math.max(0,Math.min(1,(v-data.floor)/(data.ceiling-data.floor)))}
const api={build,at};root.ShowVoiceEnvelope=api;if(typeof module!=='undefined')module.exports=api;
})(typeof window==='undefined'?globalThis:window);
