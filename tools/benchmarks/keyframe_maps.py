"""Experimental bounded-keyframe maps; opt-in until image comparisons are approved.

The exact pose sampler remains authoritative. Static poses reuse the same map;
fast moves, wide lenses and pole views always use the original projection.
"""
from __future__ import annotations

import math
import cv2
import numpy as np


class KeyframeMaps:
    def __init__(self, build, poses, source_size, *, interval=8, tolerance_deg=.001):
        self.build = build
        self.poses = poses
        self.sw, self.sh = source_size
        self.interval = interval
        self.tolerance = tolerance_deg
        self.exact_cache = {}
        self.block = None
        self.previous = None
        self.stats = dict(exact_maps=0, interpolated_maps=0, rejected_blocks=0,
                          max_midpoint_error_deg=0.)

    def exact(self, index):
        pose = self.poses[index]
        if pose not in self.exact_cache:
            self.exact_cache[pose] = self.build(pose)
            self.stats['exact_maps'] += 1
        return self.exact_cache[pose]

    def __call__(self, index):
        pose = self.poses[index]
        if self.previous is not None and self.previous[0] == pose:
            return self.previous[1]
        if self.block and self.block[0] < index < self.block[1]:
            start, end, first, last = self.block
            maps = self.blend(first, last, self.amount(start, end, index))
            self.stats['interpolated_maps'] += 1
        else:
            maps = self.exact(index)
            self.block = None
            end = min(len(self.poses) - 1, index + self.interval)
            window = self.poses[index:end + 1]
            # These conservative exclusions avoid pole/seam singularities and
            # the changing lens of a planet reveal. Include FOV speed as well.
            eligible = (end - index >= 4 and all(abs(p[1]) <= 55 and 55 <= p[2] <= 110 for p in window)
                        and all(max(abs(b[k] - a[k]) for k in range(3)) * 30 <= .75
                                for a, b in zip(window, window[1:])))
            if eligible:
                eligible = all(self.amount(index, end, i) is not None for i in range(index, end + 1))
            if eligible and window[0] != window[-1]:
                last = self.exact(end)
                # Align longitudes before blending: never blend across the
                # equirectangular seam through the opposite side of the stage.
                delta = last[0] - maps[0]
                aligned_x = last[0].copy()
                np.subtract(aligned_x, self.sw, out=aligned_x, where=delta > self.sw / 2)
                np.add(aligned_x, self.sw, out=aligned_x, where=delta < -self.sw / 2)
                last = aligned_x, last[1]
                middle = (index + end) // 2
                candidate = self.blend(maps, last, self.amount(index, end, middle))
                exact = self.exact(middle)
                dx = np.abs(candidate[0] - exact[0])
                dx = np.minimum(dx, self.sw - dx) * (360 / self.sw)
                dy = np.abs(candidate[1] - exact[1]) * (180 / self.sh)
                error = float(np.max(np.hypot(dx, dy)))
                self.stats['max_midpoint_error_deg'] = max(self.stats['max_midpoint_error_deg'], error)
                if math.isfinite(error) and error <= self.tolerance / 2:
                    self.block = index, end, maps, last
                else:
                    self.stats['rejected_blocks'] += 1
            # Bound memory regardless of shot duration; retain only endpoints.
            keep = {self.poses[index], self.poses[end]}
            self.exact_cache = {p: m for p, m in self.exact_cache.items() if p in keep}
        self.previous = pose, maps
        return maps

    def amount(self, start, end, index):
        first, last, current = self.poses[start], self.poses[end], self.poses[index]
        axis = max(range(3), key=lambda k: abs(last[k] - first[k]))
        delta = last[axis] - first[axis]
        if delta == 0:
            return 0. if current == first else None
        amount = (current[axis] - first[axis]) / delta
        if not 0 <= amount <= 1:
            return None
        if max(abs(first[k] + amount * (last[k] - first[k]) - current[k]) for k in range(3)) > self.tolerance / 4:
            return None
        return amount

    def blend(self, first, last, amount):
        mx = cv2.addWeighted(first[0], 1 - amount, last[0], amount, 0)
        my = cv2.addWeighted(first[1], 1 - amount, last[1], amount, 0)
        np.subtract(mx, self.sw, out=mx, where=mx >= self.sw)
        np.add(mx, self.sw, out=mx, where=mx < 0)
        return mx, my
