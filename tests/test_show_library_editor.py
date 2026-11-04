"""Library placement returns the exact persisted piece for the shared editor."""
from pathlib import Path
import subprocess


def test_library_media_editor_targets_saved_piece_and_preserves_selected_timing():
    source = Path('web/show_review.js').read_text(encoding="utf-8")
    handler = source[source.index('  async function placeLibraryMedia('):source.index('  async function addLibraryFiles(')]
    program = r'''
const assert=require('node:assert/strict');
let review={project_id:'p1'},libraryWords=null,request,added,closed=0;
const scene={id:'s1',start:10,end:20};
const currentScene=()=>scene,editedPlayhead=()=>12,showToast=()=>{};
const libraryDialog={close(){closed++}};
const timelineItem=id=>added?.id===id?added:null;
const clipAdded=result=>{added=result.proposal};
let api=async(url,body)=>{request=JSON.parse(body.body);return {proposal:{id:'persisted',kind:'jingle'}}};
''' + handler + r'''
(async()=>{
const piece=await placeLibraryMedia('audio');
assert.equal(piece.id,'persisted');assert.equal(piece.kind,'jingle');
assert.equal(request.at,12);assert.equal('duration' in request,false);
await placeLibraryMedia('video',scene,14,{start:14,end:14.12});
assert.ok(Math.abs(request.duration-.12)<1e-9);
assert.equal(closed,2);
api=async()=>{review.project_id='p2';return {proposal:{id:'wrong-project'}}};
assert.equal(await placeLibraryMedia('video'),undefined);
assert.equal(added.id,'persisted');assert.equal(closed,2);
})().catch(e=>{console.error(e);process.exitCode=1});
'''
    subprocess.run(['node','-e',program],check=True)


def test_library_picture_editor_receives_persisted_overlay_only():
    source=Path('web/show_review.js').read_text(encoding="utf-8")
    handler=source[source.index('  async function addToScene('):source.index('  // The Show editing desk')]
    program=r'''
const assert=require('node:assert/strict');
const scene={id:'s1',start:0,end:10,overlays:[]},open=new Set();
const currentScene=()=>scene,showToast=()=>{},renderScene=()=>{};
const fitTiming=(s,at,duration)=>({at,duration});
const timelineItem=id=>({id,overlay:scene.overlays.find(o=>'sticker:'+o.id===id)});
let saveOverlays=async()=>true;
''' + handler + r'''
(async()=>{
const placed=await addToScene('original.png',{sceneId:'s1',at:3,duration:.12});
assert.equal(placed.overlay,scene.overlays[0]);
assert.equal(placed.overlay.at,3);assert.equal(placed.overlay.duration,.12);
saveOverlays=async()=>false;
await assert.rejects(()=>addToScene('other.png'),/could not be placed/);
assert.equal(scene.overlays.length,1);
await assert.rejects(()=>addToScene('other.png',{sceneId:'s2'}),/original scene/);
assert.equal(scene.overlays.length,1);
})().catch(e=>{console.error(e);process.exitCode=1});
'''
    subprocess.run(['node','-e',program],check=True)
