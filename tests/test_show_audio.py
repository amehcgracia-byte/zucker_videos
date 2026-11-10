"""Real samples prove censorship, gentle cleanup and mixed clip audio reach export."""
import io
import json
import shutil
import subprocess
import time
import wave
from pathlib import Path
import numpy as np
import pytest
from core import show_audio,show_proposals,show_scenes,show_backgrounds,show_music
from core.project import create_project
from server.api import create_app

FFMPEG=shutil.which('ffmpeg')


def test_only_the_curse_is_replaced_and_speech_around_it_is_untouched():
    words=[('what',0,.2),('the',.2,.35),('fuck',.35,.65),('Scunthorpe',.65,.9),('fucking',1,1.2),('coño',1.4,1.6)]
    spans=show_audio.censor_spans(words)
    assert [s['word'] for s in spans]==['fuck','fucking','coño']
    assert show_audio.censor_spans(words,['fuck','fucking','coño'])==[]
    rate=show_audio.RATE;t=np.arange(2*rate)/rate;x=np.repeat((.12*np.sin(2*np.pi*1000*t))[:,None],2,axis=1).astype(np.float32)
    plan={'audio_settings':dict(censor=True,narration_volume=1),'audio_censor_spans':spans}
    out=show_audio.process(x,plan)
    assert np.array_equal(out[:int(.3*rate)],x[:int(.3*rate)])
    assert np.array_equal(out[int(.72*rate):int(.9*rate)],x[int(.72*rate):int(.9*rate)])
    part=out[int(.4*rate):int(.6*rate),0];spectrum=np.abs(np.fft.rfft(part));assert spectrum[int(1000*len(part)/rate)]<.01*np.abs(np.fft.rfft(x[int(.4*rate):int(.6*rate),0])).max()
    assert len(out)==len(x)
    assert np.max(np.abs(show_audio.process(x,{**plan,'audio_settings':dict(censor=True,narration_volume=0)})))==0
    assert np.array_equal(show_audio.process(x,{**plan,'audio_settings':dict(censor=False,narration_volume=1)}),x)
    local=show_audio.process(x[:rate],{**plan,'_edited_offset':1})
    assert not np.array_equal(local[:int(.25*rate)],x[:int(.25*rate)])
    with wave.open(io.BytesIO(show_audio.audition())) as audio:
        assert audio.getframerate()==rate and audio.getnframes()==round(.24*rate)


@pytest.fixture
def audio_project(tmp_path,monkeypatch):
    monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'data'))
    project=create_project('Audio workshop',str(tmp_path/'show.zuckervid'))
    source=project.folder/'talk.mp4'
    subprocess.run([FFMPEG,'-v','error','-y','-f','lavfi','-i','color=c=blue:s=160x90:r=30:d=2','-f','lavfi','-i','sine=frequency=300:duration=2','-c:v','libx264','-threads','1','-c:a','aac','-shortest',str(source)],check=True)
    item=show_proposals.proposal('clip',.5,1,reason='Test clip',clip=str(source),clip_length=2,clip_start=0,clip_audio=True,matte='none');item['status']='approved'
    plan={'source':str(source),'fps':30,'edited_duration':2,'cuts':0,'words':[{'text':'fuck','start':.4,'end':.7}], 'shots':[{'start_frame':0,'end_frame':60,'words':[0]}]}
    (project.artifacts_dir/'show_plan.json').write_text(json.dumps(plan))
    show_proposals.save(project.artifacts_dir/'show_proposals.json',[item],source=str(source),resources=[])
    show_scenes.save(project.artifacts_dir/'show_scenes.json',dict(identity={},scenes=[dict(id='s1',name='Test',start=0,end=2,effects=[item['id']],overlays=[],decision='modified',text='fuck')]))
    return project,item,create_app(project_path=str(project.folder),dev=True).test_client()


