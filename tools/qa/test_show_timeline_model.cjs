const assert=require('node:assert/strict'),M=require('./show_timeline_model.js');let next=0;
const id=()=>`new-${++next}`;
const make=()=>({duration:44,events:[{id:'video',type:'video',source:'original',start:0,end:44,sourceIn:0},{id:'voice',type:'audio',source:'baseAudio',start:0,end:44,sourceIn:0},{id:'steam',type:'coffee',parent:'video',start:0,end:44,visibleKeys:[[0,true],[10,false]],positionKeys:[[0,100,100],[10,200,200]]}]});
const ripple=make();M.extract(ripple,5,8,true,id);assert.equal(ripple.duration,41);for(const type of ['video','audio']){const clips=ripple.events.filter(e=>e.type===type);assert.deepEqual(clips.map(e=>[e.start,e.end,e.sourceIn]),[[0,5,0],[5,41,8]]);assert.equal(clips[1].sourceIn+6-clips[1].start,9);assert.equal(clips[0].trackId,clips[1].trackId)}
const steam=ripple.events.filter(e=>e.type==='coffee')[1];assert.deepEqual(steam.visibleKeys.at(-1),[7,false]);assert.equal(steam.effectOffset,8);assert.deepEqual(steam.positionKeys.at(-1),[7,200,200]);
const lift=make();M.extract(lift,5,8,false,id);assert.equal(lift.duration,44);assert.deepEqual(lift.events.filter(e=>e.type==='video').map(e=>[e.start,e.end,e.sourceIn]),[[0,5,0],[8,44,8]]);
const split=make(),right=M.split(split,'video',6,id());assert.equal(split.duration,44);assert.equal(right.sourceIn,6);assert.equal(right.trackId,'video');assert.equal(M.split(split,right.id,6,id()),null);
const moved={duration:44,events:[{id:'clip',type:'video',start:10,end:30,sourceIn:7,trackId:'camera'}]};M.extract(moved,15,18,true,id);assert.deepEqual(moved.events.map(e=>[e.start,e.end,e.sourceIn]),[[10,15,7],[15,27,15]]);
console.log('PASS: ripple, lift, split, linked voice, source offsets, and animated keyframes');
