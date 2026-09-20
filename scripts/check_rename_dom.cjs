const fs=require('node:fs'),vm=require('node:vm');
const out='artifacts/local-private/p1-checkpoint-rename-20260920-v1';
fs.mkdirSync(out,{recursive:true});process.env.JEV_TEST_ARTIFACT_DIR=out;
let source=fs.readFileSync('scripts/check_p1_incremental.cjs','utf8');
source=source.replace('artifacts/jev-parallel-p1/jev-parallel-p1-20260920-v1/incremental-dom.json',out+'/incremental-dom.json');
vm.runInNewContext(source,{require,console,process,structuredClone},{filename:'scripts/check_rename_dom.cjs'});
require('./check_p1_ui.cjs');
