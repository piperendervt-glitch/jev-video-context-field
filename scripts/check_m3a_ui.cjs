// DOM contract test, NOT browser automation or a visual/layout acceptance test.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Node {
 constructor(tag='div'){this.tagName=tag;this.children=[];this.dataset={};this.style={setProperty(){}};this.paused=true;this.hidden=false;}
 append(...nodes){this.children.push(...nodes);}
 replaceChildren(...nodes){this.children=nodes;}
 set textContent(value){this.text=String(value);this.children=[];}
 get textContent(){return (this.text||'')+this.children.map(n=>n.textContent||'').join('');}
 getContext(){return {clearRect(){},beginPath(){},moveTo(){},lineTo(){},stroke(){}};}
 requestVideoFrameCallback(){}
 scrollIntoView(){}
 addEventListener(){}
}
const html=fs.readFileSync('web/index.html','utf8');
const nodes=Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],new Node()]));
const document={getElementById(id){assert.ok(nodes[id],`missing #${id}`);return nodes[id];},createElement:tag=>new Node(tag),createTextNode(text){const n=new Node();n.textContent=text;return n;}};
const context=vm.createContext({document,window:{addEventListener(){}},console,setTimeout,clearTimeout,performance,URL});
const source=fs.readFileSync('web/app.js','utf8');
vm.runInContext(source.slice(0,source.lastIndexOf('(async()=>{const boot')),context);
let report=JSON.parse(fs.readFileSync('artifacts/live-status-m3a/run-1789827209572239400-development-check.json','utf8'));
context.payload=report.before_stop;vm.runInContext('render(payload)',context);
assert.match(nodes['transcript-current'].textContent,/transcript:2:3/);
assert.match(nodes['transcript-current'].textContent,/So I want to start by/);
vm.runInContext("inspectUnknown('conversation')",context);
assert.match(nodes.inspection.textContent,/UNKNOWNは未分類文章の割合ではありません/);
vm.runInContext('inspectTranscript(payload.transcripts.current.at(-1))',context);
assert.match(nodes.inspection.textContent,/local:2:TextContext/);
context.payload=structuredClone(report.before_stop);delete context.payload.transcripts;delete context.payload.profiles;delete context.payload.unknown_triage;
vm.runInContext('render(payload)',context);assert.match(nodes['transcript-state'].textContent,/LEGACY_MISSING/);
context.payload=structuredClone(report.before_stop);
const label='<img src=x onerror=alert(1)>';
context.payload.profiles=[{id:'topic-test',space:'conversation',revision:1,category_label:label,status:'registered_not_scheduled',runtime_verified:false}];
context.payload.readouts['topic-test']={items:{topic:null},status:'registered_not_scheduled'};
vm.runInContext('render(payload)',context);
assert.ok(nodes.panels.textContent.includes(label));assert.match(nodes.panels.textContent,/実Jev未確認/);
assert.match(nodes.panels.textContent,/registered_not_scheduled/);
const result={result:'passed',checks:8,method:'DOM stub unit test; browser and layout unverified'};
fs.writeFileSync(require('node:path').join(process.env.JEV_TEST_ARTIFACT_DIR||'artifacts/live-status-m3a','ui-contract.json'),JSON.stringify(result,null,2));console.log(result);
