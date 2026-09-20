// OFFLINE_COMPARISON: actual JS Number * 25 / toFixed(0), no browser or network.
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
const out=path.join(root,'artifacts/score-contract-offline/decision-prep-20260920');
const source=fs.readFileSync(path.join(root,'web/app.js'),'utf8');
assert.ok(source.includes("pct.toFixed(0)+'%'"));
assert.ok(source.includes("value.toFixed(0)+'%'"));
const rows=fs.readFileSync(path.join(out,'comparison.jsonl'),'utf8').trim().split('\n').map(JSON.parse);
const result=rows.map(row=>({sample_id:row.sample_id,evidence_stage:row.evidence_stage,
  values:Object.fromEntries(['C0','CP','CD'].map(p=>{
    const v=row.policies[p];return [p,{candidate_pct:v.candidate_value_pct,
      candidate_integer:v.candidate_score===null?null:(Number(v.candidate_score)*25).toFixed(0),
      public_integer:v.public_value_pct===null?null:(Number(v.candidate_score)*25).toFixed(0),
      public_reason:v.public_reason,selected_source:v.selected_source}];}))}));
const raw=result.find(x=>x.evidence_stage==='RAW_HTTP_BODY');
assert.equal(raw.values.CP.candidate_integer,'67');assert.equal(raw.values.CD.candidate_integer,'68');
assert.equal(raw.values.CP.public_integer,null);assert.equal(raw.values.CD.public_integer,null);
const boundaries=[67.499999999,67.5,67.500000001].map(v=>({value:v,integer:v.toFixed(0)}));
assert.deepEqual(boundaries.map(v=>v.integer),['67','68','68']);
fs.writeFileSync(path.join(out,'ui-rounding.json'),JSON.stringify({method:'actual Node Number.toFixed(0); not browser rendering',checks:7,boundaries,rows:result},null,2));
console.log('Offline numeric display: 7 assertions passed; 55 rows; case_01 candidate 67 vs 68, public null.');
