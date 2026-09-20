// Reuse the full H0/H1 save/ACK regression, then exercise the actual upper pane.
const fs=require('node:fs'),vm=require('node:vm');
let source=fs.readFileSync('scripts/check_h11_ui.cjs','utf8');
source=source.replace("const result={result:'passed',checks:40", `
 await H1.returnToRecord();
 const evalRow={...activityRow,row_id:'jev:test',pane:'evaluations',producer:'Jev・根拠評価',result_seq:11,
   original_fields:[{id:'source',label:'原文',value:'Saved original words',type:'text',target_ref:'r:9:0'}]};
 replies['evaluation-lists']=path=>({...replies.activity(path),logs:{items:[],total:0,offset:0},evaluations:{items:[evalRow],total:1,offset:0}});
 replies['activity-row']=evalRow;
 await H1.setPosition(6);
 assert.equal(nodes['h12-evaluations'].children.length,1);
 assert.equal(nodes['h1-rows'].children.length,0);
 await nodes['h12-evaluations'].children[0].onclick();
 assert.equal(H1.state().pinned.row_id,'jev:test');
 assert.match(nodes['h1-selected-text'].textContent,/元結果.*原文/);
 await nodes['h1-selected-text'].children[1].onclick();
 assert.match(nodes['h11-selected-item'].textContent,/元結果/);
 assert.equal(nodes['h1-review'].disabled,false);
 await H1.returnToRecord();
 const review=context.window.H0Review,oldGet=review.get,pending=[];
 review.get=(name,args)=>name==='evaluation-lists'?new Promise(resolve=>pending.push(resolve)):oldGet(name,args);
 const slow=H1.update(),fast=H1.update();
 const basePage={meta:replies.index.activity,logs:{items:[],total:0,offset:0},evaluations:{items:[],total:0,offset:0}};
 pending[1]({...basePage,cursor:99});await fast;
 pending[0]({...basePage,cursor:8});await slow;
 assert.equal(H1.state().page.cursor,99,'older HTTP reply cannot override newer window');
 assert.equal(nodes['h12-evaluations'].children.length,0);
 review.get=oldGet;
 const result={result:'passed',checks:48`);
vm.runInNewContext(source,{require,console,process,setTimeout,clearTimeout,performance,URL,URLSearchParams,Blob,structuredClone},{filename:'scripts/check_h12_ui.cjs'});
