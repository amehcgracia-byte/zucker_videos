"""A voice recorded for a character is heard: it talks with it even without a marked mouth,
and saving it from a "character speaks" card makes it take that effect's place."""
import json
import subprocess
from pathlib import Path

from PIL import Image

from core import show_scenes


def test_a_character_with_a_voice_talks_even_without_a_marked_mouth(tmp_path):
    folder = tmp_path / "Characters" / "Me"
    folder.mkdir(parents=True)
    Image.new("RGBA", (40, 60), (200, 100, 50, 255)).save(folder / "cutout.png")
    voice = [str(tmp_path / "voice.wav"), 0, 2.5]
    (folder / "character.json").write_text(json.dumps({"name": "Me", "mouth": None, "eyes": [], "pivot": [0.5, 1], "voice": voice}))
    document = {"scenes": [{"id": "scene-1", "start": 0, "end": 10, "decision": "accepted", "overlays": [
        {"id": "a", "asset": str(folder / "cutout.png"), "x": 0, "y": 0, "width": 30, "at": 4, "duration": 3, "action": "talk"}]}]}
    items = show_scenes.overlay_items(document)
    assert len(items) == 1 and items[0]["insert"] == "character" and items[0]["voice"] == voice
    assert "mouth" not in items[0]["overlays"][0]                       # nothing to move, the voice is heard


def test_saving_in_the_studio_with_a_voice_talks_and_takes_the_proposed_characters_place():
    script = Path("web/show_characters.js").read_text()
    save = script[script.index("  async function save() {"):script.index("  window.ShowPhotoStudio = {")]
    program = '''
const assert = require('node:assert/strict');
const calls = [];
const fields = {'#showCharacterVoice': {value: '0'}, '#showCharacterName': {value: 'Me'}, '#showCharacterStudio': {hidden: false}};
const $ = (s) => fields[s];
const document = {querySelector: () => ({value: 'eyes'})};
let state = {voices: [{source: '/v.wav', start: 0, length: 2}], photo: {photo: '/p.png'}, cut: {mask: 'm', method: 'x'}, eyes: [], mouth: null,
             placement: {sceneId: 's', at: 4, duration: 3, action: 'look', fromProposal: 'character-1'}};
const api = async () => ({cutout: '/c/cutout.png', name: 'Me'});
const showToast = () => {};
const chosenVoice = () => state.voices[0];          // the chosen voice, trimmed (here untouched)
const window = {ShowReview: {refreshLibrary: async () => {}, addToScene: async (asset, placement) => calls.push(['add', placement.action]),
                rigSaved: async () => calls.push(['rig']), characterTakesPlace: async (id) => calls.push(['replace', id])}};
''' + save + '''
save().then(() => {
  assert.deepEqual(calls, [['add', 'talk'], ['replace', 'character-1']]);
}).catch((e) => { console.error(e); process.exitCode = 1; });
'''
    subprocess.run(["node", "-e", program], check=True, capture_output=True, text=True)


def test_a_character_can_mouth_the_hosts_own_words_instead_of_its_voice(tmp_path):
    folder = tmp_path / "Characters" / "Me"
    folder.mkdir(parents=True)
    Image.new("RGBA", (40, 60), (200, 100, 50, 255)).save(folder / "cutout.png")
    (folder / "character.json").write_text(json.dumps({"name": "Me", "mouth": [0.4, 0.6, 0.2, 0.1], "eyes": [], "pivot": [0.5, 1],
                                                       "voice": [str(tmp_path / "voice.wav"), 0, 2.5]}))
    overlay = {"id": "a", "asset": str(folder / "cutout.png"), "x": 0, "y": 0, "width": 30, "at": 4, "duration": 3, "action": "talk", "voice_mode": "mine"}
    items = show_scenes.overlay_items({"scenes": [{"id": "s", "start": 0, "end": 10, "decision": "accepted", "overlays": [overlay]}]})
    assert len(items) == 1 and "insert" not in items[0]                  # no pause: the host goes on talking
    assert items[0]["mouth"] == [0.4, 0.6, 0.2, 0.1] and "voice" not in items[0]   # its jaw follows the video's sound
