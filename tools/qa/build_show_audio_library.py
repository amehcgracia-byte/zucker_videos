"""Build deterministic, locally synthesized Show audition assets (no external samples)."""
import argparse, json, wave
from pathlib import Path
import numpy as np

TRACKS = [
 ('curioso','Curiosidad de cocina',112,[0,4,7,11,7,4,2,7],'marimba'),
 ('sigilo','Pasitos de sospecha',96,[0,2,3,7,6,3,2,-1],'pluck'),
 ('absurdo','Pequeño disparate',138,[0,7,2,9,4,11,5,2],'bell'),
 ('pensando','Haciendo cuentas',105,[0,3,7,10,7,5,2,3],'marimba'),
 ('torpeza','Algo salió regular',120,[7,4,2,0,5,2,-1,0],'pluck'),
 ('tranquilo','Café tranquilo',78,[0,4,7,9,7,4,2,0],'soft'),
 ('misterio','¿Qué hay ahí?',84,[0,1,7,3,0,6,3,1],'soft'),
 ('revelacion','Una pequeña revelación',100,[0,4,7,12,11,9,7,12],'bell'),
 ('prisa','Prisa diminuta',154,[0,2,4,7,9,7,4,2],'pluck'),
 ('despedida','Hasta la próxima',94,[7,9,7,4,5,2,4,0],'marimba'),
]
FX=[('pip','Pip de censura',1000,1000,.35),('muelle','Muelle descendente',800,100,.6),('campana','Campanita',1100,1100,.8),('subida','Idea que sube',220,1200,.6),('bajada','Idea que se desinfla',600,75,.8),('doble','Aviso doble',660,880,.5)]
def build(root):
 root.mkdir(parents=True,exist_ok=True); rate=24000; items=[]
 def write(key,title,data,kind):
  data=np.nan_to_num(data); peak=max(1,float(np.max(np.abs(data)))/.8); data=data/peak
  with wave.open(str(root/(key+'.wav')),'wb') as w:
   w.setparams((1,2,rate,0,'NONE','not compressed'));w.writeframes((data*32767).astype('<i2').tobytes())
  items.append(dict(id=key,name=title,file=key+'.wav',kind=kind,seconds=round(len(data)/rate,2),origin='Síntesis local; sin muestras externas'))
 for key,title,bpm,notes,timbre in TRACKS:
  beat=60/bpm; length=32*beat; out=np.zeros(int((length+1)*rate))
  for n in range(32):
   duration=min(beat*.85,.6);t=np.arange(int(duration*rate))/rate;f=261.6256*2**(notes[n%len(notes)]/12)
   envelope=np.minimum(1,t/.012)*np.exp(-t*(5 if timbre=='soft' else 11))
   sound=np.sin(2*np.pi*f*t)
   if timbre=='bell':sound+=.3*np.sin(2*np.pi*f*2.76*t)
   elif timbre=='pluck':sound+=.2*np.sin(2*np.pi*f*2*t)
   elif timbre=='marimba':sound+=.22*np.sin(2*np.pi*f*4*t)*np.exp(-t*20)
   start=int(n*beat*rate);out[start:start+len(t)]+=sound*envelope*.24
   if n%4==0:
    bt=np.arange(int(beat*2*rate))/rate; bass=np.sin(2*np.pi*130.8128*bt)*np.minimum(1,bt/.02)*np.exp(-bt*3)*.12
    out[start:start+len(bt)]+=bass[:len(out)-start]
  out[-rate:]*=np.linspace(1,0,rate);write(key,title,out,'music')
 for key,title,f1,f2,length in FX:
  t=np.arange(int(rate*length))/rate; phase=2*np.pi*(f1*t+(f2-f1)*t*t/(2*length));env=np.minimum(1,t/.008)*np.minimum(1,(length-t)/.025)
  if key!='pip':env*=np.exp(-t*4)
  if key=='doble':env*=((t<.16)|(t>.28))
  write(key,title,.25*np.sin(phase)*env,'effect')
 (root/'manifest.json').write_text(json.dumps(items,ensure_ascii=False,indent=2))
 cards=''.join(f'<article><h2>{i["name"]}</h2><p>{"Música" if i["kind"]=="music" else "Efecto"} · {i["seconds"]} s</p><audio controls preload="none" src="{i["file"]}"></audio><p><a download href="{i["file"]}">Guardar WAV</a></p></article>' for i in items)
 (root/'index.html').write_text('<!doctype html><meta charset="utf-8"><title>Biblioteca sonora · Show</title><style>body{background:#15221b;color:#edf2e9;font:17px system-ui;padding:30px}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:20px}article{padding:20px;background:#233a2c;border-radius:15px}a{color:#efd57e}audio{max-width:100%}</style><h1>Biblioteca sonora · Show</h1><p>10 músicas y 6 efectos sintetizados para pruebas. Sin grabaciones ni muestras externas. No son versiones de canciones conocidas.</p><a href="../storyboard.html?demo=1">Volver al Show</a><main>'+cards+'</main><script>document.addEventListener("play",e=>document.querySelectorAll("audio").forEach(a=>{if(a!==e.target)a.pause()}),true)</script>')
 return items
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('destination',type=Path);build(parser.parse_args().destination)
