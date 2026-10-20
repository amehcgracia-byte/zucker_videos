// One edited clock for the transcript, timeline, source player and timed pictures.
(function(root){
  const fit=(scene,at,duration)=>{
    duration=Math.max(.04,Math.min(Number(duration)||.04,scene.end-scene.start));
    at=Math.max(scene.start,Math.min(Number(at)||scene.start,scene.end-duration));
    return {at:Math.round(at*1000)/1000,duration:Math.round(duration*1000)/1000};
  };
  const sourceAt=(segments,seconds)=>{
    let left=Math.max(0,seconds);
    for(const [index,[a,b]] of segments.entries()){
      if(left<b-a)return [index,left];
      left-=b-a;
    }
    return [Math.max(0,segments.length-1),Math.max(0,(segments.at(-1)?.[1]||0)-(segments.at(-1)?.[0]||0)-.04)];
  };
  const editedAt=(scene,segments,sourceTime,index=0,pending=null)=>{
    if(pending!=null)return pending;
    const found=segments.findIndex(([a,b])=>a<=sourceTime && sourceTime<b);
    index=found<0?index:found;
    return scene.start+segments.slice(0,index).reduce((sum,[a,b])=>sum+b-a,0)+sourceTime-segments[index][0];
  };
  const visible=(item,at)=>!!item && item.status!=='discarded' && at>=item.at-.00001 && at<item.at+item.duration-.00001;
  const clock={fit,sourceAt,editedAt,visible};
  if(typeof module!=='undefined' && module.exports)module.exports=clock;
  else root.ShowClock=clock;
})(typeof window==='undefined'?{}:window);
