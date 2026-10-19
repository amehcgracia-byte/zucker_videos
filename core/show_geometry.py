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
