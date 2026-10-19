"""Object-relative placement shared by original-image and moving cutout layers."""
import math
import cv2
import numpy as np
from core.show_textures import ObjectTracker


def validate_anchor(value):
    if value is None:return None
    if not isinstance(value,(list,tuple)) or len(value)!=4:raise ValueError('Mark a rectangle around the object')
    x,y,w,h=map(float,value)
    if not all(math.isfinite(v) for v in (x,y,w,h)) or min(x,y)<0 or min(w,h)<=0 or x+w>1.001 or y+h>1.001:raise ValueError('Keep the tracking rectangle inside the video')
    return [x,y,w,h]


class Follow:
    def __init__(self,anchor,width,height,template=None,until=None):
        self.anchor=validate_anchor(anchor);self.width=width;self.height=height;self.unmatchable=False
        self.until=until;self.last=(0.,0.) if until==0 else None
        x,y,w,h=self.anchor
        self.start=(x+w/2,y+h/2)
        self.tracker=ObjectTracker([x*width,y*height,w*width,h*height])
        if template:
            image=cv2.imread(str(template),cv2.IMREAD_GRAYSCALE)
            if image is None:raise ValueError('The saved tracking template is missing')
            image=cv2.resize(image,(max(6,round(w*width)),max(6,round(h*height))))
            self.unmatchable=float(image.std())<4
            self.tracker.reference={'template':image,'scale':1.};self.tracker.visible=False
        self.bootstrap=bool(template)
    def update(self,clean,elapsed=0.):
        if self.until is not None and elapsed>=self.until:return self.last
        if self.unmatchable:return None
        if self.bootstrap and not self.tracker.visible:self.tracker.frame=3
        box=self.tracker.update(cv2.cvtColor(clean,cv2.COLOR_RGB2GRAY))
        if box is None:return None
        x,y,w,h=box
        self.last=((x+w/2)/self.width-self.start[0],(y+h/2)/self.height-self.start[1])
        return self.last


def colour(pixels,brightness=1.,saturation=1.,hue=0.):
    """Change only foreground RGB; premultiplied alpha remains independent."""
    result=pixels.astype(np.float32)/255
    grey=result.mean(axis=2,keepdims=True)
    result=np.clip((grey+(result-grey)*float(saturation))*float(brightness),0,1)
    if hue:
        hsv=cv2.cvtColor(result,cv2.COLOR_RGB2HSV);hsv[...,0]=(hsv[...,0]+float(hue))%360
        result=cv2.cvtColor(hsv,cv2.COLOR_HSV2RGB)
    return np.clip(result*255+.5,0,255).astype(np.uint8)
