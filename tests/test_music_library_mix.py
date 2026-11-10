import io,json
from pathlib import Path
from types import SimpleNamespace
import pytest
from core import music_library,music_mix_plan,ai_connections
from core.project import create_project


@pytest.fixture
def library(tmp_path,monkeypatch):
    folder=tmp_path/'library';folder.mkdir();monkeypatch.setattr(music_library,'root',lambda:folder)
    monkeypatch.setattr(ai_connections,'api_key',lambda provider:'test-key')
    return folder


def test_jamendo_honors_download_permission_and_excludes_nc_nd(library,monkeypatch):
    calls=[]
    def fetch(url,headers=None):
        calls.append(url)
        return {'headers':{'status':'success'},'results':[
          dict(id=1,name='Song',artist_name='A',duration=10,license_ccurl='https://creativecommons.org/licenses/by/4.0/',audio='https://prod-1.storage.jamendo.com/preview',audiodownload='https://prod-1.storage.jamendo.com/full',audiodownload_allowed=False),
          dict(id=2,name='No',duration=10,license_ccurl='https://creativecommons.org/licenses/by-nc/4.0/',audiodownload_allowed=True)]}
    monkeypatch.setattr(music_library,'_json',fetch)
    found=music_library.search('jamendo','funny piano');assert len(found)==1
    assert found[0]['download']=='' and 'creativecommons.org' in found[0]['attribution']
    assert 'ccnc=false' in calls[0] and 'search=funny+piano' in calls[0]


def test_freesound_uses_header_and_labels_preview_download(library,monkeypatch):
    def fetch(url,headers=None):
        assert 'token=' not in url and headers=={'Authorization':'Token test-key'}
        assert 'Creative+Commons+0' in url
        return {'results':[dict(id=3,name='Bell',username='B',license='https://creativecommons.org/publicdomain/zero/1.0/',duration=3,previews={'preview-hq-mp3':'https://cdn.freesound.org/previews/bell.mp3'},url='https://freesound.org/s/3/')]}
    monkeypatch.setattr(music_library,'_json',fetch)
    r=music_library.search('freesound','bell')[0]
    assert r['preview_only'] and r['preview']==r['download']


def test_import_retains_license_and_project_snapshot_survives_library_removal(library,tmp_path,monkeypatch):
    monkeypatch.setattr('core.ffmpeg.ffprobe',lambda path:{'streams':[{'codec_type':'audio'}],'format':{'duration':'10'}})
    r=music_library.import_audio(io.BytesIO(b'fake audio'),'bell.mp3',{'license':'https://creativecommons.org/licenses/by/4.0/','attribution':'Bell by B'})
    p=create_project('Mix',str(tmp_path/'mix.zuckervid'))
    copies=music_library.project_tracks(p,[r['id']]);Path(r['path']).unlink()
    assert Path(copies[r['id']]['path']).read_bytes()==b'fake audio'
    assert json.loads(Path(copies[r['id']]['path']+'.json').read_text())['attribution']=='Bell by B'


def setup_plan(tmp_path,library):
    p=create_project('Backstage',str(tmp_path/'mix.zuckervid'))
    items=[dict(id='a',duration_sec=5,source_path='talk.mp4',clip_start_sec=0,text='Hello'),dict(id='b',duration_sec=2,source_path='talk.mp4',clip_start_sec=5,text='Bye')]
    (p.artifacts_dir/'backstage_edit.json').write_text(json.dumps({'segments':items}))
    audio=library/'bed.mp3';audio.write_bytes(b'fake audio')
    meta={'id':'bed','name':'Bed','duration':3,'license':'https://creativecommons.org/licenses/by/4.0/','attribution':'Bed by Artist'}
    Path(str(audio)+'.json').write_text(json.dumps(meta))
    rows=[dict(segment_id='a',mode='musica_bajo_narrador',track_id='bed',gain_db=-18,fade_in_s=.3,fade_out_s=.5),dict(segment_id='b',mode='solo_video',track_id=None,gain_db=-18,fade_in_s=.2,fade_out_s=.2)]
    return p,items,rows


def test_reviewed_plan_snapshots_tracks_keeps_approval_while_editing_and_rejects_stale_timing(library,tmp_path):
    p,items,rows=setup_plan(tmp_path,library)
    doc=music_mix_plan.save(p,rows,approve=True,revision=0)
    assert doc['status']=='approved' and 'Bed by Artist' in (p.folder/'credits.txt').read_text()
    music_mix_plan.save(p,rows,approve=False,revision=doc['revision'])
    assert music_mix_plan.for_export(p,items)['status']=='approved'
    with pytest.raises(ValueError,match='changed'):music_mix_plan.for_export(p,[{**items[0],'duration_sec':4},items[1]])
    with pytest.raises(ValueError):music_mix_plan.validate([{**rows[0],'track_id':'invented'},rows[1]],items,{'bed':{}})
    with pytest.raises(ValueError):music_mix_plan.save(p,rows,revision=0)


