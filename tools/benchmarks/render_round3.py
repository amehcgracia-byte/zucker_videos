"""Four-round driver, fixed reference, private caches and frozen plan.

Supply a fresh root containing frozen-project.json and frozen-edit-plan.json.
No renderer changes or competing work should run until the driver completes.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--source', required=True)
parser.add_argument('--root', required=True)
args = parser.parse_args()
root = Path(args.root)
assert (root / 'frozen-project.json').is_file() and (root / 'frozen-edit-plan.json').is_file()
script = Path(__file__).with_name('render_ab.py')
results = []
for variant, round_ in [('before', 'cold'), ('after', 'cold'), ('after', 'warm'), ('before', 'warm')]:
    print(f'Starting {variant} {round_}', flush=True)
    with (root / f'{variant}-{round_}.log').open('x') as log:
        subprocess.run([sys.executable, str(script), '--source', args.source, '--root', str(root),
                        '--reference', '1ffb168', '--workers', '4', '--variant', variant, '--round', round_,
                        '--metal-remap', '--overlap-verification'], stdout=log, stderr=subprocess.STDOUT, check=True)
    result = json.loads((root / variant / round_ / 'result.json').read_text())
    results.append(result)
    (root / 'summary.json').write_text(json.dumps(results, indent=2))
    print(variant, round_, result['wall_sec'], result['metal_backend_stats'], flush=True)
