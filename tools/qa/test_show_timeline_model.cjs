const assert=require('node:assert/strict'),M=require('./show_timeline_model.js');let next=0;
const id=()=>`new-${++next}`;
const make=()=>({duration:44,events:[{id:'video',type:'video',source:'original',start:0,end:44,sourceIn:0},{id:'voice',type:'audio',source:'baseAudio',start:0,end:44,sourceIn:0},{id:'steam',type:'coffee',parent:'video',start:0,end:44,visibleKeys:[[0,true],[10,false]],positionKeys:[[0,100,100],[10,200,200]]}]});
const ripple=make();M.extract(ripple,5,8,true,id);assert.equal(ripple.duration,41);for(const type of ['video','audio']){const clips=ripple.events.filter(e=>e.type===type);assert.deepEqual(clips.map(e=>[e.start,e.end,e.sourceIn]),[[0,5,0],[5,41,8]]);assert.equal(clips[1].sourceIn+6-clips[1].start,9);assert.equal(clips[0].trackId,clips[1].trackId)}
const steam=ripple.events.filter(e=>e.type==='coffee')[1];assert.deepEqual(steam.visibleKeys.at(-1),[7,false]);assert.equal(steam.effectOffset,8);assert.deepEqual(steam.positionKeys.at(-1),[7,200,200]);
const lift=make();M.extract(lift,5,8,false,id);assert.equal(lift.duration,44);assert.deepEqual(lift.events.filter(e=>e.type==='video').map(e=>[e.start,e.end,e.sourceIn]),[[0,5,0],[8,44,8]]);
const split=make(),right=M.split(split,'video',6,id());assert.equal(split.duration,44);assert.equal(right.sourceIn,6);assert.equal(right.trackId,'video');assert.equal(M.split(split,right.id,6,id()),null);
const moved={duration:44,events:[{id:'clip',type:'video',start:10,end:30,sourceIn:7,trackId:'camera'}]};M.extract(moved,15,18,true,id);assert.deepEqual(moved.events.map(e=>[e.start,e.end,e.sourceIn]),[[10,15,7],[15,27,15]]);
console.log('PASS: ripple, lift, split, linked voice, source offsets, and animated keyframes');
const av={duration:44,events:[{id:'v',type:'video',start:0,end:44,sourceIn:0,linkId:'av'},{id:'a',type:'audio',start:0,end:44,sourceIn:0,linkId:'av'}]};const vr=M.splitLinked(av,'v',12,id);const ar=av.events.find(e=>e.type==='audio'&&e.start===12);assert.equal(ar.sourceIn,12);assert.equal(vr.linkId,ar.linkId);M.removeLinked(av,vr.id);assert.equal(av.events.length,2);assert.equal(av.events[0].end,12);
M.unlink(av,'v');assert.equal(av.events.every(e=>e.linkId===null&&e.unlinked),true);const unlinked=M.splitLinked(av,'v',6,id);assert.equal(unlinked.sourceIn,6);assert.equal(av.events.filter(e=>e.type==='audio').length,1);console.log('PASS: linked split/delete and explicit unlink');
const trim={events:[{id:'tv',type:'video',start:10,end:20,sourceIn:4,linkId:'trim'},{id:'ta',type:'audio',start:10,end:20,sourceIn:4,linkId:'trim'}]},snap=JSON.parse(JSON.stringify(trim));M.retimeLinked(trim,'tv',12,20,snap,true);assert.deepEqual(trim.events.map(e=>[e.start,e.end,e.sourceIn]),[[12,20,6],[12,20,6]]);M.retimeLinked(trim,'tv',15,25,snap,false);assert.deepEqual(trim.events.map(e=>[e.start,e.end]),[[15,25],[15,25]]);console.log('PASS: linked trim and move');
{
 const {segment,effectLane,split}=require('./show_timeline_model.js');
 const original={id:'finger-a',type:'finger',autoHands:true,parent:'base',start:4,end:8,contentShift:2,color:'#abc'};
 const pasted=segment(original,4,8,20,'finger-b');
 assert.strictEqual(effectLane(original),effectLane(pasted));
 assert.strictEqual(6+original.contentShift,22+pasted.contentShift); // same gesture sample after moving
 pasted.effectTrack='independent';assert.notStrictEqual(effectLane(original),effectLane(pasted));
 const r={events:[original]};split(r,'finger-a',6,'finger-c');
 assert.strictEqual(r.events.length,2);assert.strictEqual(effectLane(r.events[0]),effectLane(r.events[1]));
 assert.strictEqual(r.events[1].start,6);assert.strictEqual(r.events[1].end,8);
 console.log('PASS: effect copies/splits share a lane, preserve gesture time, and allow explicit independent layers');
}
