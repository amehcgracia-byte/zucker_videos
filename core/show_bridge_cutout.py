"""Render saved polygon/matte cutouts against a saved still background.

Uses the existing source pixels, never a generated replacement person. The matte
sample remains bounded to its reviewed interval; this is not person segmentation.
"""
from __future__ import annotations
import json
import math
from pathlib import Path
import cv2
import numpy as np
from PIL import Image
from core import show_bridge_masks as masks


def prepare(event, analysis, source, materials, asset):
    atlas = matte = None
    polygon = event.get('polygon') or []
    if event.get('sample'):
        matte = analysis('cutout-sample')
        path = source('cutoutAtlas')
        if not matte or not path:
            raise ValueError('The reviewed moving matte and its atlas must be stored in this project')
        if float(event['start']) < float(matte['start']) or float(event['end']) > float(matte['end']) + .001:
            raise ValueError('This cutout extends beyond its reviewed moving matte; shorten it or provide another mask')
        try:
            atlas = np.asarray(Image.open(path).convert('RGBA'))[..., 3]
            mw, mh, cols, count = (int(matte[key]) for key in ('width', 'height', 'columns', 'count'))
            if min(mw, mh, cols, count) <= 0 or atlas.shape[1] < mw * cols or atlas.shape[0] < mh * math.ceil(count / cols):
                raise ValueError('The matte atlas dimensions do not match its saved frame grid')
        except (OSError, KeyError, TypeError) as exc:
            raise ValueError('The saved moving matte atlas cannot be read') from exc
    elif len(polygon) < 3:
        raise ValueError('Draw or import a closed cutout outline first')

    if not matte and (len(polygon) > 4096 or any(len(p) != 2 or not all(math.isfinite(float(x)) for x in p) for p in polygon)):
        raise ValueError('The cutout outline must contain finite coordinate pairs')
    if event.get('style') == 'paint':
        raise ValueError('Paint-pattern person fills are not supported by this cutout renderer yet; use original pixels or shadow')

    background = None
    mode = event.get('background') or 'original'
    if mode == 'library':
        chosen = str(event.get('backgroundAsset') or '')
        own = next((m for m in materials() if m.get('kind') == 'background' and m.get('id') == chosen), None)
        if own:
            if own.get('mediaType') == 'video' or own.get('type') == 'video' or own.get('duration'):
                raise ValueError('Moving background video is not supported by this cutout renderer yet')
            path = asset(own['asset']) if asset and own.get('asset') else None
        else:
            folder = masks.studio_files() / 'show-backgrounds'
            records = json.loads((folder / 'manifest.json').read_text())
            record = next((r for r in records if r['id'] == chosen), None)
            path = folder / record['file'] if record else None
        if not path or not path.is_file():
            raise ValueError('The chosen background is not saved in this project or the built-in library')
        try:
            background = np.asarray(Image.open(path).convert('RGB'))
        except OSError as exc:
            raise ValueError('The saved background must be a readable still image') from exc
    elif mode == 'photo':
        raise ValueError('This browser-only background must be imported into the project library first')
    elif mode not in ('original', 'dark', 'paper'):
        raise ValueError(f'Unknown cutout background: {mode}')

    def draw(frame, t, view, *_):
        e = masks.motion_at(event, t)
        h, w = frame.shape[:2]
        ox, oy, k = view
        if matte:
            index = min(int(matte['count']) - 1, max(0, int((t - matte['start']) * matte['fps'])))
            mw, mh, cols = int(matte['width']), int(matte['height']), int(matte['columns'])
            cell = atlas[index // cols * mh:(index // cols + 1) * mh, index % cols * mw:(index % cols + 1) * mw]
            matrix = np.float32([[1280 / mw * k, 0, -ox * k], [0, 720 / mh * k, -oy * k]])
            alpha = cv2.warpAffine(cell, matrix, (w, h), flags=cv2.INTER_LINEAR).astype(np.float32) / 255
        else:
            alpha = np.zeros((h, w), np.uint8)
            pts = np.array([[(x - ox) * k, (y - oy) * k] for x, y in polygon], np.int32)
            cv2.fillPoly(alpha, [pts], 255, lineType=cv2.LINE_AA)
            alpha = alpha.astype(np.float32) / 255
        rgb = frame.astype(np.float32) / 255
        if e.get('style') in ('shadow', 'paint'):
            colour = '#101316' if e['style'] == 'shadow' else e.get('color') or '#e6ae49'
            rgb = np.broadcast_to(np.array(Image.new('RGB', (1, 1), colour), np.float32)[0, 0] / 255, rgb.shape)
        rgba = np.dstack([rgb * alpha[..., None], alpha])
        anchor = e.get('anchor') or [640, 360]
        ax, ay = (anchor[0] - ox) * k, (anchor[1] - oy) * k
        tx, ty = (float(e.get('x', .5)) * 1280 - ox) * k, (float(e.get('y', .5)) * 720 - oy) * k
        angle = math.radians(float(e.get('rotation') or 0))
        scale = float(e.get('scale') or 1)
        c, s = math.cos(angle) * scale, math.sin(angle) * scale
        matrix = np.float32([[c, -s, tx - c * ax + s * ay], [s, c, ty - s * ax - c * ay]])
        rgba = cv2.warpAffine(rgba, matrix, (w, h), flags=cv2.INTER_LINEAR)
        if e.get('foregroundOnly') or mode == 'original':
            back = frame.astype(np.float32)
        elif background is not None:
            bh, bw = background.shape[:2]
            ratio = max(w / bw, h / bh)
            resized = cv2.resize(background, (max(w, round(bw * ratio)), max(h, round(bh * ratio))))
            y, x = (resized.shape[0] - h) // 2, (resized.shape[1] - w) // 2
            back = resized[y:y+h, x:x+w].astype(np.float32)
        else:
            colour = (22, 35, 33) if mode == 'dark' else (231, 223, 201)
            back = np.full(frame.shape, colour, np.float32)
        opacity = float(e.get('opacity', 1))
        a = rgba[..., 3:4] * opacity
        frame[:] = np.clip(back * (1 - a) + rgba[..., :3] * 255 * opacity + .5, 0, 255).astype(np.uint8)
    return draw, 'Original-pixel cutout with saved still background; polygon is fixed unless a reviewed moving matte is supplied'
