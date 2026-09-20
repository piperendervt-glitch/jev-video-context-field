// Sequential inputs to the actual two-pane renderer. Geometry is simulated,
// never reported as a real browser, video, listening, or semantic acceptance.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
let checks=0;function eq(a,b,message){assert.deepEqual(a,b,message);checks++;}function ok(a,message){assert.ok(a,message);checks++;}
class Node{
 constructor(tag){this.tagName=tag;this.children=[];this.dataset={};this.scrollTop=0;this.clientTop=0;this.h=100;}
 append(...nodes){for(const n of nodes){n.remove();n.parent=this;this.children.push(n);}}
 remove(){if(this.parent)this.parent.children=this.parent.children.filter(x=>x!==this);}
 insertBefore(n,before){n.remove();n.parent=this;const i=this.children.indexOf(before);this.children.splice(i<0?this.children.length:i,0,n);}
 replaceChildren(...nodes){this.children=[];this.append(...nodes);}
 set textContent(s){this.text=String(s);this.children=[];}get textContent(){return(this.text||'')+this.children.map(x=>x.textContent).join('');}
 get height(){return this.h+(this.images().length?20:0);}images(){return (this.tagName==='img'?[this]:[]).concat(...this.children.map(n=>n.images()));}
 getBoundingClientRect(){if(!this.dataset.row)return {top:0,bottom:300};const siblings=this.parent.children;const top=siblings.slice(0,siblings.indexOf(this)).reduce((sum,n)=>sum+n.height,0)-this.parent.scrollTop;return {top,bottom:top+this.height};}
}
const context=vm.createContext({document:{createElement:t=>new Node(t)},window:{},structuredClone});
vm.runInContext(fs.readFileSync('web/activity-view.js','utf8'),context);
const {EvaluationLists}=context.window.H11,logs=new Node('div'),upper=new Node('div'),scroll=[];
let selected=null;const view=new EvaluationLists(logs,upper,row=>selected=row,(pane,browsing)=>scroll.push({pane,browsing}));view.reset('run-1',1);
function send(row){return view.accept({run_id:'run-1',generation:1,row});}
function row(id,seq,pane='logs'){return {row_id:id,run_id:'run-1',epoch:1,revision:seq,result_seq:seq,event_seq:seq,pane,producer:pane==='logs'?'画像解析':'Jev・根拠評価',processing_state:'done',available_at:seq,
 result_fields:[{id:'description',label:'説明',value:'arrived '+id,type:'text',target_ref:'r:'+seq+':0'}],source_refs:[],evaluation:{complete:false}};}
