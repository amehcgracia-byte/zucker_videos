/* Source-timed narrative poses: each cue happens once, then its pose holds. */
(function(root){
'use strict';
function poseAt(shot,time){const keys=shot.poseKeys||[];let pose=keys.length?keys[0].pose:(shot.poses?.[0]??0);for(const key of keys){if(key.time>time)break;pose=key.pose}return pose}
const api={poseAt};root.ShowClayPose=api;if(typeof module!=='undefined')module.exports=api;
})(typeof window==='undefined'?globalThis:window);
