"""Four-worker native reprojection profile; private outputs, untouched user caches.

Map and OpenCV timings are nested wall measurements, not CPU attribution.
Decoder pipe waits include decode/conversion and overlap with encoder work.
"""
import argparse
import concurrent.futures
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
from core import spherical_motion


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--plan', required=True)
    parser.add_argument('--root', required=True)
    parser.add_argument('--opencv-threads', type=int)
    parser.add_argument('--fast-wrap', action='store_true')
    parser.add_argument('--incremental', action='store_true')
    parser.add_argument('--metal-library')
    args = parser.parse_args()
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=False)
    if subprocess.run(['pgrep', '-f', '^/Applications/Zucker Editor.app/Contents/MacOS/Zucker Editor'], capture_output=True).returncode == 0:
        raise SystemExit('Close application before measuring')
    if args.opencv_threads is not None:
        spherical_motion.cv2.setNumThreads(args.opencv_threads)
    source = load_project(args.source)
    project = Project(root / 'case', copy.deepcopy(source.data))
    project.data.setdefault('settings', {}).setdefault('export', {})['spherical_remap_backend'] = 'cpu'
    project.ensure_dirs()
    plan = json.loads(Path(args.plan).read_text())
    segments = export._frame_normalized_segments(export._apply_saved_spherical_landmarks(project, plan['segments']))
    candidates = [(i, s) for i, s in enumerate(segments) if s.get('spherical_shot')]
    selected = [candidates[round(j * (len(candidates) - 1) / 15)] for j in range(16)]
    # Include every authored movement, including planet transitions if present.
    movements = {s['spherical_shot'].get('movement') for _, s in selected}
    for pair in candidates:
        movement = pair[1]['spherical_shot'].get('movement')
        if movement not in movements:
            selected.append(pair)
            movements.add(movement)
    code = Path(spherical_motion.__file__).read_text()
    (root / 'spherical_motion.source.py').write_text(code)
    if args.incremental:
        (root / 'map_sampler.source.py').write_text(Path(__file__).with_name('keyframe_maps.py').read_text())
    replacements = {
        'count = decoder.stdout.readinto(frame_bytes[received:])': '_t = time.perf_counter()\n                    count = decoder.stdout.readinto(frame_bytes[received:])\n                    METRICS["decoder_pipe_wait_sec"] += time.perf_counter() - _t',
        'encoder.stdin.write(memoryview(pixels).cast("B"))': '_t = time.perf_counter()\n                encoder.stdin.write(memoryview(pixels).cast("B"))\n                METRICS["encoder_pipe_wait_sec"] += time.perf_counter() - _t',
    }
    if args.fast_wrap:
        replacements['mx %= sw'] = ('if mx.min() >= -sw and mx.max() < 2 * sw:\n'
                                  '        np.subtract(mx, sw, out=mx, where=mx >= sw)\n'
                                  '        np.add(mx, sw, out=mx, where=mx < 0)\n'
                                  '    else:\n'
                                  '        mx %= sw')
    if args.incremental:
        replacements['previous_pose = None'] = (
            'from tools.benchmarks.keyframe_maps import KeyframeMaps\n'
            '            poses = [(pose_sampler(i / 30) if pose_sampler else motion_pose(shot, duration, i / 30)) for i in range(frame_count)]\n'
            '            global MAP_SAMPLER\n'
            '            MAP_SAMPLER = KeyframeMaps(lambda p: reproject_maps((sw, sh), (1920, 1080), shot, p), poses, (sw, sh))\n'
            '            previous_pose = None')
        replacements['maps = reproject_maps((sw, sh), (1920, 1080), shot, pose)'] = 'maps = MAP_SAMPLER(index)'
    for old, new in replacements.items():
        assert code.count(old) == 1, old
        code = code.replace(old, new)
    bitrate = json.loads((source.artifacts_dir / 'export_manifest.json').read_text())['target_video_bitrate']

    def measure(pair):
        index, segment = pair
        metrics = dict.fromkeys(['map_sec', 'map_resize_sec', 'remap_sec', 'decoder_pipe_wait_sec', 'encoder_pipe_wait_sec'], 0.)
        metrics.update(map_calls=0, remap_calls=0)
        native = types.ModuleType('native_movement_profile')
        native.__file__ = spherical_motion.__file__
        exec(compile(code, native.__file__, 'exec'), native.__dict__)
        native.METRICS = metrics
        cv = native.cv2
        metal = None
        if args.metal_library:
            from tools.benchmarks.metal_remap import MetalRemap
            probe = export._segment_source_info(project, segment)['probe']
            metal = MetalRemap(args.metal_library, (probe['width'], probe['height']), (1920, 1080))

        def timed(name, fn):
            def call(*a, **kw):
                start = time.perf_counter()
                try:
                    return fn(*a, **kw)
                finally:
                    metrics[name + '_sec'] += time.perf_counter() - start
                    if name + '_calls' in metrics:
                        metrics[name + '_calls'] += 1
            return call

        class CVProxy:
            resize = staticmethod(timed('map_resize', cv.resize))
            remap = staticmethod(timed('remap', metal.remap if metal else cv.remap))
            def __getattr__(self, name):
                return getattr(cv, name)

        native.cv2 = CVProxy()
        native.reproject_maps = timed('map', native.reproject_maps)
        # Separate export namespace avoids replacing shared production globals.
        module = types.ModuleType('core.stages.export_profile')
        module.__package__ = 'core.stages'
        module.__file__ = export.__file__
        exec(compile(Path(export.__file__).read_text(), export.__file__, 'exec'), module.__dict__)
        module.run_reprojected_command = native.run_reprojected_command
        output = root / f'{index + 1:03d}.mp4'
        commands = []
        start = time.perf_counter()
        module._render_segment(project, segment, project.data['inputs']['master']['path'], output,
                               'youtube', bitrate, color_profile={}, overlay_config={}, warnings=[], command_recorder=commands)
        wall = time.perf_counter() - start
        start = time.perf_counter()
        module._verify_segment_frame_duration(output, module._segment_frame_count(segment), 'concurrent profile')
        verify = time.perf_counter() - start
        return dict(plan_index=index + 1, movement=segment['spherical_shot'].get('movement'),
                    frames=module._segment_frame_count(segment), wall_sec=wall, verify_sec=verify,
                    timings=metrics, output_bytes=output.stat().st_size, commands=commands,
                    incremental_stats=getattr(getattr(native, 'MAP_SAMPLER', None), 'stats', None))

    started = time.perf_counter()
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for future in concurrent.futures.as_completed([pool.submit(measure, pair) for pair in selected]):
            row = future.result()
            rows.append(row)
            result = dict(workers=4, opencv_threads=spherical_motion.cv2.getNumThreads(),
                          fast_wrap=args.fast_wrap,
                          incremental=args.incremental,
                          metal_library=args.metal_library,
                          elapsed_sec=time.perf_counter() - started, segments=rows,
                          watchdog_events=project.data.get('_export_watchdog_events', []))
            (root / 'results.json').write_text(json.dumps(result, indent=2))
            print(row['plan_index'], row['movement'], round(row['wall_sec'], 3), flush=True)


if __name__ == '__main__':
    main()
