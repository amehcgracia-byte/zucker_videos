from pathlib import Path
import subprocess


def test_saving_paper_text_preserves_the_existing_rows_and_focus():
    s=Path('web/app.js').read_text()
    function=s[s.index('function updatePaperEdit('):s.index('function renderPaperEdit(')]
    program='''
const assert=require('node:assert/strict');let paperEditCuts=[];
const input={value:'still typing'},select={value:'keep'};
const row={dataset:{paperId:'a'},classList:{toggle(){}},querySelector(){return select;}};
const list={querySelectorAll(){return [row];},set innerHTML(value){throw Error('Rows rebuilt');}};
const summary={textContent:''};const document={activeElement:input,querySelector(id){return id==='#paperEditList'?list:summary;}};
'''+function+'''
updatePaperEdit({cuts:[{id:'a',mark:'drop',duration_sec:4},{id:'b',mark:'keep',duration_sec:3}]});
assert.equal(document.activeElement,input);assert.equal(input.value,'still typing');
assert.equal(select.value,'drop');assert.match(summary.textContent,/1 kept clips.*0 cuts.*3.0 s/);
'''
    subprocess.run(['node','-e',program],check=True,capture_output=True,text=True)
