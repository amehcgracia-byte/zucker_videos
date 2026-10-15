const assert=require('node:assert/strict');
const {poseAt}=require('./show_clay_pose_model.js');
const shot={poses:[0,1,2],poseKeys:[{time:4.24,pose:0},{time:5.16,pose:1},{time:7.45,pose:2}]};
assert.equal(poseAt(shot,4.24),0);
assert.equal(poseAt(shot,5.15),0);
assert.equal(poseAt(shot,5.16),1);
assert.equal(poseAt(shot,7),1); // Quiet hold, not an elapsed-time loop.
assert.equal(poseAt(shot,7.45),2);
assert.equal(poseAt(shot,100),2); // End holds forever; no implicit wrap.
assert.equal(poseAt({...shot,start:6},6),1); // Retiming a cut keeps source-clock gestures.
assert.equal(poseAt({poses:[3,4,5]},100),3); // Legacy data is static unless cued.
console.log('PASS: word-timed poses, quiet holds, no looping, and source-clock cut retiming');
