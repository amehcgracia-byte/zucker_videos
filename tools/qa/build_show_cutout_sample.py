"""Propagate a reviewed pilot outline through a short shot; private assets stay local.

This is a bounded optical-flow mask sample, not general person segmentation.
"""
import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

# Reviewed outline at 21.32 s of the 1280x720 Show pilot.
OUTLINE = [(738,175),(749,143),(784,113),(817,99),(860,98),(909,119),(932,155),(938,207),(946,221),(943,265),(926,287),(912,317),(998,337),(1033,357),(1051,383),(1068,463),(1065,526),(1052,578),(1041,643),(1040,710),(1020,719),(649,719),(626,691),(615,612),(590,556),(558,546),(507,517),(464,526),(409,529),(376,518),(367,493),(373,465),(407,443),(486,408),(558,380),(622,351),(638,332),(683,326),(739,307),(746,282),(737,266),(734,235),(741,211)]


def build(source, output):
    output.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(source))
    fps = cap.get(cv2.CAP_PROP_FPS)
    start, count, w, h, cols = 533, 75, 640, 360, 10
    cap.set(cv2.CAP_PROP_POS_FRAMES, start)
    ok, first = cap.read()
    if not ok or abs(fps - 25) > .01:
        raise ValueError('Expected the reviewed 25 fps pilot')
    prev = cv2.cvtColor(cv2.resize(first, (w,h)), cv2.COLOR_BGR2GRAY)
    mask = np.zeros((h,w),np.uint8)
    cv2.fillPoly(mask,[np.array(OUTLINE, np.int32)//2],255)
    grid_x,grid_y = np.meshgrid(np.arange(w,dtype=np.float32),np.arange(h,dtype=np.float32))
    atlas = Image.new('RGBA',(w*cols,h*((count+cols-1)//cols)))
    frames=[]
    for index in range(count):
        if index:
            ok, frame = cap.read()
            if not ok: raise ValueError('Truncated source')
            gray = cv2.cvtColor(cv2.resize(frame,(w,h)),cv2.COLOR_BGR2GRAY)
            # Backward flow samples the previous matte in current coordinates.
            flow = cv2.calcOpticalFlowFarneback(gray,prev,None,.5,3,21,3,5,1.2,0)
            mask = cv2.remap(mask,grid_x+flow[:,:,0],grid_y+flow[:,:,1],cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT)
            prev=gray
        alpha=cv2.GaussianBlur(mask,(3,3),.5)
        rgba=np.full((h,w,4),255,np.uint8);rgba[:,:,3]=alpha
        atlas.paste(Image.fromarray(rgba),(index%cols*w,index//cols*h))
        frames.append(start+index)
    cap.release()
    atlas.save(output/'cutout-atlas.webp',lossless=True)
    (output/'cutout-sample.json').write_text(json.dumps({'start':start/fps,'end':(start+count)/fps,'fps':fps,'count':count,'width':w,'height':h,'columns':cols,'method':'Reviewed outline propagated with optical flow; inspect hands and hair','sourceFrames':frames},indent=2))
    print(f'Prepared {count} matte frames; {(output/"cutout-atlas.webp").stat().st_size} bytes')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('source',type=Path);p.add_argument('output',type=Path);a=p.parse_args();build(a.source,a.output)
