const assert=require('node:assert/strict'),V=require('./show_voice_envelope.js');
const samples=new Float32Array(1000);for(let i=400;i<600;i++)samples[i]=Math.sin(i)*.5;
const data=V.build(samples,1000,25);
assert.equal(data.rms.length,25);assert.equal(V.at(data,.2),0);assert.ok(V.at(data,.48)>.6);assert.equal(V.at(data,.9),0);assert.equal(V.at(data,-1),0);assert.equal(V.at(null,.4),0);
assert.equal(V.at(V.build(new Float32Array(1000),1000),.5),0);
console.log('PASS: sampled voice opens the jaw during speech energy and closes it on silence; no synthetic oscillation');
