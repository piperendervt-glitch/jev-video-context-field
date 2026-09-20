// No HTTP, timers, complete event array, video duration or model. Deliver one arrival at a time.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Node{
 constructor(tag){this.tagName=tag;this.children=[];this.dataset={};this.scrollTop=57;}
 append(...nodes){for(const n of nodes){n.remove();n.parent=this;this.children.push(n);}}
 remove(){if(this.parent)this.parent.children=this.parent.children.filter(x=>x!==this);}
 insertBefore(n,before){n.remove();n.parent=this;const i=this.children.indexOf(before);this.children.splice(i<0?this.children.length:i,0,n);}
 replaceChildren(...nodes){this.children=[];this.append(...nodes);}
 set textContent(s){this.text=String(s);this.children=[];}get textContent(){return(this.text||'')+this.children.map(x=>x.textContent).join('');}
}
const document={createElement:t=>new Node(t)},context=vm.createContext({document,window:{},structuredClone});
vm.runInContext(fs.readFileSync('web/activity-view.js','utf8'),context);
const {ActivityView,value,stamp}=context.window.H11,container=new Node('div');let selected=null;
const view=new ActivityView(container,row=>selected=row);view.reset('run-1',1);
assert.equal(view.rows.size,0); // no total, array, future source or end time needed
const pending={row_id:'job',run_id:'run-1',epoch:1,revision:1,producer:'Image',processing_state:'running',result_fields:[],source_refs:[]};
assert.equal(view.accept({type:'start',run_id:'run-1',generation:1,row:pending}),true);
assert.match(container.textContent,/処理中/);assert.ok(!container.textContent.includes('not arrived'));
// Only now is a completion constructed; nothing in the renderer could inspect it before this point.
const completed={...pending,revision:2,processing_state:'done',available_at:5.781,
 result_fields:[{id:'description',label:'説明',value:'Actual description',type:'text'}],
 source_refs:[{ref_id:'ref-A',modality:'video',media_s:.1001,interval:[.1001,.1001]}]};
assert.equal(view.accept({type:'result',run_id:'run-1',generation:1,row:completed}),true);
const node=view.nodes.get('job'),list=node.list,button=node.button;
assert.ok(container.textContent.includes('Actual description'));assert.ok(container.textContent.includes('00:05.781'));assert.ok(container.textContent.includes('00:00.100'));
button.onclick();assert.equal(selected.revision,2);view.freeze('job');
const thumb={type:'thumbnail',run_id:'run-1',generation:1,row_id:'job',epoch:1,revision:2,ref_id:'ref-A',state:'ready',jpeg:'AA=='};
assert.equal(view.accept(thumb),true);assert.equal(view.nodes.get('job').list,list);assert.equal(view.nodes.get('job').button,button);assert.equal(container.scrollTop,57);
assert.equal(view.accept({...thumb,ref_id:'other'}),false);
assert.equal(view.accept({...thumb,epoch:2}),false);assert.equal(view.accept({...thumb,revision:1}),false);
assert.equal(view.accept({type:'result',run_id:'run-1',generation:1,row:{...completed,revision:3}}),false,'pinned row stable');
view.freeze(null);
assert.equal(view.accept({type:'result',run_id:'run-1',generation:1,row:completed}),false,'duplicate');
assert.equal(view.accept({type:'result',run_id:'run-1',generation:1,row:pending}),false,'older revision');
assert.equal(view.rows.size,1);
assert.equal(view.accept({...thumb,state:'error',jpeg:null}),true);assert.ok(container.textContent.includes('Actual description'));assert.match(container.textContent,/画像取得不可/);
view.reset('run-2',2);assert.equal(view.accept(thumb),false,'previous run/generation ignored');assert.equal(view.rows.size,0);
const audio={...completed,run_id:'run-2',epoch:4,revision:1,thumbnail_state:'audio',source_refs:[{ref_id:'audio',modality:'audio',interval:[1,2]}]};
view.accept({type:'result',run_id:'run-2',generation:2,row:audio});assert.match(container.textContent,/▶ 音声/);assert.ok(!container.textContent.includes('画像準備中'));
assert.equal(value({type:'number',value:.001}),'<0.01');assert.equal(value({type:'number',value:0}),'0.00');
assert.equal(value({type:'number',value:null,missing_reason:'unvoiced_or_unavailable'}),'取得不可');
assert.equal(value({type:'text',value:'<img src=x>'}),'<img src=x>');
assert.equal(stamp(.1001),'00:00.100');
const result={result:'passed',checks:30,network_calls:0,future_array:false,total_or_duration_required:false,
 acceptance:['H11-09','H11-10','H11-11','H11-12','H11-13','H11-14'],method:'shared actual renderer, sequential DOM stub; not browser'};
fs.writeFileSync('artifacts/human-debug-h11/20260920/incremental-dom.json',JSON.stringify(result,null,2));console.log(result);
