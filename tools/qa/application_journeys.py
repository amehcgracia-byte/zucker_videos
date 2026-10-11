"""Real browser + real pipeline audit. Run on disposable CI runners, not user data.

Only the file drop is constructed programmatically. Navigation, settings, review,
caption burning and composition use actual UI controls; API reads observe status.
No production stages, scores, synchronization or models are mocked.
"""
from __future__ import annotations
import argparse, base64, json, os, platform, shutil, socket, subprocess, sys, time, traceback
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]


def run(command, **kwargs):
    return subprocess.run(command, check=True, capture_output=True, text=True, **kwargs)


def fixtures(folder):
    folder.mkdir(parents=True, exist_ok=True)
    audio = folder / 'master.wav'
    run(['ffmpeg','-v','error','-y','-f','lavfi','-i','anoisesrc=color=pink:seed=1234:amplitude=0.2:duration=12','-ar','48000',str(audio)])
    for name, size, effect in [('camera-a.mp4','640x360','null'),('camera-b.mp4','640x360','hue=h=90'),('sphere-360.mp4','640x320','null')]:
        run(['ffmpeg','-v','error','-y','-f','lavfi','-i',f'testsrc2=size={size}:rate=30:duration=12','-i',str(audio),'-vf',effect,'-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(folder/name)])
    speech = folder/'speech.wav'
    text = 'Welcome to our rehearsal. Today we are preparing a new song together. The guitar player arrived early to check the sound. We had a problem with the microphone, but now everything works. I love playing music with my friends. After the rehearsal we will perform on stage. Thank you for joining us today.'
    if sys.platform == 'darwin':
        run(['say','-v','Samantha','-o',str(folder/'speech.aiff'),text])
        run(['ffmpeg','-v','error','-y','-i',str(folder/'speech.aiff'),str(speech)])
    else:
        script = folder/'speech.ps1'
        script.write_text("Add-Type -AssemblyName System.Speech\n$s = New-Object System.Speech.Synthesis.SpeechSynthesizer\n$s.SetOutputToWaveFile('"+str(speech).replace("'","''")+"')\n$s.Speak('"+text+"')\n$s.Dispose()\n")
        run(['powershell','-NoProfile','-File',str(script)])
    run(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=size=640x360:rate=30','-i',str(speech),'-c:v','libx264','-preset','ultrafast','-c:a','aac','-shortest',str(folder/'interview.mp4')])
    return folder


def json_get(url):
    with urlopen(url, timeout=15) as r: return json.load(r)


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1',0)); return s.getsockname()[1]


def journey(browser, mode, media, output):
    folder=output/mode; folder.mkdir(parents=True,exist_ok=True)
    port=free_port(); url=f'http://127.0.0.1:{port}'
    env={**os.environ,'ZUCKER_DATA_ROOT':str(folder/'data'),'PYTHONUNBUFFERED':'1'}
    log=(folder/'server.log').open('w',encoding='utf-8')
    command=[sys.executable,'-c',f'from server.api import create_app; create_app(dev=True).run(host="127.0.0.1", port={port}, threaded=True, use_reloader=False)']
    process=subprocess.Popen(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
    context=browser.new_context(viewport={'width':1440,'height':1000},record_video_dir=str(folder/'browser-video'))
    context.tracing.start(screenshots=True,snapshots=True,sources=True)
    page=context.new_page(); page.set_default_timeout(45000)
    errors=[]; failures=[]; report={'mode':mode,'os':platform.platform(),'passed':False}
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.on('response',lambda response:failures.append({'url':response.url,'status':response.status}) if response.status>=400 and '/api/' in response.url else None)
    def capture(name): page.screenshot(path=str(folder/f'{name}.png'),full_page=True)
    try:
        deadline=time.monotonic()+120
        while True:
            try: json_get(url+'/api/v1/wizard/status'); break
            except Exception:
                if process.poll() is not None or time.monotonic()>deadline: raise RuntimeError('Server did not start')
                time.sleep(1)
        page.goto(url)
        tutorial=json_get(url+'/api/v1/tutorial')
        if not tutorial.get('answer'):
            page.get_by_role('button',name='No',exact=True).click()
        page.locator('#editTypeDialog').wait_for(state='visible')
        page.locator('#closeEditType').click()
        page.locator('#newProject').click()
        # Click the child label, not just card padding (regression coverage).
        page.locator(f'[data-platform="{mode}"] strong').click()
        page.locator('#editTypeDialog').wait_for(state='hidden')
        page.locator('#videoName').fill('QA '+mode)
        files=[media/'camera-a.mp4',media/'camera-b.mp4']
        if mode in {'youtube','reel'}: files.append(media/'master.wav')
        if mode=='360': files=[media/'sphere-360.mp4',media/'master.wav']
        if mode=='backstage': files=[media/'interview.mp4']
        payload=[{'name':f.name,'data':base64.b64encode(f.read_bytes()).decode()} for f in files]
        page.evaluate('''files=>{const d=new DataTransfer();for(const f of files){const bytes=Uint8Array.from(atob(f.data),c=>c.charCodeAt(0));d.items.add(new File([bytes],f.name));}document.querySelector('#dropZone').dispatchEvent(new DragEvent('drop',{bubbles:true,dataTransfer:d}));}''',payload)
        page.wait_for_function('pendingImports === 0 && detected.videos.length > 0')
        capture('01-imported')
        page.locator('#confirmFiles').click()
        page.locator('#step2.active').wait_for(timeout=120000)
        for other in ['reel','medley','backstage']:
            visible=page.locator('#'+other+'Options').is_visible()
            assert visible==(mode==other),(mode,other,visible)
        assert page.locator('#sphericalSetup').is_visible()==(mode=='360')
        # Navigate back and forward before rendering; keep imported inputs.
        page.locator('[data-step-nav="1"]').click()
        assert page.locator('#chips').inner_text().strip()
        page.locator('#confirmFiles').click()
        page.locator('#step2.active').wait_for(timeout=120000)
        if mode=='medley': page.locator('#medleyDuration').fill('10')
        if mode=='reel':
            if not page.locator('#reelDuration').is_visible():
                page.locator('#reelOptions summary').click()
            page.locator('#reelDuration').fill('20')
        capture('02-settings')
        page.locator('#startWizard').click()
        deadline=time.monotonic()+900; last=None; actions=set()
        while time.monotonic()<deadline:
            status=json_get(url+'/api/v1/wizard/status')
            current=status.get('status')
            assert not failures, f'API requests failed: {failures}'
            if current!=last:
                print(mode,current,status.get('message'),flush=True); last=current
                (folder/'status.json').write_text(json.dumps(status,indent=2),encoding='utf-8')
            if current=='waiting_review' and 'review' not in actions:
                page.locator('#renderReviewedTop').click(timeout=120000); actions.add('review')
            if current=='waiting_paper_edit' and 'paper' not in actions:
                capture('03-paper'); page.locator('#approvePaperEdit').click(timeout=120000); actions.add('paper')
            if current=='failed': raise RuntimeError(json.dumps(status))
            if current=='done': break
            page.wait_for_timeout(1000)
        else: raise TimeoutError('Pipeline did not finish in 15 minutes')
        result=status.get('result') or {}; path=Path(result['path'])
        assert path.is_file() and path.stat().st_size>1000
        probe=json.loads(run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)]).stdout)
        assert float(probe['format']['duration'])>0
        assert any(s['codec_type']=='video' for s in probe['streams'])
        assert any(s['codec_type']=='audio' for s in probe['streams'])
        # Decode the complete output, not only its first thumbnail.
        run(['ffmpeg','-v','error','-i',str(path),'-f','null','-'],timeout=180)
        shutil.copy2(path,folder/'render.mp4')
        page.wait_for_timeout(2000); capture('04-rendered')
        if mode=='reel':
            if not page.locator('#captionText').is_visible(): page.locator('[data-step-nav="5"]').click()
            page.locator('#captionText').fill('A first phrase\n\nA second phrase')
            page.locator('#captionAutoArrange').click()
            page.locator('#burnCaptions').click()
            page.wait_for_function("document.querySelector('#captionBurnStatus').textContent.includes('Created')",timeout=180000)
            page.wait_for_function("document.querySelector('#composeVideo').readyState >= 2",timeout=60000)
            capture('05-captions')
            page.locator('#composeContinue').click()
            deadline=time.monotonic()+240
            while time.monotonic()<deadline:
                final=json_get(url+'/api/v1/wizard/status')
                if final.get('status')=='failed': raise RuntimeError(json.dumps(final))
                if final.get('status')=='done' and '_composed' in str((final.get('result') or {}).get('path')): break
                page.wait_for_timeout(1000)
            else: raise TimeoutError('Caption composition did not finish')
            shutil.copy2(final['result']['path'],folder/'captioned.mp4')
        assert not errors,errors
        report.update(passed=True,duration=probe['format']['duration'],result=result,review_actions=sorted(actions))
    except Exception:
        report['error']=traceback.format_exc(); capture('failure')
    finally:
        report.update(page_errors=errors,http_failures=failures)
        (folder/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        context.tracing.stop(path=str(folder/'trace.zip')); context.close()
        process.terminate()
        try: process.wait(timeout=15)
        except subprocess.TimeoutExpired: process.kill(); process.wait()
        log.close()
        # Keep evidence and small renders, not model/proxy caches in artifacts.
        for p in (folder/'data').rglob('*.log'):
            dest=folder/'pipeline-logs'/p.name; dest.parent.mkdir(exist_ok=True); shutil.copy2(p,dest)
        shutil.rmtree(folder/'data',ignore_errors=True)
    return report


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--output',type=Path,required=True); parser.add_argument('--mode',choices=['youtube','reel','medley','360','backstage']); args=parser.parse_args()
    output=args.output.resolve(); output.mkdir(parents=True,exist_ok=True)
    media=output/'fixtures'
    if not (media/'ready').exists():
        fixtures(media)
        (media/'ready').write_text('ready', encoding='utf-8')
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser=pw.chromium.launch(); reports=[]
        for mode in ([args.mode] if args.mode else ['youtube','reel','medley','360','backstage']):
            reports.append(journey(browser,mode,media,output))
        browser.close()
    (output/(f'{args.mode}-summary.json' if args.mode else 'summary.json')).write_text(json.dumps(reports,indent=2),encoding='utf-8')
    print(json.dumps(reports,indent=2),flush=True)
    return 0 if all(r['passed'] for r in reports) else 1

if __name__=='__main__': raise SystemExit(main())
