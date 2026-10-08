"""Isolated full-export A/B: cold rendered caches, warm reverse order.

Run from the repository with PYTHONPATH set. No project or user cache is cleared.
The reference is the export implementation at 7ae1d8d with native CPU decode.
"""
import argparse,copy,hashlib,json,os,resource,subprocess,threading,time,types,sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core.project import Project,load_project
from core.stages import export as current
from core.stages.base import stable_fingerprint

parser=argparse.ArgumentParser()
parser.add_argument('--source',required=True)
parser.add_argument('--root',required=True)
parser.add_argument('--variant',choices=['before','after'],required=True)
parser.add_argument('--round',choices=['cold','warm'],required=True)
args=parser.parse_args()
root=Path(args.root);root.mkdir(parents=True,exist_ok=True)
variant_root=root/args.variant;variant_root.mkdir(exist_ok=True)
source=load_project(args.source)
snapshot=root/'frozen-project.json'
if not snapshot.exists(): snapshot.write_text(json.dumps(source.data,sort_keys=True,indent=2))
planfile=root/'frozen-edit-plan.json'
if not planfile.exists(): planfile.write_bytes((source.artifacts_dir/'edit_plan.json').read_bytes())
plan=json.loads(planfile.read_text());data=json.loads(snapshot.read_text())
project=Project(variant_root/'case',copy.deepcopy(data));project.ensure_dirs();project.save()
(project.artifacts_dir/'edit_plan.json').write_bytes(planfile.read_bytes())
for name in ['coverage.json','beats.json']:
 target=project.artifacts_dir/name
 if not target.exists() and (source.artifacts_dir/name).exists():target.write_bytes((source.artifacts_dir/name).read_bytes())
cache=variant_root/'rendered-cache';cache.mkdir(exist_ok=True)
if args.round=='cold' and any(cache.iterdir()):raise SystemExit('Cold cache is not empty; refusing to erase it')
if args.round=='warm' and not any(cache.iterdir()):raise SystemExit('Warm cache has no rendered segments')
module=current
if args.variant=='before':
 text=subprocess.check_output(['git','show','7ae1d8d:core/stages/export.py'],text=True)
 module=types.ModuleType('core.stages.export_reference');module.__package__='core.stages'
 exec(compile(text,'export_reference','exec'),module.__dict__)
 native=module.run_reprojected_command
 module.run_reprojected_command=lambda *a,**kw:native(*a,**kw,hardware_decode=False)
module.global_segment_path=lambda key:cache/f'{key}.mp4'
segments=module._frame_normalized_segments(module._apply_saved_spherical_landmarks(project,plan['segments']))
bitrate=json.loads((source.artifacts_dir/'export_manifest.json').read_text())['target_video_bitrate']
master=data['inputs']['master']['path']
output=project.exports_dir/f'{args.variant}-{args.round}.mp4'
if output.exists():raise SystemExit('Output exists; refusing to overwrite previous measurement')
# Refuse competing user renders. The installed application is closed for these runs.
def user_app_open():return subprocess.run(['pgrep','-f','^/Applications/Zucker Editor.app/Contents/MacOS/Zucker Editor'],capture_output=True).returncode==0
if user_app_open():raise SystemExit('User application open; benchmark deferred')
all_sources={str(s.get('source_path') or s['clip_path']) for s in segments}|{master}
identity={p:(Path(p).stat().st_size,Path(p).stat().st_mtime_ns) for p in all_sources}
roundroot=variant_root/args.round;roundroot.mkdir(exist_ok=True)
(roundroot/'memory-before.txt').write_text(subprocess.check_output(['vm_stat'],text=True))
start=time.monotonic();last=[start];stopped=threading.Event();invalid=[];io_lock=threading.Lock();logical_bytes=[0];seen_outputs=set()
original_run=module._run_ffmpeg_progress
original_segment=module._render_segment

def record_output(path, *, copied=False):
 path=Path(path)
 if path.is_file() and path.suffix=='.mp4':
  with io_lock:
   if copied or str(path) not in seen_outputs:
    logical_bytes[0]+=path.stat().st_size
    seen_outputs.add(str(path))

def measured_ffmpeg(command,*a,**kw):
 result=original_run(command,*a,**kw)
 record_output(command[-1])
 return result

