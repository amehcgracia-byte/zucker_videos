"""Exercise the real transport handlers with the pause event emitted by load()."""
from pathlib import Path
import subprocess


def test_loading_effects_does_not_treat_the_old_sources_pause_as_a_user_pause():
    script=Path('web/show_review.js').read_text(encoding="utf-8")
    play=script[script.index('  function playScene()'):script.index('  function stepVideo(')]
    pause=script[script.index('  $("#showStageVideo").addEventListener("pause"'):script.index('  // Seconds into the scene\'s edit')]
    program='''
const assert=require('node:assert/strict');
const review={};
let onPause, quick={effects:false,scene:{id:'s1'}}, switches=[];
const scene={id:'s1',start:0,end:4}, timelineDialog={open:true};
const video={getAttribute(){return 'source.mp4'},paused:true,pause(){this.paused=true;onPause();},play(){this.paused=false;return Promise.resolve();},addEventListener(name,fn){onPause=fn;}};
const $=()=>video, currentScene=()=>scene, editedPlayhead=()=>1;
const effectsReady=()=>true, syncPlayback=()=>{}, showToast=()=>{};
function playQuick(scene,at,end,effects=false){quick={effects,scene};switches.push(effects);onPause();return true;}
'''+play+pause+'''
playScene();
assert.equal(quick.effects,true,'load pause must not discard the effects video');
assert.deepEqual(switches,[true]);
playScene();
assert.equal(video.paused,true);
assert.equal(quick.effects,false,'explicit Pause returns to editable source');
assert.deepEqual(switches,[true,false]);
'''
    subprocess.run(['node','-e',program],check=True,capture_output=True,text=True)


def test_background_preview_updates_live_view_but_discards_responses_from_before_an_edit():
    script=Path('web/show_review.js').read_text(encoding="utf-8")
    refresh=script[script.index('  async function refreshLivePreview('):script.index('  function showScenePicture(')]
    program='''
const assert=require('node:assert/strict');
let editStamp=1,backgroundPreview,resolve,label={textContent:''};
const scene={id:'s1',preview:{status:'none'}}, timelineDialog={open:false};
const currentScene=()=>scene,editorEffectsLabel=()=>{},leftOut=()=>'', $=()=>label;
let api=async()=>({scenes:[{id:'s1',preview:{status:'ready',url:'new.mp4'}}]});
const setTimeout=()=>0;
'''+refresh+'''
(async()=>{
await refreshLivePreview(scene,1);
assert.equal(scene.preview.url,'new.mp4');
assert.match(label.textContent,/Effects preview ready/);
api=()=>new Promise(r=>resolve=r);
const pending=refreshLivePreview(scene,1);editStamp=2;
resolve({scenes:[{id:'s1',preview:{status:'ready',url:'old.mp4'}}]});await pending;
assert.equal(scene.preview.url,'new.mp4','stale response cannot replace current edit');
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
    subprocess.run(['node','-e',program],check=True,capture_output=True,text=True)


def test_a_ready_preview_reaches_the_current_scene_after_the_board_was_reloaded():
    # refreshBoard replaces the scene objects; the editor's watcher must not keep writing on the
    # old copy, or Play scene (which reads the current scene) never sees the effects as ready.
    script=Path('web/show_review.js').read_text(encoding="utf-8")
    watch=script[script.index('  async function watchEditorEffects('):script.index('  $("#showStageVideo").addEventListener("pause"')]
    program='''
