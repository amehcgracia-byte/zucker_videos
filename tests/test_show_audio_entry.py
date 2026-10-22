"""The common editor must open audio controls on the selected track."""
from pathlib import Path
import subprocess


def test_common_audio_editor_opens_mixer_and_focuses_the_selected_track():
    review=Path('web/show_review.js').read_text()
    start=review.index('  function editMedia(item) {')
    dispatch=review[start:review.index('    selectedEffect=item.id;',start)]+'\n}'
    audio=Path('web/show_audio.js').read_text()
    start=audio.index('  async function open(')
    opening=audio[start:audio.index('  async function save(',start)]
    program=r'''
const assert=require('node:assert/strict');
let opened,focused=0,scrolled=0,message;
const ShowAudio={open(id){opened=id}};
''' + dispatch + r'''
editMedia({id:'music-1',kind:'jingle'});
assert.equal(opened,'music-1');
const window={ShowReview:{active:()=>true}};
const showToast=()=>{},ensure=()=>{},refresh=async()=>{},status=s=>message=s;
const CSS={escape:s=>s};
const row={classList:{add(){}},scrollIntoView(){scrolled++},querySelector(s){return s==='.mixer-name'?{textContent:'My music'}:{focus(){focused++}}}};
const dialog={open:false,showModal(){this.open=true},querySelector(s){return s.includes('music-1')?row:null}};
''' + opening + r'''
(async()=>{
await open('music-1');assert.equal(focused,1);assert.equal(scrolled,1);assert.match(message,/My music/);
await open('missing');assert.match(message,/no active audio track/);
await open({type:'click'});assert.match(message,/saved in this project/);
})().catch(e=>{console.error(e);process.exitCode=1});
'''
    subprocess.run(['node','-e',program],check=True)
