"""MP3 project/library storage; source containers are temporary, never retained."""
from pathlib import Path
import subprocess


def mp3(source,target,ffmpeg=None):
    from core.ffmpeg import tool_status
    ffmpeg=ffmpeg or str(tool_status().get('ffmpeg_path') or 'ffmpeg')
    try:
        subprocess.run([ffmpeg,'-v','error','-nostdin','-y','-i',str(source),'-map','0:a:0','-vn','-c:a','libmp3lame','-b:a','192k','-ar','48000','-threads','2',str(target)],check=True,capture_output=True,timeout=180)
        if not Path(target).is_file() or not Path(target).stat().st_size:raise ValueError('No audio could be decoded')
        return Path(target)
    except BaseException:
        Path(target).unlink(missing_ok=True)
        raise