const assert=require('node:assert/strict');
let editorPoll=null,editorAsked='s1',editorAskedAt=Date.now(),editStamp=3,quick=null;
const old={id:'s1',preview:{status:'rendering'}}, reloaded={id:'s1',preview:{status:'rendering'}};
let current=old;
const timelineDialog={open:true}, editQueue={pending:new Map()};
const currentScene=()=>current, editorEffectsLabel=()=>{}, flushEdits=async()=>{}, $=()=>({paused:true});
const effectsReady=scene=>!!(scene&&scene.preview?.status==='ready'&&scene.preview.url);
const api=async()=>{current=reloaded;return {scenes:[{id:'s1',preview:{status:'ready',url:'new.mp4'}}]};};
const setTimeout=()=>0;
'''+watch+'''
watchEditorEffects(old).then(()=>{
  assert.equal(reloaded.preview.url,'new.mp4','the scene Play reads is the one that becomes ready');
}).catch(e=>{console.error(e);process.exitCode=1;});
'''
    subprocess.run(['node','-e',program],check=True,capture_output=True,text=True)


def test_failed_preview_request_keeps_saved_edits_and_source_playback_available():
    script=Path('web/show_review.js').read_text(encoding="utf-8")
    request=script[script.index('  async function requestScenePreview('):script.index('  async function decideScene(')]
    program="""
const assert=require('node:assert/strict');
const scene={id:'s1',preview:{status:'none'}},wantQuick=new Set(),wantEffects=new Set();
let rendered=0,label={textContent:''};
const flushEdits=async()=>{},api=async()=>{throw Error('Encoder unavailable')};
const renderScene=()=>rendered++,showToast=()=>{},$=()=>label;
"""+request+"""
requestScenePreview(scene).then(result=>{
assert.equal(result,false);assert.equal(scene.preview.status,'failed');
assert.equal(rendered,1);assert(wantQuick.has('s1'));assert(!wantEffects.has('s1'));
assert.match(label.textContent,/edits are saved/);assert.match(label.textContent,/original video/);
}).catch(error=>{console.error(error);process.exitCode=1;});
"""
    subprocess.run(['node','-e',program],check=True,capture_output=True,text=True)


def test_cold_source_does_not_pause_before_metadata_has_arrived():
    script=Path('web/show_review.js').read_text(encoding="utf-8")
    play=script[script.index('  function playQuick('):script.index('  // ---- The scene editor plays')]
    program="""
const assert=require('node:assert/strict');
let quick=null,frame,metadata,pauses=0;
const review={source_url:'/original.mp4'}, scene={id:'s1',start:5,end:6,segments:[[5,6]]};
const video={readyState:0,duration:0,currentTime:0,seeking:false,ended:false,
getAttribute(){return '/original.mp4'},pause(){pauses++},addEventListener(name,fn){metadata=fn}};
const element={hidden:false,textContent:''},transport={hidden:false};
const $=id=>id==='#showStageVideo'?video:id==='#showSceneTransport'?transport:element;
const stopQuick=()=>{quick=null},useSaved=()=>false,effectsReady=()=>false;
const liveDrawings=()=>{},editorEffectsLabel=()=>{},syncPlayback=()=>{},leftOut=()=>'',showToast=()=>{};
const sourceAt=(segments,from)=>[0,from],timelineDialog={open:false};
const requestAnimationFrame=fn=>{frame=fn;return 1};
"""+play+"""
assert.equal(playQuick(scene),true);
frame();assert.equal(pauses,0,'metadata is not a zero-length scene');
video.readyState=1;video.duration=10;metadata();
assert.equal(video.currentTime,5);frame();assert.equal(pauses,0);
video.currentTime=6;frame();assert.equal(pauses,1,'real end still pauses');
"""
    subprocess.run(['node','-e',program],check=True,capture_output=True,text=True)


def test_clay_preview_uses_scene_composition_instead_of_a_nonexistent_proposal():
    script=Path('web/show_review.js').read_text(encoding="utf-8")
    start=script.index('        const item=timelineItem(target.dataset.showPreview);')
    branch=script[start:script.index('        target.disabled=true;',start)]
    program='''
const assert=require('node:assert/strict');
const target={dataset:{showPreview:'clay:story'}},scene={id:'s1'};
const currentScene=()=>scene;
let requests=0;
const timelineItem=()=>({id:'clay:story',clay:{id:'story'}});
const requestScenePreview=async s=>{assert.equal(s,scene);requests++};
async function click(){
'''+branch+'''
throw Error('Clay must not use the proposal preview endpoint');
}
click().then(()=>assert.equal(requests,1)).catch(e=>{console.error(e);process.exitCode=1});
'''
    subprocess.run(['node','-e',program],check=True)
