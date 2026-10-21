"""Pointer targets must coincide with the transformed, cropped media pixels."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_transformed_media_pointer_targets():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required for browser coordinate regression")
    script = r"""
const assert = require('node:assert/strict');
global.window = {};
require('./web/show_media_preview.js');
const {localPoint,hitTest} = window.ShowMediaPreview;
const box = {left:100,top:60,w:200,h:100};
// At 90 degrees the original left edge is the visible top edge.
let p = {rotate:90,crop:[0,0,0,0]};
assert.equal(hitTest(box,p,200,10).edge,'left');
assert.equal(hitTest(box,p,200,210).edge,'right');
assert.equal(hitTest(box,p,150,210).resize,true);
assert.equal(hitTest(box,p,105,65),null);
// Mirror plus unequal stretching: the left crop edge is on the right.
p = {rotate:0,flip:true,stretch_x:2,stretch:.5,crop:[.25,.1,.1,.2]};
assert.equal(hitTest(box,p,300,110).edge,'left');
assert.equal(hitTest(box,p,40,125).resize,true);
assert.equal(hitTest(box,p,390,110),null);
assert.equal(hitTest(box,p,200,140),null);
assert.deepEqual(localPoint(box,p,300,110),[50,50]);
// A free rotation must also resolve the actual rendered crop boundary.
p = {rotate:37,stretch_x:1.5,stretch:2,crop:[.2,0,0,0]};
const a=37*Math.PI/180;
const x=200-90*Math.cos(a),y=110-90*Math.sin(a);
assert.equal(hitTest(box,p,x,y).edge,'left');
"""
    subprocess.run([node, "-e", script], cwd=Path(__file__).resolve().parents[1], check=True)
