"""Right-click menus like the system's: submenus open beside their item and choosing runs
the item's action; the storyboard's menus offer everything to add, by kind."""
import subprocess
from pathlib import Path


def test_cascading_menu_opens_submenus_beside_and_runs_the_chosen_action():
    script = Path("web/show_menu.js").read_text()
    program = r'''
const assert = require('node:assert/strict');
class El {
  constructor(tag) { this.tag = tag; this.children = []; this.dataset = {}; this.style = {}; this.attrs = {}; this.listeners = {}; this.className = ''; this.parent = null; this._text = ''; this.disabled = false;
    this.classList = { set: new Set(), add: (c) => this.classList.set.add(c), remove: (c) => this.classList.set.delete(c), contains: (c) => this.classList.set.has(c) }; }
  set innerHTML(html) { this.children = []; for (const cls of ['zmenu-check', 'zmenu-label', 'zmenu-hint']) { const span = new El('span'); span.className = cls; this.append(span); } }
  set textContent(v) { this._text = v; } get textContent() { return this._text; }
  append(child) { child.parent = this; this.children.push(child); all.add(child); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); all.delete(this); }
  setAttribute(k, v) { this.attrs[k] = v; } addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  fire(type) { (this.listeners[type] || []).forEach((fn) => fn({ stopPropagation() {}, preventDefault() {} })); }
  querySelector(sel) { return this.find(sel)[0] || null; } querySelectorAll(sel) { return this.find(sel); }
  find(sel) { const out = []; const walk = (n) => n.children.forEach((c) => { if (sel.includes(c.className.split(' ')[0]) && !(sel.includes(':not(:disabled)') && c.disabled)) out.push(c); walk(c); }); walk(this); return out; }
  focus() {} closest() { return null; }
  getBoundingClientRect() { return { left: 100, right: 300, top: 50 * (Number(this.parent?.dataset.level || 0) + 1), bottom: 0 }; }
  get offsetWidth() { return 200; } get offsetHeight() { return 120; }
}
const all = new Set();
const body = new El('body');
global.document = { body, createElement: (t) => new El(t), querySelectorAll: (sel) => [...all].filter((e) => e.className === 'zmenu'),
  querySelector: () => null, addEventListener() {}, removeEventListener() {}, activeElement: null };
global.window = { addEventListener() {}, removeEventListener() {}, showToast() {} };
global.innerWidth = 1280; global.innerHeight = 800;
global.setTimeout = (fn) => fn();
''' + script + r'''
const chosen = [];
window.ShowMenu.open(40, 40, [{ label: 'Add', submenu: [{ label: 'Effects', submenu: [{ label: 'Wave', action: () => chosen.push('wave') }] }] }, { separator: true }, { label: 'Paste', disabled: true }]);
const menus = () => [...all].filter((e) => e.className === 'zmenu');
assert.equal(menus().length, 1);
const rows = (menu) => menu.children.filter((c) => c.className === 'zmenu-item');
rows(menus()[0])[0].fire('pointerenter');
assert.equal(menus().length, 2);
const sub = menus()[1];
assert.equal(sub.dataset.level, 1);
assert.equal(sub.style.left, '297px');            // beside its item, not inside it
rows(sub)[0].fire('pointerenter');
rows(menus()[2])[0].fire('click');
assert.deepEqual(chosen, ['wave']);
assert.equal(menus().length, 0);                   // choosing closes every level
'''
    subprocess.run(["node", "-e", program], check=True, capture_output=True, text=True)


def test_the_add_menu_offers_every_kind_of_thing_by_category():
    script = Path("web/show_review.js").read_text()
    block = script[script.index("  function addItems("):script.index("  // An effect's own tools")]
    for category in ("Pictures", "Drawings & words", "Characters", "Video", "Effects", "Bend the picture"):
        assert f'label: "{category}"' in block
    for key in ("library", "references", "cutout", "silhouette", "photo_edit", "ai", "drawing", "lettering", "word", "character", "original_eyes",
                "original_mouth", "original_part", "clay", "cutaway", "video_reference", "face_clip", "generate_video", "math", "steam", "warp_wave", "captions"):
        assert f'"{key}"' in block, key
