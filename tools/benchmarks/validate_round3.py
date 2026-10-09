"""Validate four finished masters; never run alongside the timed rounds."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import statistics
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core.ffmpeg import tool_status


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def memory(path):
    text = path.read_text()
    return {key.strip('"'): int(value) for key, value in
            re.findall(r'^([^:\n]+):\s+(\d+)\.', text, re.M)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    args = parser.parse_args()
    root = Path(args.root)
    results = json.loads((root / 'summary.json').read_text())
    assert {(r['variant'], r['round']) for r in results} == {
        ('before', 'cold'), ('after', 'cold'), ('after', 'warm'), ('before', 'warm')}
    quality = root / 'quality'
    quality.mkdir(exist_ok=True)
    tools = tool_status()
    rows = []
    decoded = {}
    for result in results:
        label = f"{result['variant']}-{result['round']}"
        movie = Path(result['output'])
        binary_hash = sha256(movie)
        probe = json.loads(subprocess.check_output([
            tools['ffprobe_path'], '-v', 'error', '-show_streams', '-show_format',
            '-of', 'json', str(movie)], text=True))
        (quality / f'{label}-probe.json').write_text(json.dumps(probe, indent=2))
        # Decode both cold masters even if binary-identical. Warm duplicates
        # inherit that proof; different files receive their own complete decode.
        if result['round'] == 'cold' or binary_hash not in decoded:
            md5 = quality / f'{label}.framemd5'
            subprocess.run([
                tools['ffmpeg_path'], '-nostdin', '-v', 'error', '-xerror',
                '-threads', '2', '-i', str(movie), '-map', '0:v:0', '-map', '0:a:0',
                '-f', 'framemd5', '-y', str(md5)], check=True)
            decoded[binary_hash] = str(md5)
        else:
            md5 = Path(decoded[binary_hash])
        lines = [line for line in md5.read_text().splitlines() if line and not line.startswith('#')]
        counts = {}
        for line in lines:
            stream = line.split(',')[0].strip()
            counts[stream] = counts.get(stream, 0) + 1
        roundroot = root / result['variant'] / result['round']
        samples = [json.loads(line) for line in (roundroot / 'resources.jsonl').read_text().splitlines()]
        first, last = memory(roundroot / 'memory-before.txt'), memory(roundroot / 'memory-after.txt')
        row = {
            'label': label, 'output_sha256': binary_hash,
            'framemd5_path': str(md5), 'framemd5_sha256': sha256(md5),
            'frame_counts_by_stream': counts,
            'cpu_percent_sample_mean': statistics.mean(s['cpu_percent'] for s in samples),
            'peak_tree_rss_kib': max(s['rss_kib'] for s in samples),
            'memory_counter_deltas': {key: last[key] - first[key] for key in
                ['Swapins', 'Swapouts', 'Pageouts', 'Compressions', 'Decompressions']},
        }
        rows.append(row)
        (quality / 'validation.json').write_text(json.dumps(rows, indent=2))
        print(row, flush=True)
    assert len({r['framemd5_sha256'] for r in rows}) == 1, 'Decoded frames or timestamps differ'
    assert all(r['frame_counts_by_stream'].get('0') == 20256 for r in rows), 'Unexpected master frame count'
    fields = ['codec_type', 'codec_name', 'width', 'height', 'pix_fmt', 'color_range',
              'color_space', 'color_transfer', 'color_primaries', 'r_frame_rate',
              'avg_frame_rate', 'time_base', 'duration', 'sample_rate', 'channels', 'channel_layout']
    metadata = {row['label']: [{key: stream.get(key) for key in fields} for stream in
                json.loads((quality / f"{row['label']}-probe.json").read_text())['streams']]
                for row in rows}
    assert all(value == next(iter(metadata.values())) for value in metadata.values()), 'Stream metadata differ'
    (quality / 'metadata-comparison.json').write_text(json.dumps({'equal': True, 'files': metadata}, indent=2))


if __name__ == '__main__':
    main()
