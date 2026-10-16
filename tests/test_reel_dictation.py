import io
import json
import subprocess
import time
from pathlib import Path

from core.project import create_project
from core.fillers import filler_paths


def test_reel_includes_legacy_filler_sources(tmp_path):
    project = create_project('Reel', str(tmp_path / 'Reel.zuckervid'))
    project.data['settings']['wizard'] = {'platform': 'reel'}
    project.data['settings'].setdefault('edit', {})['fillers'] = [str(tmp_path / 'other-session.mp4')]
    project.data['settings']['wizard']['platform'] = 'youtube'
    assert filler_paths(project) == {str(tmp_path / 'other-session.mp4')}
    project.data['settings']['wizard']['platform'] = 'reel'
    assert filler_paths(project) == set()


def test_dictation_transcribes_uploaded_audio_and_removes_recording(tmp_path, monkeypatch):
    import server.api as api
    project = create_project('Reel', str(tmp_path / 'Reel.zuckervid'))
    project.data['settings']['wizard'] = {'platform': 'reel'}
    project.save()
    monkeypatch.setattr(api, 'ffprobe', lambda path: {'format': {'duration': '3'}, 'streams': [{'codec_type': 'audio'}]})
    observed = []
    def transcribe(sources, *args, **kwargs):
        observed.append(Path(sources[0]['path']))
        assert observed[0].read_bytes() == b'recorded voice'
        return {'status': 'ready', 'sources': [{'segments': [{'text': 'First phrase'}, {'text': 'Second phrase'}]}]}
    monkeypatch.setattr(api, 'transcribe_sources', transcribe)
    client = api.create_app(project_path=str(project.folder)).test_client()
    response = client.post('/api/v1/captions/dictation', data={'audio': (io.BytesIO(b'recorded voice'), 'voice.m4a')})
    assert response.status_code == 202
    for _ in range(100):
        status = client.get('/api/v1/captions/auto-read/status').get_json()
        if status['status'] != 'running' and observed and not observed[0].exists():
            break
        time.sleep(.01)
    assert status['status'] == 'done'
    assert status['result']['text'] == 'First phrase\n\nSecond phrase'
    assert status['result']['provenance'] == 'microphone_dictation'
    assert not observed[0].exists()


def test_arranged_captions_keep_order_and_gaps():
    source = (Path(__file__).parents[1] / 'web/app.js').read_text()
    function = source[source.index('function arrangeCaptionPhrases('):source.index('let captionRecorder =')]
    script = function + '''
const assert = require('assert');
for (const random of [()=>0,()=>.5,()=>.999]) {
 const cues=arrangeCaptionPhrases(['one','two','three'],30,random);
 assert.deepEqual(cues.map(c=>c.lines[0]),['one','two','three']);
 assert(cues[0].start>0 && cues[2].end<30);
 assert(cues[0].end<cues[1].start && cues[1].end<cues[2].start);
 assert(cues.every(c=>c.style_override.color && c.style_override.fun_effect && c.style_override.size));
}
assert.throws(()=>arrangeCaptionPhrases(['a','b'],1));
assert.throws(()=>arrangeCaptionPhrases([],30));
'''
    subprocess.run(['node', '-e', script], check=True)


def test_dictation_rejects_missing_audio_and_other_modes(tmp_path):
    from server.api import create_app
    project = create_project('Reel', str(tmp_path / 'Reel.zuckervid'))
    project.data['settings']['wizard'] = {'platform': 'reel'}
    project.save()
    client = create_app(project_path=str(project.folder)).test_client()
    assert client.post('/api/v1/captions/dictation').status_code == 400
    project.data['settings']['wizard']['platform'] = 'youtube'
    project.save()
    client = create_app(project_path=str(project.folder)).test_client()
    assert client.post('/api/v1/captions/dictation').status_code == 409


def _arrange(script):
    source = (Path(__file__).parents[1] / 'web/app.js').read_text()
    block = source[source.index('// "Distribute text as captions"'):source.index('let captionRecorder =')]
    return subprocess.run(['node', '-e', block + script], check=True, capture_output=True, text=True).stdout


def test_distributed_phrases_each_get_a_different_wild_look():
    _arrange("""
const assert = require('assert');
let seed = 7; const random = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
const phrases = Array.from({length: 12}, (_, i) => `phrase number ${i}`);
for (let run = 0; run < 20; run++) {
  const cues = arrangeCaptionPhrases(phrases, 60, random);
  assert.equal(cues.length, 12);
  for (let i = 1; i < cues.length; i++) {
    const [a, b] = [cues[i - 1].style_override, cues[i].style_override];
    assert.notEqual(a.color, b.color);
    assert.notEqual(a.fun_effect, b.fun_effect);
    assert(cues[i - 1].end < cues[i].start);
  }
  for (const cue of cues) {
    const look = cue.style_override;
    assert(look.vertical >= 10 && look.vertical <= 90, look.vertical);
    assert(look.horizontal >= 5 && look.horizontal <= 95);
    assert('outline_width' in look && 'shadow_distance' in look && 'shadow_opacity' in look);
  }
}
""")


def test_long_phrase_is_chained_into_overlapping_captions_one_under_the_other():
    out = _arrange("""
const assert = require('assert');
let seed = 3; const random = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
const long = 'Without music, life would be a mistake, and every silence would weigh more';
for (let run = 0; run < 30; run++) {
  const cues = arrangeCaptionPhrases([long, 'short one', 'Typed on\\ntwo lines'], 30, random);
  const chain = cues.slice(0, 2);
  assert.equal(cues.length, 5);
  assert.equal(chain.map(c => c.lines[0]).join(' '), long);
  assert.equal(chain[0].lines[0], 'Without music, life would be a mistake,');
  // The second part appears while the first is still on screen, below it.
  assert(chain[1].start > chain[0].start && chain[1].start < chain[0].end && chain[1].end > chain[0].end);
  assert(chain[1].style_override.vertical > chain[0].style_override.vertical);
  assert.equal(chain[1].style_override.outline_width, chain[0].style_override.outline_width);
  // Lines typed by the user are the parts of their own chain.
  assert.deepEqual(cues.slice(3).map(c => c.lines[0]), ['Typed on', 'two lines']);
  assert(cues[2].end < cues[3].start && chain[1].end < cues[2].start);
  // The phrase after a chain never repeats the chain's last effect.
  assert.notEqual(cues[2].style_override.fun_effect, chain[1].style_override.fun_effect);
}
console.log(JSON.stringify(arrangeParts('one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen')));
""")
    assert len(json.loads(out)) == 3
