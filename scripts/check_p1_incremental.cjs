const fs=require('node:fs'),vm=require('node:vm');
let source=fs.readFileSync('scripts/check_h12_incremental.cjs','utf8');
source=source.replace("const result={result:'passed',checks", `
view.reset('run-1',6);
const p={...row('stable-unit',1,'evaluations'),evaluation_state:'評価待ち',processing_state:'running',state_cursor:1,result_fields:[]};
view.accept({run_id:'run-1',generation:6,row:p});
view.accept({run_id:'run-1',generation:6,row:{...row('other-unit',2,'evaluations'),state_cursor:2}});
eq(ids(upper),['other-unit','stable-unit']);
view.accept({run_id:'run-1',generation:6,row:{...p,evaluation_state:'応答待ち',state_cursor:3}});
eq(ids(upper),['other-unit','stable-unit']);ok(upper.textContent.includes('応答待ち'));
manual(upper,120);const pendingAnchor=visible(upper);
view.accept({run_id:'run-1',generation:6,row:{...row('stable-unit',4,'evaluations'),state_cursor:4,evaluation_state:'評価済み'}});
eq(visible(upper),pendingAnchor,'completion reordering preserves manual anchor');
const result={result:'passed',checks`);
source=source.replace('artifacts/human-debug-h12/20260920/incremental-dom.json','artifacts/jev-parallel-p1/jev-parallel-p1-20260920-v1/incremental-dom.json');
vm.runInNewContext(source,{require,console,process,structuredClone},{filename:'scripts/check_p1_incremental.cjs'});
