"""Measure native pipeline waits and isolated FFmpeg stage replays; no cache mutation."""
import argparse,copy,json,subprocess,sys,time,types
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from core.project import load_project
from core.stages import export as export_current
from core import spherical_motion
from core.ffmpeg import tool_status
p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--plan',required=True);p.add_argument('--root',required=True);args=p.parse_args()
root=Path(args.root);root.mkdir(parents=True,exist_ok=True)
if subprocess.run(['pgrep','-f','^/Applications/Zucker Editor.app/Contents/MacOS/Zucker Editor'],capture_output=True).returncode==0:raise SystemExit('Close application before measuring')
export=types.ModuleType('core.stages.export_cost_reference');export.__package__='core.stages';export.__file__=export_current.__file__
exec(compile(subprocess.check_output(['git','show','8653b4e:core/stages/export.py'],text=True),'export_cost_reference','exec'),export.__dict__)
project=load_project(args.source);plan=json.loads(Path(args.plan).read_text());segments=export._frame_normalized_segments(export._apply_saved_spherical_landmarks(project,plan['segments']))
# Instrument a private copy of the unmodified native pipeline, keeping production untouched.
code=subprocess.check_output(['git','show','8653b4e:core/spherical_motion.py'],text=True)
for old,new in [
 ('decoder = subprocess.Popen(decoder_command, stdout=subprocess.PIPE, stderr=decode_log)', 'PROFILE["native_started"] = time.perf_counter()\n        decoder = subprocess.Popen(decoder_command, stdout=subprocess.PIPE, stderr=decode_log)'),
 ('count = decoder.stdout.readinto(frame_bytes[received:])','_t = time.perf_counter()\n                    count = decoder.stdout.readinto(frame_bytes[received:])\n                    PROFILE["decode_read_wait_sec"] += time.perf_counter() - _t'),
 ('pose = pose_sampler(index / 30)', 'if index == 0: PROFILE["native_first_frame_sec"] = time.perf_counter() - PROFILE["native_started"]\n                _t = time.perf_counter()\n                pose = pose_sampler(index / 30)'),
 ('encoder.stdin.write(memoryview(pixels).cast("B"))','PROFILE["reprojection_sec"] += time.perf_counter() - _t\n                _t = time.perf_counter()\n                encoder.stdin.write(memoryview(pixels).cast("B"))\n                PROFILE["encoder_write_wait_sec"] += time.perf_counter() - _t'),
 ('encoder_code = encoder.wait(timeout=120)','_t = time.perf_counter()\n            encoder_code = encoder.wait(timeout=120)\n            PROFILE["encoder_finalize_sec"] += time.perf_counter() - _t')]:
 assert old in code,old;code=code.replace(old,new)
native=types.ModuleType('native_profile');native.__file__=spherical_motion.__file__;exec(compile(code,'native_profile','exec'),native.__dict__)
export.run_reprojected_command=native.run_reprojected_command
ff=tool_status()['ffmpeg_path'];bitrate=json.loads((project.artifacts_dir/'export_manifest.json').read_text())['target_video_bitrate']
selected=[]
for camera in dict.fromkeys(s['camera_id'] for s in segments):
 group=[(i,s) for i,s in enumerate(segments) if s['camera_id']==camera]
 if not group:continue
 for j in range(8):selected.append(group[round(j*(len(group)-1)/7)])
assert len(selected)>=24,[(s['camera_id']) for _,s in selected]
def run(command):
 start=time.perf_counter();subprocess.run(command,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,check=True,timeout=180);return time.perf_counter()-start
