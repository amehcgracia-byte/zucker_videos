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
 assert(cues.every(c=>c.style_override.glow_intensity>0));
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
