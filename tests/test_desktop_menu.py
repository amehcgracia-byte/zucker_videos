import json
from core.desktop_menu import MENU_ITEMS, build_desktop_menu


def test_native_menu_callbacks_dispatch_their_own_action():
    class Window:
        def __init__(self): self.scripts = []
        def evaluate_js(self, script): self.scripts.append(script)
    window = Window()
    menus = build_desktop_menu(window)
    import sys
    entries_for_platform = [(title, entries) for title, entries in MENU_ITEMS
                            if title != '__app__' or sys.platform == 'darwin']
    for menu, (title, entries) in zip(menus, entries_for_platform):
        assert menu.title == title
        for item, (label, action) in zip(menu.items, entries):
            assert item.title == label
            item.function()
            assert window.scripts[-1].endswith('run(' + json.dumps(action) + ')')
    assert any(label == 'Buscar actualizaciones…' for title, entries in MENU_ITEMS
               if title == 'Herramientas' for label, _ in entries)


def test_frontend_menu_guards_and_dispatch():
    from pathlib import Path
    import shutil
    import subprocess
    import pytest
    node = shutil.which('node')
    if not node: pytest.skip('Node unavailable')
    source = Path(__file__).parents[1] / 'web/app.js'
    script = r'''
const vm=require('vm'),fs=require('fs'),assert=require('assert');
const source=fs.readFileSync(process.argv[1],'utf8');
const clicks=[],errors=[];
let manual=false;
const nodes=new Map();
const node=id=>{if(!nodes.has(id))nodes.set(id,{hidden:false,disabled:false,click(){clicks.push(id)},closest(){return null},scrollIntoView(){}});return nodes.get(id)};
const context=vm.createContext({window:{},document:{querySelector:node,getElementById:node},
checkForUpdates:async value=>{manual=value},showToast:message=>errors.push(message),
newProject:async()=>clicks.push('newProject'),loadProjects:async()=>{},
activeProjectId:'/project',pendingImports:0,confirmingInputs:false,latestStatus:{status:'running'},latestResult:null});
vm.runInContext(source.slice(source.indexOf('window.EditorMenu = {')),context);
(async()=>{
 await context.window.EditorMenu.run('updates');assert(manual);
 await context.window.EditorMenu.run('render');assert(clicks.includes('renderReviewedTop'));
 node('renderReviewedTop').disabled=true;const count=clicks.filter(x=>x==='renderReviewedTop').length;
 await context.window.EditorMenu.run('render');assert.equal(clicks.filter(x=>x==='renderReviewedTop').length,count);
 await context.window.EditorMenu.run('mode');assert(errors.at(-1).includes('nuevo proyecto'));
 await context.window.EditorMenu.run('composition');assert(errors.at(-1).includes('Termina primero'));
 await context.window.EditorMenu.run('new');assert(clicks.includes('newProject'));
 await context.window.EditorMenu.run('not-a-command');assert(errors.at(-1).includes('desconocida'));
})().catch(e=>{console.error(e);process.exitCode=1});
'''
    subprocess.run([node, '-e', script, str(source)], check=True, capture_output=True, text=True)
