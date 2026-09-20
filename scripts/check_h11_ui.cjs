// DOM stub only: not browser playback, visual acceptance, or a human annotation.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes={},all=[],requests=[];
class Node{
 constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.style={setProperty(){}};this.classList={add(){},toggle(){}};this.paused=true;this.hidden=false;this.value='';this.currentTime=0;this.duration=60;this.readyState=4;all.push(this);}
 set id(v){this._id=v;nodes[v]=this;}get id(){return this._id;}
 append(...values){for(const n of values){if(n.parentElement)n.parentElement.children=n.parentElement.children.filter(x=>x!==n);n.parentElement=this;this.children.push(n);if(this.tagName==='SELECT'&&this.children.length===1)this.value=n.value;}}
 remove(){if(this.parentElement)this.parentElement.children=this.parentElement.children.filter(n=>n!==this);}
 before(){}replaceChildren(...n){this.children=[];this.append(...n);}
 insertBefore(n,ref){this.append(n);this.children.splice(this.children.indexOf(n),1);this.children.splice(this.children.indexOf(ref),0,n);}
 setAttribute(k,v){this[k]=v;}showModal(){this.open=true;}close(){this.open=false;}
 querySelector(){return this.children.find(n=>['INPUT','SELECT','TEXTAREA'].includes(n.tagName));}
 set textContent(v){this.text=String(v);this.children=[];}get textContent(){return(this.text||'')+this.children.map(n=>n.textContent||'').join('');}
 addEventListener(name,fn){(this.listeners||={})[name]=fn;}getContext(){return{clearRect(){},beginPath(){},moveTo(){},lineTo(){},stroke(){}};}
 requestVideoFrameCallback(){}scrollIntoView(){}load(){}pause(){this.paused=true;}async play(){this.paused=false;}
 querySelectorAll(selector){return all.filter(n=>n.dataset.evidence&&(!selector.endsWith(':checked')||n.checked));}
}
for(const m of fs.readFileSync('web/index.html','utf8').matchAll(/id="([^"]+)"/g))new Node().id=m[1];
const fields=new Node(),oldMain=new Node('main');oldMain.append(fields,...Object.values(nodes));const document={head:new Node(),body:new Node(),getElementById:id=>nodes[id],createElement:t=>new Node(t),createTextNode(t){const n=new Node();n.textContent=t;return n;},querySelector:s=>s==='main'?oldMain:fields};
const fixture=JSON.parse(fs.readFileSync('artifacts/live-status-m3a/run-1789827209572239400-development-check.json','utf8')).before_stop;
const tr=fixture.transcripts.current[0],pin={review_id:'test-only-pin',target:{target_id:'fixture',event_cursor:10,record_version:'1'},context:{model_output_exposure:'shown_before_annotation',review_basis:'recorded_input'}};
const replies={runs:{items:[{id:'run-1',bytes:100}]},capabilities:[{feature_id:'asr_raw',status:'implemented'},{feature_id:'future_asr_correction',status:'planned'}],checks:{human_acceptance:'pending'},reviews:{items:[],issues:[]},index:{status:'ready',events:10},match:{media_match:'hash_verified'},display:{frame:fixture,cursor:10},targets:{items:[{key:'r:2:0',kind:'transcript',id:tr.segment_id,revision:tr.revision,seq:2,label:tr.text}],total:1},events:[],target:{key:'r:2:0',id:tr.segment_id,revision:tr.revision,epoch:2,source_refs:[{kind:'audio_interval',media_range:{start_s:0,end_s:1}}],links:{items:[]},body:tr},pin,save:{ack:true,record:{record_revision:1,record_id:'test-only-record'}},export:{records:0,jsonl:'',markdown:'fixture',sent:false},frame:{jpeg:'AA==',media_s:.3,pts:300,time_base:'1/1000'},timeline:{asr_inputs:[],states:[]},'managed-asset':{id:'fixture-asset',sha256:'b'.repeat(64),name:'fixture.mp4',duration_s:60}};
const context=vm.createContext({document,window:{addEventListener(){}},console,setTimeout,clearTimeout,performance,URL,URLSearchParams,Blob,crypto:require('node:crypto').webcrypto,structuredClone,requestAnimationFrame:()=>{}});
document.body.append(oldMain);
const source=fs.readFileSync('web/app.js','utf8');vm.runInContext(source.slice(0,source.lastIndexOf('(async()=>{const boot')),context);
context.fakeApi=async(path,body)=>{requests.push({path,body});const key=path.split('/').at(-1).split('?')[0];assert.ok(key in replies,`unexpected API ${path}`);return structuredClone(typeof replies[key]==='function'?replies[key](path,body):replies[key]);};
vm.runInContext('api=fakeApi;debugMode=true;replaying=true;stopped=true;',context);
const activityRow={run_id:'run-1',epoch:1,revision:10,processing_state:'done',available_at:5,result_fields:[{id:'text',label:'Original',type:'text',value:'Saved original words',target_ref:'r:9:0'}],source_refs:[],thumbnail_state:'audio',row_id:'e:2',producer:'音声認識',status:'done',text:'Saved original words',result_record_s:5,members:['e:2','r:9:0'],target_key:'r:9:0'};
replies.index={status:'ready',events:10,activity:{mode:'elapsed',duration:10}};
replies.activity=path=>{const position=Number(new URL('http://fixture'+path).searchParams.get('position'));return{meta:replies.index.activity,cursor:position>=5?10:1,total:position>=5?1:0,items:position>=5?[activityRow]:[],clock:{media:position>=5?1:0,state:position>=5?'paused':'playing',elapsed:position,epoch:1}};};
replies['evaluation-lists']=path=>{const old=replies.activity(path);return {...old,logs:{items:old.items.map(r=>({...r,pane:'logs',result_seq:10})),total:old.total,offset:0},evaluations:{items:[],total:0,offset:0}};};
replies['activity-row']=activityRow;
replies.target={...replies.target,body:{text:'Saved original words',media_start_s:.2,media_end_s:2.5},source_refs:[{kind:'audio_interval',media_range:{start_s:.2,end_s:2.5},missing_reasons:['segment_times_not_sample_exact']},{kind:'audio_interval',media_range:{start_s:0,end_s:1}},{kind:'audio_interval',media_range:{start_s:2,end_s:3}}]};
replies['evidence-frame']={jpeg:'AA==',media_s:.3};
(async()=>{
 await vm.runInContext(fs.readFileSync('web/debug.js','utf8'),context);
 await vm.runInContext(fs.readFileSync('web/activity-view.js','utf8'),context);
 await vm.runInContext(fs.readFileSync('web/activity.js','utf8'),context);
 const H1=context.window.H1Activity;assert.ok(H1,'H1 loaded');assert.equal(oldMain.hidden,true);
 assert.equal(nodes['h1-play'].disabled,true);assert.ok(!nodes['h1-rows'].textContent.includes('Saved original words'));
 assert.equal(nodes['h1-open'].textContent,'記録を開く');assert.equal(nodes['h1-choose-media'].hidden,true);
 nodes['h0-run'].value='run-1';await H1.prepare();
 assert.equal(nodes.video.src,'/media/fixture-asset');assert.equal(H1.state().position,0);assert.equal(nodes['h1-play'].disabled,false);
 assert.ok(requests.some(r=>r.path.includes('managed-asset')));assert.ok(requests.some(r=>r.path.includes('/match')));
 await nodes['h1-play'].onclick();assert.equal(H1.state().running,true);assert.equal(nodes.video.paused,false);
 await H1.setPosition(5);assert.equal(nodes['h1-rows'].children.length,1);assert.equal(nodes.video.paused,true,'historical pause while result arrives');
 await nodes['h1-rows'].children[0].onclick();
 assert.equal(H1.state().running,false);assert.equal(H1.state().pinned.cursor,10);assert.equal(nodes.video.currentTime,.2);
 assert.equal(nodes['h1-listen'].hidden,false);assert.equal(nodes['h1-review'].disabled,false);
 await nodes['h1-listen'].onclick();assert.equal(nodes.video.paused,false);
 nodes.video.currentTime=1.01;nodes.video.ontimeupdate();assert.equal(nodes.video.currentTime,2,'skip unreferenced audio gap');
 nodes.video.currentTime=2.51;nodes.video.ontimeupdate();assert.equal(nodes.video.currentTime,.2,'repeat only referenced segment');
 const selectedText=nodes['h1-selected-text'].textContent;await H1.update();assert.equal(nodes['h1-selected-text'].textContent,selectedText);
 await nodes['h1-review'].onclick();assert.equal(nodes['h1-review-dialog'].open,true);assert.match(nodes['h1-review-reference'].textContent,/test-only-pin/);
 nodes['h0-reviewer'].value='TEST FIXTURE';nodes['h0-verdict'].value='consistent_with_evidence';nodes['h0-confirm'].checked=true;nodes['h0-evidence-transcript_read'].checked=true;
 nodes['h0-note'].value='Unsaved human draft';nodes['h0-review'].listeners.input();
 await assert.rejects(()=>H1.returnToRecord());assert.equal(nodes['h0-note'].value,'Unsaved human draft');assert.equal(H1.state().pinned.cursor,10);
 await assert.rejects(()=>H1.prepare());assert.equal(nodes['h0-note'].value,'Unsaved human draft');
 await nodes['h1-save'].onclick();assert.equal(nodes['h1-review-dialog'].open,false);assert.match(nodes['h0-save-status'].textContent,/ACK.*revision 1/);
 assert.equal(requests.find(r=>r.path.includes('/save')).body.review_id,'test-only-pin');
 await H1.returnToRecord();assert.equal(H1.state().pinned,null);assert.equal(H1.state().position,5);assert.equal(nodes.video.currentTime,1);
 // A second row exercises the single-click frame path with no search or internal ID entry.
 replies.target={...replies.target,body:{description:'Saved frame'},source_refs:[{kind:'video_frame',media_range:{start_s:.3,end_s:.3}}]};
 await H1.selectRow(activityRow,10);assert.equal(nodes['h1-source-image'].hidden,false);assert.equal(nodes['h1-source-image'].src,'data:image/jpeg;base64,AA==');assert.equal(nodes.video.currentTime,.3);
 assert.equal(nodes['h0-confirm'].checked,false,'new pin clears human evidence claims');
 replies.reviews={items:[{pin,annotation:{record_revision:1,record_id:'test-only-record',reviewer:{label:'TEST FIXTURE'},judgment:{semantic_verdict:'unreviewed',review_status:'draft',numeric_check:{status:'unknown'},pipeline_check:{status:'unknown'},issue_tags:[]},view_context:{reviewed_evidence:[],human_evidence_confirmation:false}}}],issues:[]};
 await context.window.H0Review.reloadReviews();await nodes['h0-saved-list'].children[0].onclick();assert.equal(nodes['h1-review-dialog'].open,true,'saved review can reopen for revision');
 assert.ok(requests.every(r=>!/^\/api\/(clock|observe|audio|live|control|snapshot|session)/.test(r.path)&&!r.path.includes('start-local')));
 assert.ok(!fs.readFileSync('web/activity.js','utf8').includes('innerHTML'));
 const visibleText=document.body.children.filter(n=>n!==oldMain).map(n=>n.textContent).join(' ');
 assert.ok(!visibleText.includes('文字起こしではありません'));assert.ok(!visibleText.includes('future_asr_correction'));
 const result={result:'passed',checks:40,method:'DOM stub; NOT a real browser, listening or human judgment',api_calls:requests.length};
 fs.mkdirSync(process.env.JEV_TEST_ARTIFACT_DIR||'artifacts/human-debug-h11/20260920',{recursive:true});fs.writeFileSync((process.env.JEV_TEST_ARTIFACT_DIR||'artifacts/human-debug-h11/20260920')+'/dom.json',JSON.stringify(result,null,2));console.log(result);
})().catch(e=>{console.error(e);process.exitCode=1;});
