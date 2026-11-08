// Run: node tools/qa/show_clock_test.cjs. No DOM mocks: production clock functions.
const assert=require('node:assert/strict');
const clock=require('../../web/show_clock.js');
const scene={start:23.833,end:48.833};
const spans=[[92.4,94.067],[94.067,95.333],[99.433,100.767]];
assert.deepEqual(clock.sourceAt(spans,0),[0,0]);
assert.deepEqual(clock.sourceAt(spans,1.667),[1,1.667-(94.067-92.4)]);
assert.ok(Math.abs(clock.editedAt(scene,spans,99.933)-27.266)<.00001);
assert.equal(clock.editedAt(scene,spans,0,0,32.65),32.65); // wait for the source seek, not the old frame's time
const item={at:32.65,duration:1.78,status:'approved'};
assert.equal(clock.visible(item,32.64),false);
assert.equal(clock.visible(item,32.65),true);
assert.equal(clock.visible(item,34.429),true);
assert.equal(clock.visible(item,34.43),false);
assert.equal(clock.visible({...item,status:'discarded'},33),false);
const trimmed={...item,duration:.08};
assert.equal(clock.visible(trimmed,32.70),true);
assert.equal(clock.visible(trimmed,32.74),false); // duration is not just metadata
const moved={...item,...clock.fit(scene,40,.12)};
assert.equal(clock.visible(moved,33),false);
assert.equal(clock.visible(moved,40.04),true);
assert.deepEqual(clock.fit(scene,100,2),{at:46.833,duration:2});
assert.deepEqual(clock.fit(scene,0,.01),{at:23.833,duration:.04});
assert.equal(clock.sourceAt(spans,999)[0],spans.length-1); // never jump back to frame zero at the end
console.log('Show clock: source cuts, pending seeks, visibility, trimming and moves pass');
