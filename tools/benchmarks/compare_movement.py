"""Full-frame objective comparison of private movement profile outputs."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core.ffmpeg import tool_status
from tools.benchmarks.zoom_quality import compare

parser = argparse.ArgumentParser()
parser.add_argument('--before', required=True)
parser.add_argument('--after', required=True)
args = parser.parse_args()
before, after = Path(args.before), Path(args.after)
root = after / 'quality'; root.mkdir(exist_ok=False)
ffmpeg = tool_status()['ffmpeg_path']; rows = []
for first in sorted(before.glob('*.mp4')):
    second = after / first.name
    identical = hashlib.sha256(first.read_bytes()).digest() == hashlib.sha256(second.read_bytes()).digest()
    row = dict(file=first.name, binary_identical=identical)
    # Do not spend a second full decode proving exact binary equality.
    if not identical:
        row.update(compare(ffmpeg, first, second, root / first.stem))
        for name, video in [('before', first), ('after', second)]:
            subprocess.run([ffmpeg, '-v', 'error', '-i', str(video), '-vf', f"select=eq(n\\,{row['worst_frame']})",
                            '-frames:v', '1', str(root / f'{first.stem}-{name}.png')], check=True)
    rows.append(row); (root / 'result.json').write_text(json.dumps(rows, indent=2))
    print(row, flush=True)
