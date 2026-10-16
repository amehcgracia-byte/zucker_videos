"""Editing/opening the live view must not schedule video rendering."""
import json
import subprocess
from pathlib import Path


def test_live_view_does_not_start_render_jobs():
    script = Path('web/show_review.js').read_text()
    auto = script[script.index('  function autoPreview('):script.index('  function showScenePicture(')]
    watch = script[script.index('  async function watchEditorEffects('):script.index('  $("#showStageVideo").addEventListener("pause"')]
    program = '''
const assert = require('node:assert/strict');
const wantEffects = new Set(), wantQuick = new Set();
let editorPoll = null, editStamp = 0;
const scene = {id:'scene-1',preview:{status:'none'}};
const timelineDialog = {open:true};
const currentScene = () => scene;
const editorEffectsLabel = () => {};
const effectsReady = () => false;
const api = () => { throw new Error('Live editing must not request a render or poll'); };
''' + auto + watch + '''
autoPreview(scene);
assert(wantQuick.has(scene.id));
assert(!wantEffects.has(scene.id));
watchEditorEffects(scene).catch(error => {console.error(error);process.exitCode=1;});
'''
    subprocess.run(['node', '-e', program], check=True, capture_output=True, text=True)
