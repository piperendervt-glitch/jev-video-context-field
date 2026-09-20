// Exercise production controller in the established DOM harness, no network.
const fs=require('node:fs'),vm=require('node:vm');
let source=fs.readFileSync('scripts/check_h12_ui.cjs','utf8');
source=source.replace("const result={result:'passed',checks:48", `
 replies['evaluation-lists']=path=>({...replies.activity(path),logs:{items:[],total:0,offset:0},evaluations:{items:[],total:0,offset:0},evaluation_progress:{state:'running',label:'normal',hard_error:false}});
 await H1.setPosition(6);
 assert.equal(nodes['cd-evaluation-progress'].hidden,true);
 assert.equal(nodes['cd-evaluation-progress'].textContent,'');
 replies['evaluation-lists']=path=>({...replies.activity(path),logs:{items:[],total:0,offset:0},evaluations:{items:[],total:0,offset:0},evaluation_progress:{state:'hard_error',label:'確率合計の不正で停止',hard_error:true,event_seq:19,reason:'probability_sum'}});
 await H1.setPosition(7);
 assert.equal(nodes['cd-evaluation-progress'].hidden,false);
 assert.equal(nodes['cd-evaluation-progress'].textContent,'確率合計の不正で停止');
 await H1.setPosition(0);
 const result={result:'passed',checks:52`);
vm.runInNewContext(source,{require,console,process,setTimeout,clearTimeout,performance,URL,URLSearchParams,Blob,structuredClone},{filename:'scripts/check_p1_ui.cjs'});