def test_audio_settings_track_modes_and_balance_job_persist(audio_project):
    project,item,client=audio_project
    data=client.get('/api/v1/show/audio').get_json()
    assert data['settings']['censor'] and len(data['words'])==1 and len(data['tracks'])==1
    assert client.patch('/api/v1/show/audio',json={'project_id':'another project','censor':False}).status_code==409
    assert client.post('/api/v1/show/audio/job',json={'project_id':'another project','mode':'balance'}).status_code==409
    assert client.patch('/api/v1/show/audio',json={'narration_volume':-1}).status_code==400
    assert client.patch('/api/v1/show/audio',json={'censor':False,'narration_volume':.4}).status_code==200
    response=client.patch('/api/v1/show/audio/tracks/'+item['id'],json={'volume':0,'mode':'mix','under_voice':True})
    assert response.status_code==200,response.get_json()
    cut,layer,_=show_proposals.render_inputs([response.get_json()])
    assert len(cut)==1 and layer[0]['volume']==0 and layer[0]['under_voice'] and not any(p.get('insert') for p in layer)
    assert client.get('/api/v1/show/audio/censor-sound').mimetype=='audio/wav'
    assert client.post('/api/v1/show/audio/job',json={'mode':'balance'}).status_code==202
    for _ in range(100):
        job=client.get('/api/v1/show/audio/job').get_json()
        if job['status']!='working':break
        time.sleep(.05)
    assert job['status']=='done',job
    assert job['result']['balanced']==1
    saved=show_proposals.load(project.artifacts_dir/'show_proposals.json')['proposals'][0]
    assert saved['params']['audio_mode']=='mix' and saved['params']['under_voice']