results=[]
for number,(idx,original) in enumerate(selected):
 for control in [False,True]:
  # Paired controls add the missing moving Sony/static phone/locked 360 cases.
  s=copy.deepcopy(original)
  if control:
   if s.get('spherical_shot'):
    sh=s['spherical_shot'];sh.update(movement='hold',hold_motion='none',hold_motion_rate_deg_per_sec=0,drift_yaw_fraction=0,drift_pitch_fraction=0,fov_delta_fraction=0,sweep_enabled=False,intershot_sweep=False)
   elif s.get('motion'):s.pop('motion')
   else:s['motion']=copy.deepcopy(next(x['motion'] for x in segments if x.get('motion')))
  metrics={'decode_read_wait_sec':0.,'reprojection_sec':0.,'encoder_write_wait_sec':0.,'encoder_finalize_sec':0.};native.PROFILE=metrics
  output=root/f'{number:02d}-{int(control)}.mp4';commands=[];start=time.perf_counter()
  export._render_segment(project,s,project.data['inputs']['master']['path'],output,'youtube',bitrate,color_profile={},overlay_config={},warnings=[],command_recorder=commands)
  render=time.perf_counter()-start;start=time.perf_counter();export._verify_segment_frame_duration(output,export._segment_frame_count(s),'profile');export._verify_moving_segment(output,s['duration_sec'],'profile','profiling');verify=time.perf_counter()-start
  duration=s['duration_sec'];base=[ff,'-v','error','-nostdin','-ss',str(s['clip_start_sec']),'-i',s['source_path'],'-an']
  # Upper bound for launch/open/seek: also includes decoding the first source frame.
  first=run(base+['-frames:v','1','-f','null','-']);decode=run(base+['-t',str(duration),'-f','null','-'])
  filter_time=None
  if not s.get('spherical_shot'):
   command=commands[-1];end=command.index('-pix_fmt');diagnostic=command[:end]+['-an','-frames:v',str(export._segment_frame_count(s)),'-f','null','-'];filter_time=run(diagnostic)
  # Encoder replay on decoded, filtered pixels provides a standalone estimate,
  # rather than falsely subtracting overlapped stage wall times.
  command=commands[-1];captured_metrics=dict(metrics)
  raw=root/f'{number:02d}-{int(control)}.nut';end=command.index('-pix_fmt',command.index('-filter_complex'))
  raw_command=command[:end]+['-pix_fmt','yuv420p','-an','-frames:v',str(export._segment_frame_count(s)),'-c:v','rawvideo','-f','nut','-y',str(raw)]
  try:
   if s.get('spherical_shot'):
    native.PROFILE={'decode_read_wait_sec':0.,'reprojection_sec':0.,'encoder_write_wait_sec':0.,'encoder_finalize_sec':0.}
    shot=s['spherical_shot'];source=export._segment_source_info(project,s);probe=source['probe']
    sampler=(lambda seconds:export._v360_motion_at(shot,duration,seconds)) if (shot.get('type') in {'recorded_move','planet'} and shot.get('movement')!='planet_to_stage') or not shot.get('movement') else None
    native.run_reprojected_command(raw_command,s['source_path'],(probe['width'],probe['height']),s['clip_start_sec'],export._segment_frame_count(s),shot,pose_sampler=sampler)
   else:run(raw_command)
   encode_replay=run([ff,'-v','error','-y','-i',str(raw),'-an',*export._video_encode_args('h264_videotoolbox',bitrate),'-f','null','-'])
  finally:raw.unlink(missing_ok=True)
  row={'plan_index':idx+1,'paired_control':control,'source':Path(s['source_path']).name,'camera':s['camera_id'],'movement':s.get('spherical_shot',{}).get('movement') or s.get('motion',{}).get('movement') or 'none','duration_sec':duration,'render_wall_sec':render,'verification_sec':verify,'first_decoded_frame_upper_bound_sec':first,'decode_only_replay_sec':decode,'decode_plus_filters_replay_sec':filter_time,'encode_filtered_pixels_replay_sec':encode_replay,'native_timings':captured_metrics,'commands':commands}
  results.append(row);(root/'results.json').write_text(json.dumps(results,indent=2));print(number,control,row['camera'],row['movement'],round(render,3),round(first/render*100,2),flush=True)
print('COMPLETE',len(results),flush=True)
