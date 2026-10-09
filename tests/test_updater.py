import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import zipfile
import pytest
from core import updater


def release(version='2.5.4', extension='.dmg'):
    return dict(tag_name='v'+version, assets=[dict(state='uploaded',name='Zucker.Editor'+extension,
        browser_download_url=f'https://github.com/{updater.REPOSITORY}/releases/download/v{version}/installer'+extension,
        digest='sha256:'+'a'*64,size=10)])


def test_version_carry_and_no_downgrade():
    assert updater.version_tuple('v2.5.0') > updater.version_tuple('2.4.9')
    assert updater.release_asset(release('2.4.9'), 'darwin','2.5.3') is None
    assert updater.release_asset(release('2.5.3'), 'darwin','2.5.3') is None
    assert updater.release_asset(release(), 'darwin','2.5.3')['version'] == '2.5.4'
    assert updater.release_asset(release(extension='-windows.zip'),'win32','2.5.3')


@pytest.mark.parametrize('change',[{'prerelease':True},{'draft':True}])
def test_preview_releases_not_offered(change):
    assert updater.release_asset({**release(),**change},'darwin','2.5.3') is None


def test_wrong_host_or_missing_digest_rejected():
    candidate=release();candidate['assets'][0]['browser_download_url']='https://evil.example/installer.dmg'
    with pytest.raises(ValueError,match='URL'):updater.release_asset(candidate,'darwin','2.5.3')
    candidate=release();candidate['assets'][0]['digest']=None
    with pytest.raises(ValueError,match='SHA-256'):updater.release_asset(candidate,'darwin','2.5.3')


def test_download_hash_and_size_before_install(tmp_path,monkeypatch):
    payload=b'installer';asset=release()['assets'][0]
    asset.update(size=len(payload),digest='sha256:'+hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(updater.urllib.request,'urlopen',lambda *args,**kwargs:io.BytesIO(payload))
    path=tmp_path/'installer.dmg';progress=[]
    updater.download_asset(asset,path,progress.append)
    assert path.read_bytes()==payload
    path.unlink();asset['digest']='sha256:'+'b'*64
    with pytest.raises(ValueError,match='verification'):updater.download_asset(asset,path,progress.append)
    assert not path.exists()


@pytest.mark.parametrize('name',['../bad.exe','C:/bad.exe','a/../../bad','/bad','..\\bad'])
def test_windows_archive_cannot_escape_staging(tmp_path,name):
    archive=tmp_path/'installer.zip'
    with zipfile.ZipFile(archive,'w') as package:package.writestr(name,b'bad')
    with pytest.raises(ValueError,match='Unsafe'):updater.extract_windows(archive,tmp_path/'stage','2.5.4')


def test_windows_exe_resources_and_version_must_match(tmp_path):
    archive=tmp_path/'installer.zip'
    with zipfile.ZipFile(archive,'w') as package:
        package.writestr('Zucker Editor 2.5.4.exe',b'exe')
        package.writestr('_internal/build_info.json',json.dumps({'version':'2.5.4'}))
    assert updater.extract_windows(archive,tmp_path/'stage','2.5.4').name=='Zucker Editor 2.5.4.exe'
    with pytest.raises(ValueError):updater.extract_windows(archive,tmp_path/'wrong','2.5.5')


@pytest.mark.skipif(sys.platform == 'win32', reason='macOS helper uses bash')
@pytest.mark.parametrize('launch_ok',[True,False])
def test_mac_helper_replaces_after_exit_and_rolls_back(tmp_path,launch_ok):
    installed=tmp_path/'Zucker Editor.app';staged=tmp_path/'.Zucker-update-test.app';backup=tmp_path/'.Zucker-backup-test.app'
    installed.mkdir();staged.mkdir();(installed/'version').write_text('old');(staged/'version').write_text('new')
    launcher=tmp_path/'open';launcher.write_text('#!/bin/sh\nexit '+('0' if launch_ok else '1')+'\n');launcher.chmod(0o755)
    script=updater.mac_helper(99999999,installed,staged,backup)
    import shlex
    script=script.replace('/usr/bin/open',shlex.quote(str(launcher)))
    result=subprocess.run(['/bin/bash','-c',script],capture_output=True,text=True)
    assert (installed/'version').read_text()==('new' if launch_ok else 'old')
    assert result.returncode==(0 if launch_ok else 1)


def test_update_api_requires_consent_and_idle(tmp_path,monkeypatch):
    from core import storage
    from server.api import create_app
    monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'data'))
    app=create_app();client=app.test_client();manager=app.config['ZUCKER_UPDATER']
    assert client.post('/api/v1/updates/download',json={}).status_code==403
    token=manager.token
    state=app.config['ZUCKER_STATE']
    monkeypatch.setattr(state.wizard,'status',lambda:dict(status='waiting_review'))
    assert client.post('/api/v1/updates/download',json={'token':token}).status_code==409
    monkeypatch.setattr(state.wizard,'status',lambda:dict(status='idle'))
    assert client.post('/api/v1/updates/download',json={'token':token},headers={'Origin':'https://evil.example'}).status_code==403
    manager.set_state(status='downloading')
    assert client.post('/api/v1/wizard/start',json={}).status_code==409


