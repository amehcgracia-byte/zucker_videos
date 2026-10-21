"""A tracking template must be captured from the same frame the user marked."""
from pathlib import Path
import subprocess


def test_follow_selection_pauses_and_records_its_owner():
    source=Path('web/show_review.js').read_text()
    start=source.index('  document.addEventListener("pointerdown", (event) => {',source.index('  function steamSave('))
    end=source.index('  document.addEventListener("pointermove"',start)
    handler=source[start:end]
    script=r'''
const assert=require('node:assert/strict');
class Element {closest(){return null}}
let down,steamDrag=null,steamBoxFor='layer',pauses=0;
const review={project_id:'project'},currentScene=()=>({id:'scene'});
const video={readyState:2,currentTime:3.25,pause(){pauses++}};
const layer={getBoundingClientRect:()=>({left:0,top:0,right:640,bottom:360,width:640,height:360})};
const $=s=>s==='#showStageLayer'?layer:video;
const timelineItem=()=>({id:'layer'}),showToast=()=>{};
const document={addEventListener(type,fn){down=fn}};
''' + handler + r'''
const event={target:new Element(),clientX:480,clientY:180,preventDefault(){},stopPropagation(){}};
down(event);
assert.equal(pauses,1);
assert.equal(steamDrag.videoTime,3.25);
assert.equal(steamDrag.projectId,'project');assert.equal(steamDrag.sceneId,'scene');
assert.deepEqual(steamDrag.start,{x:.75,y:.5});
steamDrag=null;video.readyState=1;down(event);
assert.equal(steamDrag,null);assert.equal(pauses,1);
'''
    subprocess.run(['node','-e',script],check=True)