function ids(pane){return [...pane.children].map(x=>x.dataset.row);}
function visible(pane){const n=pane.children.find(n=>n.getBoundingClientRect().bottom>0);return n?{id:n.dataset.row,offset:n.getBoundingClientRect().top}:null;}
function manual(pane,top){pane.scrollTop=top;pane.onscroll();}
eq(ids(logs),[]);eq(ids(upper),[]);
send({...row('A',1),processing_state:'running',result_fields:[]});ok(logs.textContent.includes('処理中'));ok(!logs.textContent.includes('arrived A'));
// Construct a result only after the pending assertion.
send({...row('A',2),source_refs:[{ref_id:'frame-A',modality:'video',media_s:99}]});send({...row('B',3),source_media_ranges:[[2,2]]});send({...row('C',4),source_media_ranges:[[1,1]]});
eq(ids(logs),['C','B','A']);eq(logs.scrollTop,0);eq(upper.scrollTop,0);
manual(logs,125);const logAnchor=visible(logs);send({...row('D',5),source_refs:[{ref_id:'frame-D',modality:'video',media_s:1}]});eq(visible(logs),logAnchor);eq(upper.scrollTop,0);
send(row('E1',6,'evaluations'));send(row('E2',7,'evaluations'));send(row('E3',8,'evaluations'));eq(ids(upper),['E3','E2','E1']);eq(visible(logs),logAnchor);
manual(upper,118);const evalAnchor=visible(upper);send(row('E4',9,'evaluations'));eq(visible(upper),evalAnchor);eq(visible(logs),logAnchor);
const lowerBefore=logs.scrollTop,upperBefore=upper.scrollTop;
const c=row('eval-B',10,'evaluations');
view.transaction({run_id:'run-1',generation:1,rows:[c],completions:[{row_id:'B',epoch:1,result_seq:3,evaluation_ids:['eval-B']}]});
ok(ids(logs).includes('B'),'visible anchor temporarily retained');ok(logs.textContent.includes('評価済み'));eq(visible(logs),logAnchor);eq(visible(upper),evalAnchor);
view.latest('logs');ok(!ids(logs).includes('B'));eq(logs.scrollTop,0);eq(visible(upper),evalAnchor);
manual(logs,110);const anchorBeforeImage=visible(logs),originalButton=view.logs.nodes.get('A').button;
const thumb={type:'thumbnail',run_id:'run-1',generation:1,row_id:'A',epoch:1,revision:2,ref_id:'frame-A',state:'ready',jpeg:'AA=='};
ok(view.accept(thumb));eq(visible(logs),anchorBeforeImage);eq(view.logs.nodes.get('A').button,originalButton);eq(visible(upper),evalAnchor);
ok(!view.accept({...thumb,epoch:2}));ok(!view.accept({...thumb,generation:0}));ok(!view.accept({...thumb,revision:1}));
ok(view.accept({...thumb,state:'error'}));eq(visible(logs),anchorBeforeImage);ok(view.accept(thumb));eq(visible(logs),anchorBeforeImage);
const above={...thumb,row_id:'D',revision:5,ref_id:'frame-D'};
ok(view.accept(above));eq(visible(logs),anchorBeforeImage,'image changes height ABOVE the visible row');eq(visible(upper),evalAnchor);
view.freeze('A');view.logs.nodes.get('A').button.onclick();eq(selected.row_id,'A');const pinned=structuredClone(selected),draft='unsaved human note';
view.transaction({run_id:'run-1',generation:1,rows:[row('eval-A',11,'evaluations')],completions:[{row_id:'A',epoch:1,result_seq:2,evaluation_ids:['eval-A']}]});
ok(ids(logs).includes('A'));eq(pinned.revision,2);eq(draft,'unsaved human note');view.freeze(null);view.latest('logs');ok(!ids(logs).includes('A'));
// Evaluation arrives before its source row at the client. Exact refs, no text join.
view.transaction({run_id:'run-1',generation:1,rows:[row('late-eval',13,'evaluations')],completions:[{row_id:'late-source',epoch:1,result_seq:12,evaluation_ids:['late-eval']}]});
ok(!ids(logs).includes('late-source'));send(row('same-text-other',12));ok(ids(logs).includes('same-text-other'));send(row('late-source',12));ok(!ids(logs).includes('late-source'));
send(row('late-source',14));ok(ids(logs).includes('late-source'),'new result revision not consumed by old completion');
send(row('eval-A',11,'evaluations'));eq(ids(upper).filter(x=>x==='eval-A').length,1);
const order=ids(logs);send({...row('late-source',14),state_cursor:30,evaluation_state:'一部評価済み'});eq(ids(logs),order);
view.reset('run-2',2);eq(ids(logs),[]);eq(ids(upper),[]);ok(!view.accept(thumb));ok(!send(row('old-run',40)));
// Saved-page snapshots use the same transaction boundary and retain a manual anchor.
view.reset('run-1',3);
for(let i=1;i<=5;i++)view.accept({run_id:'run-1',generation:3,row:row('s'+i,i)});
manual(logs,220);const savedAnchor=visible(logs);
view.transaction({run_id:'run-1',generation:3,rows:[row('s6',6),row('s5',5),row('s4',4)],snapshot:true});
eq(visible(logs),savedAnchor,'anchor excluded by a response window is retained');ok(ids(logs).includes(savedAnchor.id));
// All scroll input mechanisms reach onscroll. Only a matching application correction is ignored.
for(const name of ['wheel','scrollbar','touch','keyboard']){manual(logs,30);eq(view.anchors.logs.anchor.id,visible(logs).id,name);view.anchors.logs.restore();logs.onscroll();eq(view.anchors.logs.anchor.id,visible(logs).id,'automatic correction');}
manual(logs,0);eq(view.anchors.logs.anchor,null);send(row('stale-generation',99));ok(!ids(logs).includes('stale-generation'));
// A stream has the same finite DOM boundary as a saved window.
view.reset('run-1',4);
for(let i=0;i<85;i++)view.accept({run_id:'run-1',generation:4,row:row('bounded-'+i,100+i)});
eq(logs.children.length,60);eq(logs.children[0].dataset.row,'bounded-84');
manual(logs,135);const boundedAnchor=visible(logs);view.accept({run_id:'run-1',generation:4,row:row('new-above',200)});eq(visible(logs),boundedAnchor);eq(logs.children.length,60);
view.reset('run-1',5);
view.transaction({run_id:'run-1',generation:5,rows:[row('offpage-eval',1,'evaluations')],completions:[{row_id:'offpage-source',epoch:1,result_seq:1,evaluation_ids:['offpage-eval']}]});
for(let i=2;i<65;i++)view.accept({run_id:'run-1',generation:5,row:row('upper-'+i,i,'evaluations')});
eq(upper.children.length,60);ok(!view.evaluations.rows.has('offpage-eval'));
view.accept({run_id:'run-1',generation:5,row:row('offpage-source',1)});ok(!view.logs.rows.has('offpage-source'),'exact completion survives its upper card leaving the current page');
view.accept({run_id:'run-1',generation:5,row:{...row('offpage-source',1),state_cursor:100,evaluation_state:'一部評価済み',evaluation:{complete:false}}});
ok(view.logs.rows.has('offpage-source'),'later explicit target-set state is not hidden by an earlier completion');
const result={result:'passed',checks,network_calls:0,future_array:false,method:'actual EvaluationLists/ActivityView, simulated DOM geometry; NOT real browser',coverage:['H12-01','H12-02','H12-03','H12-04','H12-05','H12-06','H12-07','H12-08','H12-19','H12-21','H12-22']};
fs.writeFileSync('artifacts/human-debug-h12/20260920/incremental-dom.json',JSON.stringify(result,null,2));console.log(result);
