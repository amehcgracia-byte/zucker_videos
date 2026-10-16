"""The person chose automatic previews (16 Oct): opening a scene asks once for its
preview with effects, and the scene editor keeps it up to date; the live view of the
cut and chosen pictures never waits for that render."""
import subprocess
from pathlib import Path


def test_opening_a_scene_asks_once_for_its_effects_preview_and_the_editor_keeps_it_current():
    script = Path('web/show_review.js').read_text()
    auto = script[script.index('  function autoPreview('):script.index('  function showScenePicture(')]
    watch = script[script.index('  async function watchEditorEffects('):script.index('  $("#showStageVideo").addEventListener("pause"')]
    program = '''
const assert = require('node:assert/strict');
const wantEffects = new Set(), wantQuick = new Set(), wantSaved = new Set();
let editorPoll = null, editorAsked = null, editorAskedAt = 0, editStamp = 0, review = {};
const scene = {id:'scene-2',preview:{status:'none'}};
const scenes = () => [{id:'scene-1'}, scene, {id:'scene-3'}];
const timelineDialog = {open:true};
const currentScene = () => scene;
const editorEffectsLabel = () => {};
const effectsReady = () => false;
const flushEdits = async () => {};
const editQueue = {pending:new Map()};
const refreshBoard = () => {};
const quick = null;
const calls = [];
const api = async (path, options = {}) => { calls.push([options.method || 'GET', path, options.body || '']); return {scenes:[{id:'scene-2',preview:{status:'queued'}}]}; };
const setTimeout = () => 0;
''' + auto + watch + '''
autoPreview(scene);
autoPreview(scene);                                   // asked once, not again
assert(wantEffects.has(scene.id));
assert.deepEqual(calls.filter(([method]) => method === 'POST').map(([, path, body]) => [path, body]),
                 [['/show/scenes/scene-2/preview', JSON.stringify({next: ['scene-3']})]]);
scene.preview = {status: 'none'}; calls.length = 0;
watchEditorEffects(scene).then(() => {
  assert.deepEqual(calls.map(([method, path]) => [method, path]),
                   [['POST', '/show/scenes/scene-2/preview'], ['GET', '/show/scenes?current=scene-2']]);
}).catch(error => {console.error(error); process.exitCode = 1;});
'''
    subprocess.run(['node', '-e', program], check=True, capture_output=True, text=True)
