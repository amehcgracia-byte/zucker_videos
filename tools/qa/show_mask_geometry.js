(function(root){
function faceAt(points,frame){const near=points.find(p=>Math.abs(p[0]-frame)<=1);return near||null}
function bounds(polygon){if(!polygon?.length)return [0,0,1,1];const xs=polygon.map(p=>p[0]),ys=polygon.map(p=>p[1]);return[Math.min(...xs),Math.min(...ys),Math.max(...xs)-Math.min(...xs),Math.max(...ys)-Math.min(...ys)]}
function placement(e,face){if(e.followFace!==false){if(!face)return null;const width=face[3]*(e.maskScale||1.6);return{x:face[1]+(e.maskOffsetX||0),y:face[2]+(e.maskOffsetY||0),width,angle:face[5]+(e.maskRotation||0)*Math.PI/180,mouth:face[6]}}return{x:e.x*1280+(e.maskOffsetX||0),y:e.y*720+(e.maskOffsetY||0),width:(e.radius||.12)*1280*(e.maskScale||1),angle:((e.maskRotation||0)+(e.rotation||0))*Math.PI/180,mouth:0}}
const api={faceAt,bounds,placement};root.ShowMaskGeometry=api;if(typeof module!=='undefined')module.exports=api;
})(typeof window==='undefined'?globalThis:window);
