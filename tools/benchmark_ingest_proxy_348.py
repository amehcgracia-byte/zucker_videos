import os,subprocess,tempfile,time,json,pathlib
ffmpeg='/usr/bin/ffmpeg'
ffprobe='/usr/bin/ffprobe'

def run(args):
    t=time.perf_counter();p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=110)
    return p,time.perf_counter()-t

with tempfile.TemporaryDirectory(prefix='jm348-synthetic-benchmark-',dir='/var/tmp') as tmp:
    root=pathlib.Path(tmp);source=root/'source.mp4'
    p,setup=run([ffmpeg,'-hide_banner','-loglevel','error','-f','lavfi','-i','testsrc2=size=960x540:rate=30:duration=5','-c:v','libx264','-preset','ultrafast','-threads','2','-crf','16','-pix_fmt','yuv420p',str(source)])
    if p.returncode:raise RuntimeError(p.stderr[-600:])
    rows=[]
    for preset in ['medium','fast','veryfast']:
        dest=root/f'{preset}.mp4'
        cmd=[ffmpeg,'-hide_banner','-loglevel','error','-nostdin','-i',str(source),'-map','0:v:0','-vf','scale=trunc(iw/2)*2:trunc(ih/2)*2,fps=30,format=yuv420p','-r','30','-fps_mode','cfr','-c:v','libx264','-preset',preset,'-threads','2','-b:v','2500k','-maxrate','3500k','-bufsize','7000k','-pix_fmt','yuv420p','-an','-y',str(dest)]
        p,elapsed=run(['nice','-n','10',*cmd]);
        if p.returncode:raise RuntimeError(p.stderr[-700:])
        metrics=[ffmpeg,'-hide_banner','-nostats','-i',str(source),'-i',str(dest),'-lavfi','psnr','-f','null','-']
        m,_=run(metrics)
        import re
        psnr=re.findall(r'average:([0-9.]+)',m.stderr)
        rows.append({'preset':preset,'encode_seconds':round(elapsed,3),'bytes':dest.stat().st_size,'psnr_db':round(float(psnr[-1]),2) if psnr else None})
    print(json.dumps({'source_kind':'synthetic_testsrc2','source_duration_seconds':5,'source_resolution':'960x540','threads':2,'results':rows,'note':'Synthetic isolated encoding only; excludes upload, proxy analysis, other pipeline stages and concurrent work.'},indent=2))
