const assert=require('node:assert/strict');
const {shape,resample,reveal,hash}=require('./show_paint_tools.js');
const path=[[800,180],[700,240],[790,310],[890,220]];
// A partially revealed shape is exactly a prefix of a fixed canvas shape.
// It must never recenter on the current fingertip as the original preview did.
for(const kind of ['circle','spiral']){
 const full=shape(path,kind,60,1),partial=shape(path,kind,60,.4);
 assert.deepEqual(partial,full.slice(0,partial.length));
 assert.ok(full.every(p=>p.every(Number.isFinite)));
 assert.ok(Math.abs(full[0][0]-795)<1);
}
assert.deepEqual(resample([[0,0],[10,0]],2),[[0,0],[2,0],[4,0],[6,0],[8,0],[10,0]]);
assert.deepEqual(reveal([[0,0],[10,0]],.5),resample([[0,0],[10,0]],1.4).slice(0,4));
assert.deepEqual(shape([],'circle'),[]);
assert.equal(hash(35),hash(35));assert.notEqual(hash(35),hash(36));
console.log('PASS: fixed gesture anchors, stable shape reveal, arc-length sampling and deterministic pigment seeds');
const {partMatrix}=require('./show_paint_tools.js');
function apply(m,p){return[m[0]*p[0]+m[2]*p[1]+m[4],m[1]*p[0]+m[3]*p[1]+m[5]]}
for(const degrees of [-35,0,90]){
 const matrix=partMatrix([840,320],[840,320],1.4,degrees),neck=apply(matrix,[840,320]);
 assert.ok(Math.abs(neck[0]-840)<1e-9&&Math.abs(neck[1]-320)<1e-9,'The enlarged head must stay anchored at the neck');
}
const moved=partMatrix([840,320],[600,180],1,90);
const anchor=apply(moved,[840,320]);assert.ok(Math.abs(anchor[0]-600)<1e-9&&Math.abs(anchor[1]-180)<1e-9);
const right=apply(moved,[850,320]);assert.ok(Math.abs(right[0]-600)<1e-9&&Math.abs(right[1]-190)<1e-9);
console.log('PASS: head/region scale and rotation preserve their source anchor and translate independently');
