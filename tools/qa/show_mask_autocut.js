/* On-device face outline. Only the model/runtime are downloaded; photos stay here. */
(function(){
let detectorPromise;
const oval=[10,338,297,332,284,251,389,356,454,323,361,288,397,365,379,378,400,377,152,148,176,149,150,136,172,58,132,93,234,127,162,21,54,103,67,109];
async function cut(image){
 if(!detectorPromise)detectorPromise=(async()=>{const {FaceLandmarker,FilesetResolver}=await import('https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.21/vision_bundle.mjs');const vision=await FilesetResolver.forVisionTasks('https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.21/wasm');return FaceLandmarker.createFromOptions(vision,{baseOptions:{modelAssetPath:'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task',delegate:'CPU'},runningMode:'IMAGE',numFaces:1,minFaceDetectionConfidence:.5})})().catch(err=>{detectorPromise=null;throw err});
 const detector=await detectorPromise,result=detector.detect(image),p=result.faceLandmarks?.[0];if(!p)throw Error('No human face detected. Keep a transparent character image or use Outline to select it.');
 const clamp=n=>Math.max(0,Math.min(1,n)),polygon=oval.map(i=>[clamp(p[i].x),clamp(p[i].y)]),left=Math.min(p[61].x,p[291].x),right=Math.max(p[61].x,p[291].x),top=Math.min(p[0].y,p[13].y),bottom=Math.max(p[17].y,p[14].y),pad=(right-left)*.08;
 return {polygon,mouth:[clamp(left-pad),clamp(top-pad),Math.min(1-left+pad,right-left+pad*2),Math.min(1-top+pad,bottom-top+pad*3)],method:'MediaPipe Face Landmarker 0.10.21 · local face oval, not hair segmentation'};
}
window.ShowMaskAuto={cut};
})();
