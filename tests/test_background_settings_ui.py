from pathlib import Path
import shutil
import subprocess
import pytest


def test_settings_remain_usable_during_measured_background_progress():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for the frontend runtime regression')
    app = Path(__file__).parents[1]/'web/app.js'
    script = r'''
const vm = require('vm'), fs = require('fs'), assert = require('assert');
const source = fs.readFileSync(process.argv[1], 'utf8');
function section(start, end) { return source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start))); }
const nodes = new Map();
for (const id of ['settingsPreparation','settingsPreparationTitle','settingsPreparationDetail','settingsPreparationBar']) {
  nodes.set('#'+id, {hidden:true, textContent:'', replaceChildren(value) {this.bar=value;}});
}
let requests = 0, polls = 0, resolve;
const context = vm.createContext({window:{MeasuredProgress:{create:(percent,label)=>({percent,label})}},
 document:{querySelector:id=>nodes.get(id)},
 api:()=>{requests++; return new Promise(r=>resolve=r);},
 ensureStatusPolling:()=>{polls++;},
 logFrontendError:()=>{}, setStep:()=>{throw new Error('Settings were interrupted');},
});
vm.runInContext(section('const detected =','const LANDMARK_LABELS')+
 section('function sameProjectId','function stopStatusPolling')+
 section('function renderSettingsPreparation','function formatDuration')+
 section('function renderWizardStatus','function renderStatusStrip'), context);
vm.runInContext('activeProjectId="/project"; selectedPlatform="youtube"; currentStep=2; beginSettingsPreparation();', context);
assert.strictEqual(requests, 1);
assert.strictEqual(nodes.get('#settingsPreparationBar').bar.percent, null);
(async()=>{
 resolve({status:'running', project_path:'/project', stage:'ingest', stage_progress:37, detail:'Preparing Sony'});
 await vm.runInContext('backgroundPreparation',context);
 assert.strictEqual(polls,1);
 assert.strictEqual(vm.runInContext('currentStep',context),2);
 assert.strictEqual(nodes.get('#settingsPreparationBar').bar.percent,37);
 assert.strictEqual(nodes.get('#settingsPreparationDetail').textContent,'Preparing Sony');
 vm.runInContext('renderWizardStatus({status:"waiting_choice", stage_progress:100});',context);
 assert.strictEqual(nodes.get('#settingsPreparationBar').bar.percent,100);
 assert(nodes.get('#settingsPreparationTitle').textContent.includes('Videos prepared'));
 vm.runInContext('selectedPlatform="medley"; beginSettingsPreparation();',context);
 assert.strictEqual(requests,1); // no extra ingest/proxy pipeline for edited Medley videos
 assert.strictEqual(nodes.get('#settingsPreparation').hidden,true);
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    subprocess.run([node,'-e',script,str(app)],capture_output=True,text=True,check=True)
