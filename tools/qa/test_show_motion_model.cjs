const assert=require('node:assert/strict');
const M=require('./show_motion_model.js'),G=require('./show_mask_geometry.js');
const e={type:'image',start:2,end:8,x:.5,y:.4,radius:.2,opacity:1};M.preset(e,'peek');assert.ok(M.at(e,2).x>1);assert.equal(M.at(e,4.1).x,.96);assert.ok(M.at(e,7.96).x>1);
M.preset(e,'grow');assert.equal(M.at(e,5).radius,1.2);
const linear={x:0,y:0,motionEase:'linear',motionKeys:[{time:0,x:-.2,y:.4},{time:2,x:1.2,y:.6}]};assert.ok(Math.abs(M.at(linear,1).x-.5)<1e-10);assert.equal(M.at(linear,1).y,.5);assert.equal(linear.x,0);
M.setKey(linear,1);assert.equal(linear.motionKeys.length,3);M.setKey(linear,1);assert.equal(linear.motionKeys.length,3);
assert.equal(G.faceAt([[10,1,2,100,120,0,.2]],20),null);
assert.equal(G.placement({followFace:true},null),null);assert.equal(G.placement({followFace:false,x:1.1,y:.5,radius:.2},null).x,1408);
assert.deepEqual(G.bounds([[.1,.2],[.8,.2],[.8,.9]]),[.1,.2,.7000000000000001,.7]);
console.log('PASS: offscreen paths, full-frame growth, immutable interpolation, key updates and missing-face behaviour');

const manual=G.placement({followFace:false,x:1.1,y:.5,radius:.2,maskScale:2,maskOffsetX:10,rotation:12,maskRotation:3},null);
assert.equal(manual.width,512);assert.equal(manual.x,1418);assert.equal(manual.angle,15*Math.PI/180);
