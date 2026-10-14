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