def test_noise_cleanup_is_cached_and_preserves_sample_duration(tmp_path,monkeypatch):
    monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'data'))
    project=create_project('Noise',str(tmp_path/'noise.zuckervid'));source=project.folder/'noise.wav'
    rate=48000;t=np.arange(3*rate)/rate;rng=np.random.default_rng(123)
    x=(.012*rng.normal(size=len(t))+.18*np.sin(2*np.pi*500*t)*(t>1)).astype(np.float32)
    with wave.open(str(source),'wb') as output:
        output.setnchannels(1);output.setsampwidth(2);output.setframerate(rate);output.writeframes((x*32767).astype('<i2').tobytes())
    result=show_audio.prepare_noise(project,source,ffmpeg=FFMPEG)
    assert result['status']=='reduced',result
    raw=subprocess.run([FFMPEG,'-v','error','-i',result['path'],'-ac','1','-ar',str(rate),'-f','f32le','-'],capture_output=True,check=True).stdout
    y=np.frombuffer(raw,dtype='<f4');assert len(y)==len(x)
    assert np.std(y[rate//4:rate//2])<np.std(x[rate//4:rate//2])*.85
    tone=lambda a:abs(np.dot(a[round(1.5*rate):round(2.5*rate)],np.exp(-2j*np.pi*500*np.arange(rate)/rate)))
    assert tone(y)>tone(x)*.9, 'Noise filtering must preserve the speech-band tone' 
    # Original bytes are unchanged; a second pass must never run cleanup again.
    monkeypatch.setattr(show_audio,'_run',lambda *a,**k:pytest.fail('cleaned twice'))
    assert show_audio.prepare_noise(project,source,ffmpeg=FFMPEG)['path']==result['path']


def test_person_masks_are_shared_across_spans_backgrounds_and_render_sizes(tmp_path,monkeypatch):
    source=tmp_path/'clip.mp4'
    subprocess.run([FFMPEG,'-v','error','-y','-f','lavfi','-i','testsrc=s=160x90:r=30:d=3','-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(source)],check=True)
    calls=[]
    class Segmenter:
        def __call__(self,rgb):
            calls.append(1);out=np.zeros(show_backgrounds.MASK_SIZE[::-1],np.uint8);out[:,100:200]=255;return out
    monkeypatch.setattr(show_backgrounds,'Segmenter',Segmenter)
    cache=tmp_path/'project/cache/show_masks'
    first=show_backgrounds.source_masks(source,.5,1,30,cache,ffmpeg=FFMPEG);count=len(calls)
    assert count>0 and np.load(first).shape[0]==30
    second=show_backgrounds.source_masks(source,.8,.5,30,cache,ffmpeg=FFMPEG)
    assert len(calls)==count and np.array_equal(np.load(second),np.load(first)[9:24])
    raw={'video_layer':str(source),'matte':'person','clip_start':.8,'at':20,'duration':.5,'picture':'different-background'}
    show_backgrounds.prepare(source,[raw],{'fps':30},cache,ffmpeg=FFMPEG)
    assert len(calls)==count and raw['masks']==str(second)
    assert list((tmp_path/'project/assets/video-cutouts').rglob('*.npz'))
    slower=show_backgrounds.source_masks(source,.5,1,15,cache,ffmpeg=FFMPEG)
    assert len(calls)==count and np.array_equal(np.load(slower),np.load(first)[::2])


def test_censorship_is_in_the_rendered_audio_and_keeps_video_duration(audio_project):
    from core.show_render import render_show
    project,item,client=audio_project
    plan=json.loads((project.artifacts_dir/'show_plan.json').read_text());plan=show_audio.attach(project,plan)
    output=project.folder/'censored.mp4'
    render_show(plan['source'],plan,output,ffmpeg=FFMPEG,width=160,height=90,preset='ultrafast')
    raw=subprocess.run([FFMPEG,'-v','error','-i',str(output),'-ac','1','-ar','48000','-f','f32le','-'],capture_output=True,check=True).stdout
    samples=np.frombuffer(raw,dtype='<f4')
    def frequency(a,b,hz):
        part=samples[round(a*48000):round(b*48000)];spec=np.abs(np.fft.rfft(part));k=round(hz*len(part)/48000)
        return spec[max(0,k-2):k+3].max()/len(part)
    assert frequency(.45,.6,300)<frequency(.1,.3,300)*.05
    assert frequency(.1,.3,300)>.02
    probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-of','json',str(output)]))
    assert float(probe['format']['duration'])==pytest.approx(2,abs=.05)


def test_mixed_audio_ducks_the_clip_not_the_narration_and_zero_really_mutes(audio_project):
    project,item,client=audio_project;source=Path(item['params']['clip']);jingle=project.folder/'music.wav'
    subprocess.run([FFMPEG,'-v','error','-y','-f','lavfi','-i','sine=frequency=1500:duration=2',str(jingle)],check=True)
    base=dict(jingle=str(jingle),at=0,duration=2,start=0,volume=1,duck=1,fade=.01)
    outputs=[]
    for under,volume in [(False,1),(True,1),(False,0)]:
        output=project.folder/f'mix-{under}-{volume}.mp4';show_music.mix(source,[{**base,'under_voice':under,'volume':volume}],output,ffmpeg=FFMPEG)
        raw=subprocess.run([FFMPEG,'-v','error','-i',str(output),'-ac','1','-ar','8000','-f','f32le','-'],capture_output=True,check=True).stdout
        samples=np.frombuffer(raw,dtype='<f4')[4000:12000];spec=np.abs(np.fft.rfft(samples));outputs.append([spec[hz-3:hz+4].max() for hz in (300,1500)])
    assert outputs[1][0]==pytest.approx(outputs[0][0],rel=.07)
    assert outputs[1][1]<outputs[0][1]*.7
    assert outputs[2][1]<outputs[0][1]*.01


def test_balance_does_not_overwrite_a_track_edited_while_it_measures(audio_project,monkeypatch):
    project,item,_=audio_project;calls=[];path=project.artifacts_dir/'show_proposals.json'
    def measure(*args):
        calls.append(1)
        if len(calls)==2:
            stored=show_proposals.load(path);stored['proposals'][0]['params']['audio_volume']=.33
            show_proposals.save(path,stored['proposals'],**{k:v for k,v in stored.items() if k not in ('proposals','version')})
            show_audio.save(project,{'narration_volume':.25})
        return .1,.2
    monkeypatch.setattr(show_audio,'measure',measure)
    result=show_audio.balance(project,FFMPEG)
    assert result['balanced']==0 and len(result['unavailable'])==2
    assert show_audio.load(project)['narration_volume']==.25
    assert show_proposals.load(path)['proposals'][0]['params']['audio_volume']==.33


def test_recorded_library_character_is_in_mixer_and_mix_does_not_pause_narration(tmp_path,monkeypatch):
    monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'data'))
    from PIL import Image
    p=create_project('Voice',str(tmp_path/'voice.zuckervid'))
    asset=p.folder/'assets/show-library/character/cutout.png';asset.parent.mkdir(parents=True);Image.new('RGBA',(20,20),'red').save(asset)
    voice=p.folder/'voice.wav';voice.write_bytes(b'placeholder')
    (asset.parent/'character.json').write_text(json.dumps({'voice':[str(voice),0,1]}))
    scene={'id':'scene-01','start':0,'end':3,'decision':'modified','effects':[],
           'overlays':[{'id':'speaker','asset':str(asset),'at':.5,'duration':1,'action':'talk','x':60,'y':30,'width':10}]}
    show_scenes.save(p.artifacts_dir/'show_scenes.json',{'scenes':[scene]})
    show_proposals.save(p.artifacts_dir/'show_proposals.json',[])
    (p.artifacts_dir/'show_plan.json').write_text(json.dumps({'source':str(voice),'shots':[],'words':[]}))
    client=create_app(str(p.folder)).test_client()
    tracks=client.get('/api/v1/show/audio').get_json()['tracks'];assert tracks[0]['id']=='sticker:speaker'
    assert tracks[0]['mode']=='own'
    response=client.patch('/api/v1/show/audio/tracks/sticker:speaker',json={'volume':.25,'mode':'mix','under_voice':True})
    assert response.status_code==200,response.get_json()
    doc=show_scenes.load(p.artifacts_dir/'show_scenes.json');items=show_scenes.overlay_items(doc)
    assert not any(item.get('insert') for item in items)
    assert next(item for item in items if item.get('jingle'))['volume']==.25
    assert next(item for item in items if item.get('image'))['voice']==[str(voice),0,1]
    # Re-saving the scene must retain its mixer controls.
    response=client.post('/api/v1/show/scenes/scene-01/overlays',json={'overlays':doc['scenes'][0]['overlays']})
    assert response.status_code==200,response.get_json()
    assert response.get_json()['overlays'][0]['volume']==.25
    assert response.get_json()['overlays'][0]['audio_mode']=='mix'
    assert client.post('/api/v1/show/scenes/resume',json={'scene_id':'missing'}).status_code==404
    assert client.post('/api/v1/show/scenes/resume',json={'scene_id':'scene-01'}).status_code==200
    assert json.loads((p.artifacts_dir/'show_resume.json').read_text())['scene_id']=='scene-01'


def test_recovered_project_reopens_review_without_rewriting_completed_stage_history(audio_project):
    from server.api import _project_wizard_status
    p,_,_=audio_project
    before=json.loads(json.dumps(p.data))
    (p.artifacts_dir/'show_resume.json').write_text(json.dumps({'scene_id':'s1','reopen_review':True}))
    assert _project_wizard_status(p)['status']=='waiting_show_review'
    assert p.data==before


def test_balancing_keeps_every_choice_and_only_levels_and_tames_peaks(audio_project, monkeypatch):
    project, item, client = audio_project
    stored = show_proposals.load(project.artifacts_dir / 'show_proposals.json')
    muted = show_proposals.proposal('clip', 1.2, .5, reason='Muted clip', clip=item['params']['clip'], clip_length=2, clip_start=0, clip_audio=False, matte='none')
    muted['status'] = 'approved'
    show_proposals.save(project.artifacts_dir / 'show_proposals.json', stored['proposals'] + [muted], source=stored['source'], resources=[])
    monkeypatch.setattr(show_audio, 'measure', lambda *a, **k: (.03, .9))
    result = show_audio.balance(project, FFMPEG)
    saved = {p['id']: p['params'] for p in show_proposals.load(project.artifacts_dir / 'show_proposals.json')['proposals']}
    own = saved[item['id']]
    assert own.get('audio_mode', 'own') == 'own' and own.get('clip_audio') is True and not own.get('under_voice')   # its own sound, as chosen
    assert own['audio_volume'] == 4.0 and own['tame_peaks']                    # quiet clip brought up to the narration, peaks tamed
    assert saved[muted['id']].get('clip_audio') is False and 'audio_volume' not in saved[muted['id']]   # muted: left alone
    assert result['balanced'] == 1


def test_a_sudden_loud_moment_is_brought_down_and_the_rest_is_untouched():
    from core.show_inserts import tame_peaks
    rate = 48000
    quiet = np.full((rate, 2), .1, np.float32)
    bang = quiet.copy()
    bang[rate // 2:rate // 2 + 2400] = .95
    tamed = tame_peaks(bang, rate=rate)
    assert np.abs(tamed).max() <= .5 + 1e-6
    assert np.allclose(tamed[:rate // 4], quiet[:rate // 4])
    assert np.allclose(tame_peaks(quiet, rate=rate), quiet)
