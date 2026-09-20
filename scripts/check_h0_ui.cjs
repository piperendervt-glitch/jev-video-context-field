// DOM stub only: not browser playback, visual acceptance, or a human annotation.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes={},all=[],requests=[];
class Node{
 constructor(tag='div'){this.tagName=tag;this.children=[];this.dataset={};this.style={setProperty(){}};this.classList={add(){}};this.paused=true;this.hidden=false;this.value='';this.currentTime=0;this.duration=60;this.readyState=4;all.push(this);}
 set id(v){this._id=v;nodes[v]=this;}get id(){return this._id;}
 append(...values){for(const n of values){this.children.push(n);if(this.tagName==='select'&&this.children.length===1)this.value=n.value;}}
 before(){}replaceChildren(...n){this.children=n;}
 set textContent(v){this.text=String(v);this.children=[];}get textContent(){return(this.text||'')+this.children.map(n=>n.textContent||'').join('');}
 addEventListener(){}getContext(){return{clearRect(){},beginPath(){},moveTo(){},lineTo(){},stroke(){}};}
 requestVideoFrameCallback(){}scrollIntoView(){}load(){}pause(){this.paused=true;}async play(){this.paused=false;}
 querySelectorAll(selector){return all.filter(n=>n.dataset.evidence&&(!selector.endsWith(':checked')||n.checked));}
}
for(const m of fs.readFileSync('web/index.html','utf8').matchAll(/id="([^"]+)"/g))new Node().id=m[1];
const fields=new Node(),document={head:new Node(),body:new Node(),getElementById:id=>nodes[id],createElement:t=>new Node(t),createTextNode(t){const n=new Node();n.textContent=t;return n;},querySelector:s=>fields};
const fixture=JSON.parse(fs.readFileSync('artifacts/live-status-m3a/run-1789827209572239400-development-check.json','utf8')).before_stop;
const tr=fixture.transcripts.current[0],pin={review_id:'test-only-pin',target:{target_id:'fixture',event_cursor:10,record_version:'1'},context:{model_output_exposure:'shown_before_annotation',review_basis:'recorded_input'}};
const replies={runs:{items:[{id:'run-1',bytes:100}]},capabilities:[{feature_id:'asr_raw',status:'implemented'},{feature_id:'future_asr_correction',status:'planned'}],checks:{human_acceptance:'pending'},reviews:{items:[],issues:[]},index:{status:'ready',events:10},match:{media_match:'hash_verified'},display:{frame:fixture,cursor:10},targets:{items:[{key:'r:2:0',kind:'transcript',id:tr.segment_id,revision:tr.revision,seq:2,label:tr.text}],total:1},events:[],target:{key:'r:2:0',id:tr.segment_id,revision:tr.revision,epoch:2,source_refs:[{kind:'audio_interval',media_range:{start_s:0,end_s:1}}],links:{items:[]},body:tr},pin,save:{ack:true,record:{record_revision:1,record_id:'test-only-record'}},export:{records:0,jsonl:'',markdown:'fixture',sent:false},frame:{jpeg:'AA==',media_s:.3,pts:300,time_base:'1/1000'},timeline:{asr_inputs:[],states:[]},'managed-asset':{id:'fixture-asset',sha256:'b'.repeat(64),name:'fixture.mp4',duration_s:60}};
const context=vm.createContext({document,window:{addEventListener(){}},console,setTimeout,clearTimeout,performance,URL,URLSearchParams,Blob,crypto:require('node:crypto').webcrypto,structuredClone});
const source=fs.readFileSync('web/app.js','utf8');vm.runInContext(source.slice(0,source.lastIndexOf('(async()=>{const boot')),context);
context.fakeApi=async(path,body)=>{requests.push({path,body});const key=path.split('/').at(-1).split('?')[0];assert.ok(key in replies,`unexpected API ${path}`);return structuredClone(replies[key]);};
vm.runInContext('api=fakeApi;debugMode=true;replaying=true;stopped=true;',context);
(async()=>{
 await vm.runInContext(fs.readFileSync('web/debug.js','utf8'),context);
 assert.match(nodes['h0-capabilities'].textContent,/pending/);assert.match(nodes['h0-capabilities'].textContent,/planned/);
 assert.equal(nodes.video.src,undefined,'no synthetic video default');
 nodes['h0-run'].value='run-1';await nodes['h0-open-run'].onclick();
 assert.ok(nodes['transcript-current'].textContent.includes(tr.text));
 await nodes['h0-managed'].onclick();assert.equal(nodes.video.src,'/media/fixture-asset');
 await nodes['transcript-current'].children[0].onclick();
 await nodes['h0-pin-target'].onclick();
 nodes['h0-cursor'].value=9;await nodes['h0-load-cursor'].onclick();
 nodes['h0-reviewer'].value='TEST FIXTURE';nodes['h0-verdict'].value='consistent_with_evidence';nodes['h0-confirm'].checked=true;nodes['h0-evidence-transcript_read'].checked=true;
 await nodes['h0-save'].onclick();assert.match(nodes['h0-save-status'].textContent,/ACK.*revision 1/);
 const saved=requests.find(r=>r.path.includes('/save'));assert.equal(saved.body.review_id,'test-only-pin');
 assert.equal(saved.body.exposure.human_evidence_confirmation,true);
 nodes['h0-audio-start'].value=1;nodes['h0-audio-end'].value=2;await nodes['h0-repeat'].onclick();assert.equal(nodes.video.currentTime,1);assert.equal(nodes.video.paused,false);
 nodes.video.currentTime=2.1;nodes.video.ontimeupdate();assert.equal(nodes.video.currentTime,1);
 await nodes['h0-export'].onclick();assert.match(nodes['h0-export-status'].textContent,/未送信/);
 assert.ok(requests.every(r=>!/^\/api\/(clock|observe|audio|live|control|snapshot|session)/.test(r.path)));
 assert.ok(!source.includes('innerHTML'));assert.ok(!fs.readFileSync('web/debug.js','utf8').includes('innerHTML'));
 const result={result:'passed',checks:15,method:'DOM stub; not browser/human operation',api_calls:requests.length};
 fs.mkdirSync('artifacts/human-debug-h0/20260920',{recursive:true});fs.writeFileSync('artifacts/human-debug-h0/20260920/dom.json',JSON.stringify(result,null,2));console.log(result);
})().catch(e=>{console.error(e);process.exitCode=1;});
