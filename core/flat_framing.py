"""One static crop on ordinary cameras, shared by review and final renders."""
import math

def validate(spec):
    if not isinstance(spec,dict):raise ValueError('Choose a camera framing')
    result={k:float(spec.get(k,default)) for k,default in [('zoom',1),('x',.5),('y',.5)]}
    if not all(math.isfinite(v) for v in result.values()) or not 1<=result['zoom']<=3 or not 0<=result['x']<=1 or not 0<=result['y']<=1:raise ValueError('Keep zoom between 1 and 3 and the focus inside the image')
    return result

def filtergraph(spec):
    v=validate(spec);z=v['zoom'];x=v['x'];y=v['y']
    return f"crop=w='max(2,floor(iw/{z:.6f}/2)*2)':h='max(2,floor(ih/{z:.6f}/2)*2)':x='max(0,min(iw-ow,iw*{x:.6f}-ow/2))':y='max(0,min(ih-oh,ih*{y:.6f}-oh/2))'"