def measured_segment(*a,**kw):
 result=original_segment(*a,**kw)
 record_output(kw.get('output_path') or a[3])
 return result
original_copy=module.shutil.copy2

def measured_copy(source,destination,*a,**kw):
 result=original_copy(source,destination,*a,**kw)
 record_output(result,copied=True)
 return result
module.shutil.copy2=measured_copy
module._run_ffmpeg_progress=measured_ffmpeg;module._render_segment=measured_segment

def progress(percent,detail):
 last[0]=time.monotonic()
 if invalid:raise RuntimeError(invalid[0])
 (roundroot/'progress.json').write_text(json.dumps({'variant':args.variant,'round':args.round,'elapsed_sec':round(time.monotonic()-start,2),'percent':percent,'detail':str(detail)}))

def processes():
 rows=[]
 for line in subprocess.check_output(['ps','-axo','pid=,ppid=,pcpu=,rss='],text=True).splitlines():
  fields=line.split()
  if len(fields)==4:
   try:rows.append((int(fields[0]),int(fields[1]),float(fields[2]),int(fields[3])))
   except ValueError:pass
 owned={os.getpid()}
 for _ in range(5):owned|={pid for pid,parent,cpu,rss in rows if parent in owned}
 return owned,rows

def monitor():
 with (roundroot/'resources.jsonl').open('w') as log:
  while not stopped.wait(5):
   owned,rows=processes()
   log.write(json.dumps({'elapsed_sec':round(time.monotonic()-start,2),'cpu_percent':sum(cpu for pid,parent,cpu,rss in rows if pid in owned),'rss_kib':sum(rss for pid,parent,cpu,rss in rows if pid in owned)})+'\n');log.flush()
   if user_app_open():invalid.append('User opened application; competing-work measurement invalid')
   if time.monotonic()-last[0]>180:invalid.append('No export progress for 180 seconds; baseline/variant stalled')
   if invalid:
    for pid in owned-{os.getpid()}:
     try:os.kill(pid,9)
     except ProcessLookupError:pass
    return
watcher=threading.Thread(target=monitor,daemon=True);watcher.start()
usage_before=resource.getrusage(resource.RUSAGE_CHILDREN);self_before=time.process_time()
try:
 warnings=[]
 module._render_plan(project,segments,master,output,str(plan.get('platform') or 'youtube'),bitrate,warnings,progress,
    master_window_start=float(plan.get('master_window_start_sec') or segments[0].get('master_start_sec') or 0))
 wall=time.monotonic()-start
 if invalid:raise RuntimeError(invalid[0])
 assert all(identity[p]==(Path(p).stat().st_size,Path(p).stat().st_mtime_ns) for p in all_sources),'Source changed during benchmark'
 usage_after=resource.getrusage(resource.RUSAGE_CHILDREN)
 result={'variant':args.variant,'round':args.round,'wall_sec':round(wall,3),'output':str(output),'output_bytes':output.stat().st_size,
   'logical_completed_media_write_bytes':logical_bytes[0],'bytes_scope':'Completed FFmpeg/segment media outputs and copy2 media writes; excludes JSON, partial failed writes, filesystem journaling and faststart internal rewrite' ,
   'python_cpu_sec':round(time.process_time()-self_before,3),'child_cpu_sec':round(usage_after.ru_utime+usage_after.ru_stime-usage_before.ru_utime-usage_before.ru_stime,3),
   'plan_sha256':hashlib.sha256(planfile.read_bytes()).hexdigest(),'bitrate':bitrate,'performance':project.data.get('_export_performance'),
   'warnings':warnings,'cache_definition':'Cold = empty private rendered-segment cache, not cleared OS/source/analysis caches; warm = reuse same private segments',
   'reference_export_commit':'7ae1d8d','implementation_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()}
 (roundroot/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:result[k] for k in ['variant','round','wall_sec','output_bytes']}),flush=True)
except BaseException as exc:
 (roundroot/'failure.json').write_text(json.dumps({'error':str(exc),'elapsed_sec':time.monotonic()-start,'measurement_valid':False},indent=2))
 raise
finally:
 stopped.set();watcher.join(timeout=7)
 (roundroot/'memory-after.txt').write_text(subprocess.check_output(['vm_stat'],text=True))