def test_plan_uses_shared_model_client_and_validates_its_catalog_ids(library,tmp_path,monkeypatch):
    p,items,rows=setup_plan(tmp_path,library)
    import core.narrative_scoring as scoring
    monkeypatch.setattr(scoring,'_provider_config',lambda:{'provider':'openai','key':'fake','base_url':'https://api.openai.com/v1','model':'test-model'})
    calls=[]
    def completion(client,config,**kwargs):
        calls.append(json.loads(kwargs['messages'][1]['content']))
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({'segments':rows})))])
    monkeypatch.setattr(scoring,'_completion',completion)
    assert music_mix_plan.propose(p)==rows
    assert calls[0]['library'][0]['id']=='bed'
    assert calls[0]['segments'][0]['text']=='Hello'


def test_single_pass_graph_contains_ducking_crossfade_and_correct_extra_input_indices(library,tmp_path):
    from core.stages.backstage import _backstage_single_pass_filtergraph
    p,items,rows=setup_plan(tmp_path,library);doc=music_mix_plan.save(p,rows,approve=True)
    inputs,graph,_,duration,_=_backstage_single_pass_filtergraph(items,mix_plan=doc,logo_path='logo.png',parchment_cards=[(Path('card.png'),0,2)])
    assert 'sidechaincompress' in graph and 'acrossfade=d=1.000000' in graph
    assert '[2:a]' in graph and '[3:v]' in graph and '[4:v]' in graph and '[5:v]' in graph
    assert 'volume=-18.000dB' in graph and duration==7
    assert inputs.count('-i')==6


def test_project_music_still_appears_in_plan_after_global_cleanup(library,tmp_path):
    p,items,rows=setup_plan(tmp_path,library)
    doc=music_mix_plan.save(p,rows,approve=True)
    (library/'bed.mp3').unlink()
    assert 'bed' in music_mix_plan.available_tracks(p)
    revised=music_mix_plan.save(p,rows,approve=True,revision=doc['revision'])
    assert Path(revised['tracks']['bed']['path']).is_file()


def test_wav_import_is_stored_only_as_mp3_and_keeps_attribution(library,tmp_path):
    import shutil,subprocess
    ffmpeg=shutil.which('ffmpeg')
    assert ffmpeg,'FFmpeg is required for audio import'
    source=tmp_path/'voice.wav'
    subprocess.run([ffmpeg,'-v','error','-f','lavfi','-i','sine=frequency=330:duration=0.25','-y',str(source)],check=True)
    with source.open('rb') as audio:r=music_library.import_audio(audio,'voice.wav',{'attribution':'Me'})
    assert Path(r['path']).suffix=='.mp3' and Path(r['path']).is_file()
    assert not list(library.glob('*.wav')) and not list(library.glob('*.normalized.mp3'))
    assert r['attribution']=='Me'


def test_music_library_track_can_be_placed_as_a_show_audio_bar(library,tmp_path):
    from core import show_scenes,show_proposals
    from server.api import create_app
    p,_,_=setup_plan(tmp_path,library)
    source=p.folder/'source.mp4';source.write_bytes(b'placeholder')
    (p.artifacts_dir/'show_plan.json').write_text(json.dumps({'source':str(source),'fps':25,'edited_duration':5,'shots':[],'words':[]}))
    show_proposals.save(p.artifacts_dir/'show_proposals.json',[],source=str(source),resources=[])
    show_scenes.save(p.artifacts_dir/'show_scenes.json',{'identity':{},'scenes':[{'id':'s','start':0,'end':5,'effects':[],'overlays':[],'decision':'pending','text':'hello'}]})
    client=create_app(str(p.folder)).test_client()
    result=client.post('/api/v1/music/place/bed',json={'project_id':str(p.folder),'scene_id':'s','at':1,'duration':2,'text':'Hello'})
    assert result.status_code==201,result.get_json()
    item=result.get_json()['proposal'];assert item['kind']=='jingle' and item['duration']==2
    assert Path(item['params']['path']).is_relative_to(p.folder/'assets/music')
    assert 'Bed by Artist' in (p.folder/'credits.txt').read_text()
    assert item['id'] in show_scenes.load(p.artifacts_dir/'show_scenes.json')['scenes'][0]['effects']

    trimmed=client.patch('/api/v1/show/review/'+item['id'],json={'at':1.5,'duration':1.5,'start':.5})
    assert trimmed.status_code==200,trimmed.get_json()
    assert trimmed.get_json()['params']['start']==.5
