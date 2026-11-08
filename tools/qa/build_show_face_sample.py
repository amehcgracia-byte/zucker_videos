"""Private, offline face anchors for masks in the fixed Show pilot."""
import argparse,hashlib,json,math,time
from pathlib import Path

def build(source,output,enhance=False):
 import cv2
 import mediapipe as mp
 cap=cv2.VideoCapture(str(source));fps=cap.get(cv2.CAP_PROP_FPS);w,h=int(cap.get(3)),int(cap.get(4));points=[];i=0;start=time.monotonic()
 with mp.solutions.face_mesh.FaceMesh(static_image_mode=False,max_num_faces=1,min_detection_confidence=.6,min_tracking_confidence=.6) as detector:
  while True:
   ok,bgr=cap.read()
   if not ok:break
   ox,oy,cw,ch=0,0,w,h
   analysis=bgr
   if enhance:
    ox,oy,cw,ch=int(w*.25),0,int(w*.65),int(h*.75)
    analysis=bgr[oy:oy+ch,ox:ox+cw]
    lab=cv2.cvtColor(analysis,cv2.COLOR_BGR2LAB);lab[:,:,0]=cv2.createCLAHE(2.5,(8,8)).apply(lab[:,:,0]);analysis=cv2.cvtColor(lab,cv2.COLOR_LAB2BGR)
    analysis=cv2.resize(analysis,None,fx=1.5,fy=1.5)
   result=detector.process(cv2.cvtColor(analysis,cv2.COLOR_BGR2RGB))
   if result.multi_face_landmarks:
    p=result.multi_face_landmarks[0].landmark
    if enhance:
     for point in p:point.x=(point.x*cw+ox)/w;point.y=(point.y*ch+oy)/h
    l,r=p[234],p[454];top,bottom=p[10],p[152]
    width=math.hypot((r.x-l.x)*w,(r.y-l.y)*h)
    angle=math.atan2((p[263].y-p[33].y)*h,(p[263].x-p[33].x)*w)
    mouth=math.hypot((p[14].x-p[13].x)*w,(p[14].y-p[13].y)*h)/max(1,width)
    points.append([i,round((l.x+r.x)*w/2,2),round((top.y+bottom.y)*h/2,2),round(width,2),round((bottom.y-top.y)*h,2),round(angle,4),round(min(1,mouth*8),3)])
   i+=1
   if i%250==0:print(f'{i} frames; {len(points)} face anchors',flush=True)
 cap.release()
 with source.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
 output.write_text(json.dumps(dict(version=1,engine='MediaPipe Face Mesh 0.10.21',analysis_enhanced=enhance,fps=fps,width=w,height=h,frames=i,detected_frames=len(points),source_sha256=digest,wall_sec=round(time.monotonic()-start,3),points=points)))
 print(f'{len(points)}/{i} face frames',flush=True)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('source',type=Path);p.add_argument('output',type=Path);p.add_argument('--enhance',action='store_true');a=p.parse_args();build(a.source,a.output,a.enhance)
