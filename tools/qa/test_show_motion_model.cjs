const assert=require('node:assert/strict');
const M=require('./show_motion_model.js'),G=require('./show_mask_geometry.js');
const e={type:'image',start:2,end:8,x:.5,y:.4,radius:.2,opacity:1};M.preset(e,'peek');assert.ok(M.at(e,2).x>1);assert.equal(M.at(e,4.1).x,.96);assert.ok(M.at(e,7.96).x>1);
M.preset(e,'grow');assert.equal(M.at(e,5).radius,1.2);
const linear={x:0,y:0,motionEase:'linear',motionKeys:[{time:0,x:-.2,y:.4},{time:2,x:1.2,y:.6}]};assert.ok(Math.abs(M.at(linear,1).x-.5)<1e-10);assert.equal(M.at(linear,1).y,.5);assert.equal(linear.x,0);
M.setKey(linear,1);assert.equal(linear.motionKeys.length,3);M.setKey(linear,1);assert.equal(linear.motionKeys.length,3);
assert.equal(G.faceAt([[10,1,2,100,120,0,.2]],20),null);
assert.equal(G.placement({followFace:true,x:.5,y:.5},null).x,640);assert.equal(G.placement({followFace:false,x:1.1,y:.5,radius:.2},null).x,1408);
assert.deepEqual(G.bounds([[.1,.2],[.8,.2],[.8,.9]]),[.1,.2,.7000000000000001,.7]);
console.log('PASS: offscreen paths, full-frame growth, immutable interpolation, key updates and missing-face behaviour');

const manual=G.placement({followFace:false,x:1.1,y:.5,radius:.2,maskScale:2,maskOffsetX:10,rotation:12,maskRotation:3},null);
assert.equal(manual.width,512);assert.equal(manual.x,1418);assert.equal(manual.angle,15*Math.PI/180);

const track=[[10,100,200,120,150,0,.3],[20,140,210,125,155,.1,.4]];
assert.equal(G.faceAt(track,15,true),track[0]);assert.equal(G.faceAt(track,0,true),track[0]);assert.equal(G.faceAt(track,80,true),track[1]);
assert.deepEqual(G.dragOffset({followFace:true,maskOffsetX:12,maskOffsetY:-8},10,-30),{maskOffsetX:22,maskOffsetY:-38});
assert.deepEqual(G.dragOffset({followFace:false,x:.5,y:.5},128,-72),{x:.6,y:.4});
assert.equal(G.mouthOpen({start:0,animateMouth:false},.2,1),0);
assert.equal(G.mouthOpen({start:0,mouthMode:'manual',mouthOpening:.7},.2),.7);
assert.equal(G.mouthOpen({start:0,mouthMode:'detected',mouthOpening:2},.2,.3),.6);
assert.ok(G.mouthOpen({start:0},.1,0)>0);
console.log('PASS: masks persist across detection gaps, drag without disabling tracking, and expose independent mouth opening');