def test_failed_preparation_keeps_running_app(monkeypatch,tmp_path):
    manager=updater.UpdateManager();manager.asset=release()['assets'][0]
    monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'data'))
    def failed(*args):raise ValueError('checksum failed')
    monkeypatch.setattr(updater,'download_asset',failed)
    manager._prepare()
    assert manager.snapshot()['status']=='failed'
    assert manager.prepared is None
    assert not list((tmp_path/'data/Cache/Updates').glob('update-*'))


def test_stable_windows_executable_for_future_releases(tmp_path):
    archive=tmp_path/'installer.zip'
    with zipfile.ZipFile(archive,'w') as package:
        package.writestr('Zucker Editor.exe',b'exe')
        package.writestr('_internal/build_info.json',json.dumps({'version':'2.5.4'}))
    assert updater.extract_windows(archive,tmp_path/'stage','2.5.4').name=='Zucker Editor.exe'


def test_mac_prepares_verified_staging_before_shutdown(tmp_path,monkeypatch):
    installed=tmp_path/'Zucker Editor.app';binary=installed/'Contents/MacOS/Zucker Editor';binary.parent.mkdir(parents=True);binary.write_bytes(b'old')
    monkeypatch.setattr(updater.sys,'platform','darwin')
    monkeypatch.setattr(updater.sys,'executable',str(binary))
    monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'storage'))
    monkeypatch.setattr(updater,'download_asset',lambda asset,path,progress:path.write_bytes(b'dmg'))
    calls=[]
    def run(command,**kwargs):
        calls.append(command)
        if command[:2]==['hdiutil','attach']:
            app=Path(command[-1])/'Zucker Editor.app';(app/'Contents/Resources').mkdir(parents=True)
            (app/'Contents/Resources/build_info.json').write_text(json.dumps({'version':'2.5.4'}))
        if command[0]=='ditto':
            import shutil;shutil.copytree(command[1],command[2])
        return subprocess.CompletedProcess(command,0,b'',b'')
    monkeypatch.setattr(updater.subprocess,'run',run)
    manager=updater.UpdateManager();manager.asset=updater.release_asset(release(),'darwin','2.5.3');manager._prepare()
    assert manager.snapshot()['status']=='ready'
    assert binary.read_bytes()==b'old'
    assert any(command[0]=='codesign' for command in calls)
    assert calls[-1][:2]==['hdiutil','detach']
    assert manager.prepared[0][0]=='/bin/bash'
    assert (tmp_path/'storage/Cache/Updates/pending.json').is_file()


def test_update_token_rejects_untrusted_host(tmp_path,monkeypatch):
    from server.api import create_app
    monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'storage'))
    app=create_app();manager=app.config['ZUCKER_UPDATER']
    response=app.test_client().post('/api/v1/updates/download',json={'token':manager.token},headers={'Host':'evil.example','Origin':'http://evil.example'})
    assert response.status_code==403


@pytest.mark.skipif(sys.platform != 'win32', reason='Runs the Windows replacement helper on Windows')
@pytest.mark.parametrize('launch_ok',[True,False])
def test_windows_helper_replaces_and_rolls_back(tmp_path,launch_ok):
    installed=tmp_path/'Zucker Editor';staged=tmp_path/'.Zucker-update-test';backup=tmp_path/'.Zucker-backup-test'
    installed.mkdir();staged.mkdir();(installed/'version').write_text('old');(staged/'version').write_text('new')
    script=updater.windows_helper(99999999,installed,staged,backup,'Zucker Editor.exe')
    script=('function Start-Process { param($FilePath) '+('return' if launch_ok else "throw 'launch failed'")+' }\n')+script
    path=tmp_path/'apply.ps1';path.write_text(script,encoding='utf-8-sig')
    result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(path)],capture_output=True,text=True)
    assert (installed/'version').read_text()==('new' if launch_ok else 'old')
    assert (result.returncode==0)==launch_ok

@pytest.mark.parametrize('status', ['up_to_date', 'unavailable', 'available', 'failed'])
def test_manual_check_retries_completed_checks(monkeypatch, status):
    calls = []
    class Thread:
        def __init__(self, **kwargs): calls.append(kwargs)
        def start(self): pass
    monkeypatch.setattr(updater.threading, 'Thread', Thread)
    manager = updater.UpdateManager()
    manager.set_state(status=status, error='old failure', version='old')
    manager.asset = {'old': True}
    assert manager.check()['status'] == status
    assert not calls
    assert manager.check(force=True)['status'] == 'checking'
    assert manager.asset is None
    assert manager.snapshot()['error'] is None
    assert len(calls) == 1
    manager.check(force=True)
    assert len(calls) == 1


@pytest.mark.parametrize('status', ['checking', 'downloading', 'preparing', 'ready', 'installing'])
def test_manual_check_preserves_active_update(status):
    manager = updater.UpdateManager()
    manager.set_state(status=status, percent=42)
    manager.asset = {'version': '9.0.0'}
    assert manager.check(force=True)['status'] == status
    assert manager.snapshot()['percent'] == 42
    assert manager.asset == {'version': '9.0.0'}
