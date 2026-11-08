"""Actual server render with a missing optional source and an untouched master clock."""
import json
import subprocess
from pathlib import Path
from core.project import create_project,file_record,atomic_write_json
from core.stages.sync import SyncStage,SYNC_ALGORITHM_VERSION
from core.stages.export import ExportStage
from core.ffmpeg import ffprobe,tool_status
from core.media_validation import validate_camera_video_metadata
from server.inbox import save_global_config


def test_server_export_omits_missing_insert_and_unused_media(tmp_path,monkeypatch):
    monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'state'))
    monkeypatch.setenv('ZUCKER_GLOBAL_CACHE',str(tmp_path/'cache'))
    save_global_config({'branding':{'mode':'none'}})
    ff=tool_status()['ffmpeg_path']
    camera=tmp_path/'camera.mp4';master=tmp_path/'master.wav';missing=tmp_path/'logo.mp4'
    subprocess.run([ff,'-v','error','-y','-f','lavfi','-i','anoisesrc=color=pink:seed=7:amplitude=.2:duration=2','-ar','48000',str(master)],check=True)
    subprocess.run([ff,'-v','error','-y','-f','lavfi','-i','testsrc2=size=320x180:rate=30:duration=2','-i',str(master),'-c:v','libx264','-threads','1','-c:a','aac','-shortest',str(camera)],check=True)
    p=create_project('Optional server',str(tmp_path/'edit.zuckervid'))
    record=file_record(str(camera));record['probe']=validate_camera_video_metadata(ffprobe(str(camera))).summary
    p.data['inputs']['videos']=[record,{'path':str(missing)},{'path':str(tmp_path/'unused.mp4')}]
    p.data['inputs']['master']=file_record(str(master))
    p.data['settings']['wizard']={'platform':'youtube'}
    p.data['settings']['edit']['fillers']=[str(missing)]
    p.data['stages']['sync'].update(status='done',fingerprint=SyncStage().inputs_fingerprint(p))
    atomic_write_json(p.artifacts_dir/'sync_map.json',{'sync_algorithm_version':SYNC_ALGORITHM_VERSION,'clips':{'camera':{'path':str(camera),'source_path':str(camera),'offset_sec':0,'duration_sec':2,'confidence':12,'threshold':6}}})
    shot={'source_path':str(camera),'clip_path':str(camera),'clip_start_sec':0,'master_start_sec':0,'duration_sec':1}
    plan={'platform':'youtube','segments':[shot,{**shot,'source_path':str(missing),'clip_path':str(missing),'master_start_sec':1}]}
    atomic_write_json(p.artifacts_dir/'edit_plan.json',plan)
    before=(p.artifacts_dir/'edit_plan.json').read_bytes()
    p.save()
    ExportStage().run(p,lambda *args:None)
    manifest=json.loads((p.artifacts_dir/'export_manifest.json').read_text())
    assert manifest['optional_omissions'][0]['shot']==2
    assert any('optional insert logo.mp4 omitted' in row for row in manifest['warnings'])
    effective=json.loads(Path(manifest['effective_plan_path']).read_text())
    assert effective['segments'][1]['source_path']==str(camera)
    assert effective['segments'][1]['clip_start_sec']==1
    assert effective['segments'][1]['master_start_sec']==1
    assert (p.artifacts_dir/'edit_plan.json').read_bytes()==before
    output=Path(manifest['exports'][0]['path'])
    info=ffprobe(str(output))
    assert abs(float(info['format']['duration'])-2)<.1
    assert {s['codec_type'] for s in info['streams']}=={'audio','video'}
    subprocess.run([ff,'-v','error','-i',str(output),'-f','null','-'],check=True,capture_output=True)
