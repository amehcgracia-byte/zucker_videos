const assert=require('node:assert/strict');
const EditQueue=require('../../web/show_edit_queue.js');
(async()=>{
  const writes=[];let offline=true;
  const queue=new EditQueue(async(id,changes)=>{writes.push([id,changes]);if(id==='sofa'&&offline)throw Error('offline');});
  queue.enqueue('sofa',{at:10});queue.enqueue('sun',{duration:2});
  await assert.rejects(queue.flush(),/not saved/);
  assert.deepEqual(writes.map(x=>x[0]),['sofa','sun']); // failure must not drop the rest of the batch
  assert.deepEqual(queue.pending.get('sofa'),{at:10});
  assert.equal(queue.pending.has('sun'),false);
  queue.enqueue('sofa',{at:12});offline=false;
  await queue.flush();assert.deepEqual(writes.at(-1),['sofa',{at:12}]);assert.equal(queue.pending.size,0);
  let release;let count=0;
  const concurrent=new EditQueue(async()=>{count++;if(count===1){await new Promise(r=>release=r);throw Error('late failure');}});
  concurrent.enqueue('sofa',{size:.2});const first=concurrent.flush();
  // Wait for the in-flight writer, then queue a newer draft and a render's flush.
  await new Promise(r=>setImmediate(r));concurrent.enqueue('sofa',{size:.3});const second=concurrent.flush();release();
  await assert.rejects(first,/late failure/);await second;
  assert.equal(count,2);assert.equal(concurrent.pending.size,0);
  console.log('Show edit queue: failed saves, later effects, newer drafts and awaited retries pass');
})().catch(error=>{console.error(error);process.exitCode=1;});
