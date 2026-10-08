"""Early-downscale quality experiment, never modifies the production renderer."""
import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys
import time
import types

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core.project import Project, load_project
from core.stages import export


def compare(ffmpeg, first, second, root):
    stats = root.with_suffix('.ssim.txt')
    vmaf = root.with_suffix('.vmaf.json')
    subprocess.run([ffmpeg, '-v', 'error', '-i', str(second), '-i', str(first),
                    '-lavfi', f'[0:v][1:v]ssim=stats_file={stats}', '-an', '-f', 'null', '-'], check=True)
    subprocess.run([ffmpeg, '-v', 'error', '-i', str(second), '-i', str(first),
                    '-lavfi', f'[0:v][1:v]libvmaf=log_fmt=json:log_path={vmaf}:n_threads=2', '-an', '-f', 'null', '-'], check=True)
    values = [float(line.split('All:')[1].split()[0]) for line in stats.read_text().splitlines()]
    scores = json.loads(vmaf.read_text())
    return dict(ssim_mean=sum(values)/len(values), ssim_min=min(values), worst_frame=values.index(min(values)),
                vmaf=scores['pooled_metrics']['vmaf'], frames=len(values))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--plan', required=True)
    parser.add_argument('--root', required=True)
    args = parser.parse_args()
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=False)
    source = load_project(args.source)
    project = Project(root / 'case', copy.deepcopy(source.data)); project.ensure_dirs()
    plan = json.loads(Path(args.plan).read_text())
    segments = export._frame_normalized_segments(export._apply_saved_spherical_landmarks(project, plan['segments']))
    moving = sorted([(i, s) for i, s in enumerate(segments) if s.get('motion')],
                    key=lambda pair: max(pair[1]['motion']['zoom_start'], pair[1]['motion']['zoom_end']), reverse=True)[:2]
    sony = [(i, copy.deepcopy(s)) for i, s in enumerate(segments) if s.get('camera_id') == 'sony']
    selected = moving + [sony[len(sony)//3], sony[len(sony)*2//3]]
    for _, s in selected[2:]:
        s['motion'] = copy.deepcopy(moving[0][1]['motion'])
    candidate = types.ModuleType('core.stages.export_zoom_experiment')
    candidate.__package__ = 'core.stages'; candidate.__file__ = export.__file__
    exec(compile(Path(export.__file__).read_text(), export.__file__, 'exec'), candidate.__dict__)
    original_graph = candidate._segment_filtergraph
    candidate._segment_filtergraph = lambda *a, **kw: original_graph(*a, **kw).replace('3840:2160', '1920:1080')
    bitrate = json.loads((source.artifacts_dir / 'export_manifest.json').read_text())['target_video_bitrate']
    rows = []
    for n, (index, segment) in enumerate(selected):
        row = dict(plan_index=index + 1, camera=segment['camera_id'], added_motion=n >= 2, motion=segment['motion'])
        paths = []
        # Invert order for alternate cuts to limit one-sided source-cache bias.
        for name, module in ([('before', export), ('after', candidate)] if n % 2 == 0 else [('after', candidate), ('before', export)]):
            output = root / f'{index + 1:03d}-{name}.mp4'; commands = []; start = time.perf_counter()
            module._render_segment(project, segment, project.data['inputs']['master']['path'], output,
                                   'youtube', bitrate, color_profile={}, overlay_config={}, warnings=[], command_recorder=commands)
            row[name] = dict(wall_sec=time.perf_counter()-start, commands=commands, output=str(output))
        row['quality'] = compare(export._ffmpeg_path(), Path(row['before']['output']), Path(row['after']['output']), root / f'{index + 1:03d}')
        rows.append(row); (root / 'results.json').write_text(json.dumps(rows, indent=2))
        print(index + 1, row['camera'], row['quality'], flush=True)


if __name__ == '__main__':
    main()
