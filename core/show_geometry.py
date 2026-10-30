"""Shared non-destructive crop and scale controls for placed images and videos."""
import math
import numpy as np


def validate_crop(value):
    if not isinstance(value,(list,tuple)) or len(value)!=4:
        raise ValueError('Crop needs left, top, right and bottom margins')
    result=[float(v) for v in value]
    if not all(math.isfinite(v) and 0<=v<=.98 for v in result) or result[0]+result[2]>.98 or result[1]+result[3]>.98:
        raise ValueError('Leave at least two percent of the picture visible')
    return result


def crop_alpha(alpha, crop=None):
    """Mask the edges without stretching/repositioning the retained pixels."""
    if not crop:return alpha
    l,t,r,b=validate_crop(crop);h,w=alpha.shape[:2]
    result=alpha.copy()
    result[:round(t*h)]=0;result[round((1-b)*h):]=0
    result[:,:round(l*w)]=0;result[:,round((1-r)*w):]=0
    return result


def transform_pixels(pixels, crop=None, pan=None):
    """Pan content under a stationary crop window; works with premultiplied RGBA."""
    if pan and any(pan):
        import cv2
        h,w=pixels.shape[:2]
        pixels=cv2.warpAffine(pixels,np.array([[1,0,float(pan[0])*w],[0,1,float(pan[1])*h]],np.float32),(w,h))
    return crop_alpha(pixels,crop)


PLACEMENT_FIELDS = ("crop", "rotate", "stretch", "stretch_x", "person_scale", "person_offset")


def place_frame(frame, settings):
    """Crop/transform on a fixed output canvas; scale never allocates a larger frame."""
    import cv2
    h, w = frame.shape[:2]
    pixels = transform_pixels(frame, settings.get("crop"))
    scale = float(settings.get("person_scale", 1))
    ox, oy = settings.get("person_offset") or (0, 0)
    sx = scale * float(settings.get("stretch_x", 1))
    sy = scale * float(settings.get("stretch", 1))
    matrix = np.array([[sx, 0, w*(1-sx)/2+ox*w],
                       [0, sy, h*(1-scale)+h*(scale-sy)/2+oy*h]], np.float32)
    if settings.get("rotate"):
        rotation = cv2.getRotationMatrix2D((w*(.5+ox), h*(1-scale/2+oy)), -float(settings["rotate"]), 1.)
        matrix = (np.vstack([rotation, [0,0,1]]) @ np.vstack([matrix, [0,0,1]]))[:2].astype(np.float32)
    return cv2.warpAffine(pixels, matrix, (w, h))
