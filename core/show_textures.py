"""Nature textures share one object tracker. No semantic cup detection is claimed.

The immutable marked-object template survives loss and shot changes. A missing
object hides the effect; a strong match can reacquire it anywhere, at a new scale.
Tracking must see the clean video, before backgrounds, mouths or textures.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any
import numpy as np

PRESETS = {'rainbow': 'Rainbow smoke', 'smoke': 'Smoke', 'fog': 'Mist',
           'water': 'Water', 'electricity': 'Electricity', 'fire': 'Fire',
           'snow': 'Snow', 'rain': 'Rain', 'sparks': 'Sparks', 'leaves': 'Leaves'}


@dataclass
class ObjectTracker:
    rect: list[float]
    reference: dict[str, Any] = field(default_factory=dict)
    scale: float = 1.0
    visible: bool = True
    frame: int = 0
    previous: np.ndarray | None = None
    previous_histogram: np.ndarray | None = None
    hits: int = 0
    misses: int = 0

    def update(self, grey: np.ndarray) -> list[float] | None:
        import cv2
        height, width = grey.shape
        thumb = cv2.resize(grey, (48, 27))
        histogram = cv2.calcHist([grey], [0], None, [32], [0, 256])
        cut = (self.previous is not None and float(np.mean(cv2.absdiff(thumb, self.previous))) > 28
               and cv2.compareHist(histogram, self.previous_histogram, cv2.HISTCMP_CORREL) < .9)
        self.previous = thumb
        self.previous_histogram = histogram
        self.frame += 1
        if 'template' not in self.reference:
            x, y, w, h = (int(round(v)) for v in self.rect)
            if x < 0 or y < 0 or w < 6 or h < 6 or x+w > width or y+h > height:
                self.visible = False
                return None
            template = grey[y:y+h, x:x+w].copy()
            if float(template.std()) < 4:  # a blank wall is not an identifiable object
                self.visible = False
                return None
            self.reference.update(template=template, scale=self.scale)
            self.visible = True
            return self.rect
        was_visible = self.visible
        if cut:
            self.visible = False
            self.hits = 0
        global_search = not self.visible
        # Bound the expensive whole-frame search; local tracking still runs each frame.
        if global_search and not cut and self.frame % 4:
            return None
        template = self.reference['template']
        hint = self.scale / self.reference['scale']
        current = self.rect[2] / template.shape[1]
        scales = sorted(set([current, hint] + [hint*k for k in (.5, .67, .8, 1.25, 1.5, 2, 3, 4)])) if global_search else [current, current*.97, current*1.03]
        best = (-1., None)
        for scale in scales:
            tw, th = max(6, round(template.shape[1]*scale)), max(6, round(template.shape[0]*scale))
            if tw > width or th > height:
                continue
            if global_search:
                left, top, right, bottom = 0, 0, width, height
            else:
                x, y, w, h = self.rect
                reach = max(12, round(width*.055))
                left, top = max(0, round(x)-reach), max(0, round(y)-reach)
                right, bottom = min(width, round(x+w)+reach), min(height, round(y+h)+reach)
            area = grey[top:bottom, left:right]
            if area.shape[0] < th or area.shape[1] < tw:
                continue
            match = cv2.resize(template, (tw, th), interpolation=cv2.INTER_LINEAR)
            ratio = min(1., 640 / width) if global_search else 1.
            if ratio < 1:
                area = cv2.resize(area, None, fx=ratio, fy=ratio, interpolation=cv2.INTER_AREA)
                match = cv2.resize(match, (max(3, round(tw*ratio)), max(3, round(th*ratio))), interpolation=cv2.INTER_AREA)
            scores = cv2.matchTemplate(area, match, cv2.TM_CCOEFF_NORMED)
            _, score, _, (mx, my) = cv2.minMaxLoc(scores)
            if math.isfinite(score) and score > best[0]:
                best = score, [float(left+mx/ratio), float(top+my/ratio), float(tw), float(th)]
        if best[1] is None or best[0] < (.82 if global_search else .72):
            self.misses += 1
            if was_visible and not cut and self.misses < 2:
                return self.rect  # one bad frame is not an appearance/disappearance
            self.visible = False
            self.hits = 0
            return None
        self.rect = best[1]
        self.misses = 0
        self.hits += 1
        # Two strong detections after a loss avoid flashing on one accidental match.
        self.visible = (was_visible and not cut) or self.hits >= 2
        return self.rect if self.visible else None


def draw_nature(frame: np.ndarray, texture: Any, t: float) -> None:
    """Deterministic animated texture presets, clipped and alpha-composited in RGB.

    These are procedural overlays, not photorealistic stock-footage simulations.
    The same position/density/scale/opacity controls apply to every preset.
    """
    import cv2
    height, width = frame.shape[:2]
    s = width / 960 * texture.scale
    fade = max(0., min(1., (t-texture.at)/.3, (texture.at+texture.duration-t)/.3))
    if not fade:
        return
    overlay = frame.copy()
    def colour(base):
        if texture.colour == 'white':
            return (245, 245, 245)
        if texture.colour.startswith('#'):
            return tuple(int(texture.colour[k:k+2], 16) for k in (1, 3, 5))
        return base
    rng = np.random.default_rng(407)
    kind = texture.texture
    for i in range(texture.puffs):
        phase, spread, speed = rng.random(3)
        age = (t*(.45+speed*.4)+phase) % 1
        x = texture.x + (spread-.5)*180*s
        y = texture.y + (age-.5)*180*s*texture.rise
        if kind in ('snow', 'rain'):
            ink = colour((225, 239, 255))
            if kind == 'snow':
                cv2.circle(overlay, (round(x+math.sin(t+i)*8*s), round(y)), max(1, round(2*s)), ink, -1, cv2.LINE_AA)
            else:
                cv2.line(overlay, (round(x), round(y)), (round(x-5*s), round(y+16*s)), ink, max(1, round(s)), cv2.LINE_AA)
        elif kind == 'water':
            radius = max(1, round((8+age*55)*s))
            cv2.ellipse(overlay, (round(texture.x), round(texture.y)), (radius, max(1, radius//3)), 0, 0, 360, colour((80, 180, 245)), max(1, round(s)), cv2.LINE_AA)
        elif kind == 'electricity':
            if i >= max(1, texture.puffs//10):
                continue
            points = [(round(texture.x+(rng.random()-.5)*28*s), round(texture.y))]
            for j in range(1, 9):
                points.append((round(texture.x+math.sin(t*19+i*7+j*3)*28*s), round(texture.y-j*16*s*texture.rise)))
            cv2.polylines(overlay, [np.array(points, np.int32)], False, colour((100, 170, 255)), max(1, round(5*s)), cv2.LINE_AA)
            cv2.polylines(overlay, [np.array(points, np.int32)], False, (235, 245, 255), max(1, round(s)), cv2.LINE_AA)
        elif kind in ('fire', 'sparks'):
            x = texture.x+(spread-.5)*(1-age)*70*s
            y = texture.y-age*150*s*texture.rise
            radius = max(1, round((1-age)*(12 if kind=='fire' else 3)*s))
            cv2.circle(overlay, (round(x), round(y)), radius, colour((255, round(70+170*(1-age)), 20)), -1, cv2.LINE_AA)
        elif kind == 'leaves':
            cv2.ellipse(overlay, (round(x+math.sin(t+i)*18*s), round(y)), (max(1, round(7*s)), max(1, round(3*s))), t*70+i*33, 0, 360, colour((160, 180, 65)), -1, cv2.LINE_AA)
    frame[:] = cv2.addWeighted(frame, 1-texture.strength*fade, overlay, texture.strength*fade, 0)
