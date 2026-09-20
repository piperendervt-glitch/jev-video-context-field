'use strict';
const $ = id => document.getElementById(id);
const video = $('video');
const names = {person:'人物',place:'場所',conversation:'会話'};
const english = {person:'PERSON',place:'PLACE',conversation:'CONVERSATION'};
const colors = {person:'#b6a3ed',place:'#6dbbae',conversation:'#d8b37a'};
const labels = {casual:'日常会話',business:'ビジネス会話',angry:'怒って見える',smile:'笑顔に見える',day:'昼らしい',night:'夜らしい',twilight:'薄明',unknown:'不明'};
const statuses = {valid:'評価済み',pending:'未評価',stale:'期限切れ',superseded:'旧版失効',insufficient:'根拠不足',conflicting:'対立',disabled:'無効',error:'エラー'};
let token='', epoch=1, seq=0, latest=null, frameTime=0, busy=false, seeking=false, replaying=false, stopped=false, selected=null, lastCapture=-1, epochStart=0;
let audioContext=null, audioSamples=null, audioSampleRate=0, audioStamp=0;
let selectedAsset=null,analysisEnd=120,modelsReady=false;
let receipt=null;
let debugMode=false;
const panels = {};
function el(tag, cls, text){const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=text;return node;}
function clear(node){node.replaceChildren();}
async function api(path,body){
  if(debugMode&&path.startsWith('/api/evidence/'))path+=(path.includes('?')?'&':'?')+'run='+encodeURIComponent(window.h0Run||'');
  const response=await fetch(path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json','X-Session-Token':token},body:body===undefined?undefined:JSON.stringify(body)});
  if(!response.ok){const error=await response.json();throw Error(error.error||response.status);}
  return response.json();
}
function showError(error){$('error').hidden=false;$('error').textContent=String(error);}
function safe(task){return (...args)=>Promise.resolve(task(...args)).catch(showError);}
for(const space of Object.keys(names)){
  const field=el('article','field');field.style.setProperty('--accent',colors[space]);field.dataset.space=space;
  const head=el('div','field-top'),title=el('div','field-title '+space,names[space]);title.append(el('small','',english[space]));head.append(title,el('span','field-meta','独立した総量 1.0'));
  const concentration=el('div','concentration'),type=el('div','type-label','場の濃度');type.append(el('span','','UNKNOWN込みで合計100%'));
  const tiles=el('div','tile-list');concentration.append(type,tiles);
  const readout=el('div','readout'),dynamic=el('div','dynamic-readouts'),triage=el('div','triage-summary');field.append(head,concentration,readout,dynamic,triage);$('panels').append(field);panels[space]={field,tiles,readout,dynamic,triage};
}
function render(payload){
  if(!payload)return;latest=payload;epoch=payload.clock.epoch;
  if(!replaying)seq=Math.max(seq,payload.clock.seq??-1);
  $('session-id').textContent=payload.snapshot.session;
  $('mode').textContent=payload.evaluation_label||payload.mode;$('epoch').textContent=`epoch ${epoch} · ${payload.snapshot.shot}`;
  renderTranscripts(payload);
  if(payload.local_pipeline){
    const state=payload.local_pipeline;
    $('model-status').textContent=`モデル ${state.models.status} · ASR small / Qwen3-VL-2B共有 · 待機 ${state.pending.join(', ')||'なし'} · 期限切れ/破棄 ${state.dropped}`;
    if(state.errors.length)$('model-status').textContent+=' · '+state.errors.at(-1).error;
    const asr=payload.local_observations.SpeechASR,visual=payload.local_observations.FrameInterpreter;
    $('transcript').textContent=asr?`ASR ${asr.observed_s.map(t=>t.toFixed(1)).join('–')}秒: ${asr.text||'発話なし'}`:state.has_audio?'ASR: 公開済み音声を待機':'音声trackなし · 会話は未観測';
    $('visual-description').textContent=visual?`画像 ${visual.media_s.toFixed(1)}秒: ${visual.description}`:'画像: 公開済みframeを待機';
    if(payload.analysis_status==='range_complete'){video.pause();stopped=true;$('upload-status').textContent='解析区間が終了しました。次の区間は自動で開始しません。';}
  }
  $('play-state').textContent=replaying?'記録再生':payload.clock.state==='playing'?'再生中':stopped?'停止':'一時停止';
  $('play').textContent=video.paused?'▶ 再生':'Ⅱ 一時停止';
  $('time').textContent=`${payload.clock.media_s.toFixed(1)} / ${(video.duration||32).toFixed(1)} s`;
  if(!seeking)$('seek').value=payload.clock.media_s;
  for(const space of Object.keys(names)){
    const data=payload.snapshot.spaces[space],ui=panels[space];clear(ui.tiles);
    const visible=data.hypotheses.slice(0,12);
    for(const row of visible){
      const tile=el('button','tile'+(row.conflict?' conflict':''),row.label+(row.conflict?' · 対立':''));
      tile.dataset.hypothesis=row.id;tile.append(el('b','',(100*row.C).toFixed(0)+'%'));
      tile.title=`支持 ${row.S.toFixed(4)} / 反証 ${row.R.toFixed(4)}。場の質量の割合であり正解率ではありません。`;
      tile.style.backgroundColor=`color-mix(in srgb, ${colors[space]} ${Math.round(8+row.C*45)}%, #172029)`;
      tile.onclick=safe(()=>inspect(row));ui.tiles.append(tile);
    }
    if(data.hypotheses.length>12){const other=el('div','tile','その他');other.append(el('b','',(100*data.hypotheses.slice(12).reduce((n,r)=>n+r.C,0)).toFixed(0)+'%'));ui.tiles.append(other);}
    const unknown=el('button','tile unknown','UNKNOWNを調べる');unknown.append(el('b','',(data.UNKNOWN*100).toFixed(0)+'%'));unknown.onclick=()=>inspectUnknown(space);ui.tiles.append(unknown);
    clear(ui.triage);const flags=payload.unknown_triage?.spaces[space]?.flags;
    ui.triage.append(el('p','readout-details',flags?flags.map(f=>f.reason_code).join(' · ')||'機械診断で追加flagなし':'LEGACY_MISSING · この記録には原因診断がありません'));
    clear(ui.dynamic);
    for(const profile of (payload.profiles||[]).filter(p=>p.space===space)){
      const value=payload.readouts[profile.id],box=el('section','topic-readout');
      box.append(el('h3','',`話題 · ${profile.category_label}`),el('div','readout-details',`${profile.id} / v${profile.revision} · ${profile.runtime_verified?'実Jev応答検証済み':'実Jev未確認'} · ${profile.status}`));
      const pct=value?.items?.topic;
      box.append(el('p','',`独立Score · 非排他: ${pct==null?'—':pct.toFixed(0)+'%'} · ${statuses[value?.status]||value?.status||'未評価'}`));
      if(value?.as_of_media_s!==undefined)box.append(el('p','readout-details',`${value.as_of_media_s.toFixed(2)}秒の場 / 古さ ${(value.age_s||0).toFixed(2)}秒 · ${value.input_snapshot_id}`));
      if(value?.original_reason||value?.reason)box.append(el('p','readout-details',value.original_reason||value.reason));
      const more=el('details');more.append(el('summary','','定義・版・依存・評価を確認'),el('pre','',JSON.stringify({profile,readout:value},null,2)));box.append(more);ui.dynamic.append(box);
    }
    const r=payload.readouts[space];clear(ui.readout);
    const rh=el('div','readout-head');rh.append(el('span','','Jev読取り · '+(space==='person'?'選択人物の表情':space==='place'?'画面の時間帯':'会話の様式')),
      el('span','kind',space==='place'?'Choice · 排他':'Score · 非排他'),el('span','status '+r.status,statuses[r.status]||r.status));ui.readout.append(rh);
    for(const [key,value] of Object.entries(r.items)){
      const row=el('div','score-row'),track=el('div','track'),fill=el('div','fill');fill.style.width=(value===null?0:value)+'%';track.append(fill);
      row.append(el('span','',labels[key]||key),track,el('span','score-value',value===null?'—':value.toFixed(0)+'%'));ui.readout.append(row);
    }
    const detail=r.as_of_media_s===undefined?`未評価 · ${r.reason||'観測待ち'}`:`${payload.mode==='REPLAY'?'記録':r.mode||payload.mode} · ${r.as_of_media_s.toFixed(1)}秒の場 · 古さ ${(r.age_s||0).toFixed(1)}秒 · ${r.subject} · ${r.input_snapshot_id}`;
    ui.readout.append(el('div','readout-details',detail));
    if(r.original_reason||r.reason)ui.readout.append(el('div','readout-details',`採否理由: ${r.original_reason||r.reason}`));
    ui.readout.title=space==='place'?'候補と不明を含む同一Choiceの分布。未評価は分布を作りません。':'各項目の0〜4段階を0〜100に変換。合計100%へ正規化しません。';
  }
  clear($('adapters'));
  for(const [name,adapter] of Object.entries(payload.adapters)){
    const row=el('div','adapter');row.append(el('span','',name),el('span',adapter.enabled?'':'disabled',adapter.enabled?'LOCAL · 計測可能':'無効 · '+adapter.reason));$('adapters').append(row);
  }
  if(Object.keys(payload.local_observations).length)$('measurements').textContent=JSON.stringify(payload.local_observations,null,2);
  $('queue-count').textContent='待機 '+Object.values(payload.queues).reduce((a,b)=>a+b,0);
  if(payload.evaluation_status?.stopped&&payload.status!=='stopped'){
    const last=payload.evaluation_status.last_events.findLast(e=>e.status==='error'||e.status==='budget_stop');
    $('queue-count').textContent+=' · Jev送信停止: '+(last?.reason||'停止ログを確認');
  }
  clear($('stats'));
  const stats={'実API送信':payload.external.attempts+' / 240','実評価unit':payload.external.units+' / 720','実usage':payload.external.usage?JSON.stringify(payload.external.usage):'未取得','MOCK送信相当':payload.mock.attempts,'MOCK unit':payload.mock.units,'追加確認完了':payload.feedback.completed+'（'+(payload.media?.evaluation_mode||'MOCK')+'）','場の規則':'evidence-mass-v1.1'};
  for(const [key,value] of Object.entries(stats))$('stats').append(el('dt','',key),el('dd','',String(value)));
  clear($('events'));for(const event of payload.events.slice().reverse()){
    const row=el('div');row.append(el('span','',String(event.event_seq).padStart(4,'0')),document.createTextNode(event.kind));$('events').append(row);
  }
  drawHistory(payload.history);
}
function renderTranscripts(payload){
  const store=payload.transcripts;clear($('transcript-current'));clear($('transcript-history'));
  $('transcript-state').textContent=store?`${store.state}${store.reason?' · '+store.reason:''} · epoch ${payload.clock.epoch}`:'LEGACY_MISSING · 旧ログには版付き原文がありません。下の旧ASR表示は記録された範囲のみです。';
  function entry(r){const button=el('button','transcript-entry');button.append(el('small','',`${r.media_start_s.toFixed(2)}–${r.media_end_s.toFixed(2)}秒 · ${r.segment_id} / r${r.revision} · ${r.status}`),el('span','',r.text||'発話なし／取消し'));
    button.onclick=()=>inspectTranscript(r);return button;}
  for(const r of store?.current||[])$('transcript-current').append(entry(r));
  for(const r of store?.history||[])$('transcript-history').append(entry(r));
  const interpretation=payload.local_observations?.TextContext;
  $('text-interpretation').textContent=interpretation?JSON.stringify({topic:interpretation.topic,register:interpretation.register,quote:interpretation.quote,asr_id:interpretation.asr_id},null,2):'文章解析を待機 · 原文の上書きはしません';
}
function mappedEntry(item,box){
  box.append(el('p','chain',`${item.observation_id} → ${item.hypothesis_ids.join(', ')||'候補なし'} → ${item.evaluation_ids.join(', ')||'評価待ち'} → ${item.contribution_ids.join(', ')||'寄与なし'} → ${item.readout_ids.join(', ')||'readoutなし'}`),el('p','',item.text),el('p','readout-details',`${item.processing_state} · ${item.semantic_status}`));
  for(const hid of item.hypothesis_ids){const row=latest.snapshot.spaces[item.space].hypotheses.find(h=>h.id===hid);if(row){const b=el('button','',`場の根拠: ${row.label}`);b.onclick=safe(()=>replaying?showRecordedEvidence(item,box):inspect(row));box.append(b);}}
  const d=el('details');d.append(el('summary','','出典・時刻・対応ID'),el('pre','',JSON.stringify(item,null,2)));box.append(d);
}
function showRecordedEvidence(item,box){box.append(el('pre','',JSON.stringify(item,null,2)));}
function inspectTranscript(segment){
  const box=$('inspection');clear(box);box.append(el('h3','',`${segment.segment_id} / r${segment.revision}`),el('p','',segment.text));
  const rows=latest.unknown_triage?.unmapped_observation_index.filter(r=>r.transcript_refs.some(t=>t.segment_id===segment.segment_id&&t.revision===segment.revision))||[];
  if(!rows.length)box.append(el('p','','この版に対応する候補はまだありません。'));
  for(const row of rows)mappedEntry(row,box);
  const detail=el('details');detail.append(el('summary','','原文・sample参照・旧版'),el('pre','',JSON.stringify(segment,null,2)));box.append(detail);box.scrollIntoView({block:'nearest'});
}
function inspectUnknown(space){
  const box=$('inspection');clear(box);const triage=latest.unknown_triage;
  box.append(el('h3','',`${names[space]} · UNKNOWNの処理状況`),el('p','',triage?.meaning||'LEGACY_MISSING · 当時の診断記録なし'));
  for(const f of triage?.spaces[space]?.flags||[])box.append(el('h4','',`${f.reason_code} · ${f.basis}`),el('p','',f.explanation),el('p','',f.next_action),el('pre','',JSON.stringify(f.refs,null,2)));
  box.append(el('p','','新しい意味分類の判断はM3b未着手です。'));
  for(const row of triage?.unmapped_observation_index.filter(r=>r.space===space)||[])mappedEntry(row,box);
  box.scrollIntoView({block:'nearest'});
}
function drawHistory(history){
  const canvas=$('history'),ctx=canvas.getContext('2d'),w=canvas.width,h=canvas.height;ctx.clearRect(0,0,w,h);
  ctx.strokeStyle='#25343c';ctx.lineWidth=1;
  for(let y=10;y<h;y+=35){ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(w,y);ctx.stroke();}
  const points=history.filter(p=>p.epoch===epoch),max=Math.max(32,...points.map(p=>p.media_s));
  for(const space of Object.keys(names)){ctx.strokeStyle=colors[space];ctx.lineWidth=2;ctx.beginPath();points.forEach((p,i)=>{const x=p.media_s/max*w,y=10+(1-p.unknown[space])*(h-20);i?ctx.lineTo(x,y):ctx.moveTo(x,y);});ctx.stroke();}
}
async function inspect(row){
  selected=row.id;const box=$('inspection');clear(box);box.append(el('div','inspect-title',row.label+' · '+row.subject));
  const grid=el('div','inspect-grid');for(const [key,value] of Object.entries({支持:row.S,反証:row.R,正味:row.E,濃度:row.C})){grid.append(el('span','',key),el('b','',value.toFixed(6)));}box.append(grid);
  const sources=[...row.sources.support,...row.sources.contradict];
  const replayQuery=replaying&&latest.replay_event_cursor?'?replay_cursor='+latest.replay_event_cursor:'';
  for(const source of sources.slice(0,4)){
    const record=await api('/api/evidence/'+encodeURIComponent(source.evaluation_id)+replayQuery);
    const obs=await api('/api/evidence/'+encodeURIComponent(source.observation_id)+replayQuery);
    box.append(el('p','chain',`${source.root} → ${obs.agent} → ${record.id} → contribution → ${latest.snapshot.snapshot_id}`));
    box.append(el('p','',obs.text),el('p','readout-details',`basis: ${obs.basis} · 引用 ${source.tau.toFixed(2)}秒 · root配分 ${source.d.toFixed(4)} → 減衰後 ${source.decayed.toFixed(4)}`));
    const details=el('details'),summary=el('summary','','観測・生応答・版を表示');details.append(summary,el('pre','',JSON.stringify({observation:obs,evaluation:record},null,2)));box.append(details);
  }
}
function presented(_now,meta){frameTime=meta.mediaTime;video.requestVideoFrameCallback(presented);}
if(video.requestVideoFrameCallback)video.requestVideoFrameCallback(presented);
else showError('このブラウザーは表示frame時刻を取得できません。対応ブラウザーが必要です。');
const captureCanvas=document.createElement('canvas');captureCanvas.width=320;captureCanvas.height=180;
async function capture(){
  if(latest?.mode==='LOCAL'||video.paused||video.readyState<2||frameTime-lastCapture<.1||seeking||replaying||stopped)return;
  if(!latest.clock.released.video.some(([a,b])=>a<=frameTime&&frameTime<=b))return;
  lastCapture=frameTime;captureCanvas.getContext('2d').drawImage(video,0,0,320,180);
  const jpeg=captureCanvas.toDataURL('image/jpeg',.6).split(',')[1];
  await api('/api/observe',{epoch,media_s:frameTime,jpeg});
}
async function tick(){
  if(latest?.mode==='LOCAL'&&video.readyState<2)return;
  if(busy||seeking||replaying||!token||stopped)return;busy=true;
  try{
    const current=Math.min(video.currentTime,analysisEnd);
    const packet=audioSamples?{samples:audioSamples,stamp:audioStamp,rate:audioSampleRate}:null;
    audioSamples=null;
    const message={epoch,seq:++seq,media_s:current,video_presented_s:Math.min(frameTime,current),state:video.ended?'ended':video.paused?'paused':'playing',rate:video.playbackRate,
      audio_presented_s:latest?.mode==='MOCK'||latest?.media?.has_audio?current:null};
    if(receipt)message.viewer_receipt=receipt;
    const sent=performance.now(),payload=await api('/api/clock',message),received=performance.now();render(payload);
    receipt=payload.display_event_cursor?{display_event_cursor:payload.display_event_cursor,roundtrip_ms:received-sent,render_ms:performance.now()-received}:null;
    await capture();
    if(packet&&!video.paused){const end=packet.stamp,start=end-packet.samples.length/packet.rate;
      if(start>=epochStart&&latest.clock.released.audio.some(([a,b])=>a<=start&&end<=b))await api('/api/audio',{epoch,start_s:start,end_s:end,samples:packet.samples,sample_rate:packet.rate});}
  }catch(error){showError(error);}finally{busy=false;}
}
$('play').onclick=safe(async()=>{if(replaying)return;if(latest?.mode==='LOCAL'&&!modelsReady)throw Error('モデルの準備完了を待ってください。');if(stopped){if(latest.mode==='LOCAL'){await prepareLocal();}else{await openMode('MOCK');}}if(video.paused){await video.play();}else{video.pause();}await tick();});
$('step').onclick=safe(async()=>{if(replaying||stopped)return;while(busy)await new Promise(r=>setTimeout(r,10));await video.play();await tick();await new Promise(resolve=>setTimeout(resolve,100));video.pause();while(busy)await new Promise(r=>setTimeout(r,10));await tick();});
$('stop').onclick=safe(async()=>{video.pause();stopped=true;render(await api('/api/control',{epoch,action:'stop'}));});
$('seek').onchange=safe(async()=>{if(replaying)return;video.pause();seeking=true;while(busy)await new Promise(r=>setTimeout(r,20));receipt=null;const target=Number($('seek').value);render(await api('/api/control',{epoch,action:'seek',target}));stopped=false;seq=0;frameTime=target;lastCapture=-1;epochStart=target;video.currentTime=target;await new Promise(resolve=>video.addEventListener('seeked',resolve,{once:true}));seeking=false;});
video.onloadedmetadata=()=>{$('seek').max=Math.min(video.duration,analysisEnd);$('play').disabled=false;if(selectedAsset&&latest?.mode==='LOCAL')$('asset-info').textContent=`${selectedAsset.name} · browser再生準備OK / backend decode OK · 音声 ${selectedAsset.has_audio?'あり':'なし'} · ${selectedAsset.duration_s.toFixed(1)}秒`;};
video.onerror=()=>showError('動画を再生できません。WebMまたはブラウザー対応MP4を確認してください。');
video.onratechange=()=>{if(video.playbackRate!==1){video.playbackRate=1;showError('初期版は1倍速です。');}};
async function openMode(mode){
  receipt=null;
  video.pause();seeking=true;while(busy)await new Promise(r=>setTimeout(r,20));replaying=false;stopped=false;seq=0;epochStart=0;frameTime=0;lastCapture=-1;
  analysisEnd=120;$('seek').min=0;
  render(await api('/api/session',{mode}));video.src=mode==='LOCAL'?'/media/local':'/media/fixture';video.load();$('replay-box').hidden=true;
  $('mode-note').textContent=mode==='LOCAL'?'指定した実動画の画素と音声をlocalhostで計測します。ASR・VLM・文章解釈・実Jevは無効のため、意味の場とreadoutは未評価です。元containerのPTS検証は未対応です。':'映像と意味評価は固定fixtureです。Jevの実評価ではありません。20秒以降は新しい証拠がなく、蒸発と評価の失効を確認できます。';
  $('video-label').textContent=mode==='LOCAL'?'LOCAL MEDIA / MEASUREMENTS':'SYNTHETIC FOOTAGE';$('origin-badge').textContent='合成';$('reader').checked=true;seeking=false;clear($('inspection'));$('error').hidden=true;
}
$('demo').onclick=safe(()=>openMode('MOCK'));$('local').onclick=()=>$('file-input').click();
function uploadFile(file){return new Promise((resolve,reject)=>{
  const xhr=new XMLHttpRequest();xhr.open('POST','/api/assets');xhr.setRequestHeader('Content-Type','application/octet-stream');xhr.setRequestHeader('X-Session-Token',token);xhr.setRequestHeader('X-File-Name',encodeURIComponent(file.name));
  xhr.upload.onprogress=e=>{if(e.lengthComputable){$('upload-progress').value=e.loaded/e.total*100;$('upload-status').textContent=`localhostへ取込み ${Math.round(e.loaded/e.total*100)}% · ${(e.total/1024**2).toFixed(1)} MiB`;}};
  xhr.onload=()=>{let data;try{data=JSON.parse(xhr.responseText);}catch{return reject(Error('取込み応答が不正です。'));}xhr.status===200?resolve(data):reject(Error(data.error));};
  xhr.onerror=()=>reject(Error('localhostへの取込み通信に失敗しました。'));xhr.send(file);
});}
$('file-input').onchange=safe(async()=>{
  const file=$('file-input').files[0];if(!file)return;
  if(file.size>2*1024**3)throw Error('取込み上限は2 GiBです。');
  video.pause();seeking=true;while(busy)await new Promise(r=>setTimeout(r,20));
  await api('/api/control',{epoch,action:'stop'});stopped=true;
  $('upload-progress').hidden=false;$('error').hidden=true;
  try{selectedAsset=await uploadFile(file);$('range-controls').hidden=false;$('analysis-start').max=Math.max(0,selectedAsset.duration_s-.1);$('analysis-start').value=0;
    $('asset-info').textContent=`${selectedAsset.name} · backend decode OK · ${selectedAsset.duration_s.toFixed(1)}秒 · ${selectedAsset.id}`;
    $('upload-status').textContent='取込み完了。開始位置を指定して解析を準備してください。';
  }finally{$('upload-progress').hidden=true;seeking=false;$('file-input').value='';}
});
async function prepareLocal(){
  receipt=null;
  if(!selectedAsset)throw Error('動画を選択してください。');
  const start=Number($('analysis-start').value);
  if(!Number.isFinite(start)||start<0||start>=selectedAsset.duration_s)throw Error('開始位置が動画の範囲外です。');
  video.pause();seeking=true;while(busy)await new Promise(r=>setTimeout(r,20));
  try{
    receipt=null;replaying=false;stopped=false;seq=0;epochStart=start;frameTime=start;analysisEnd=Math.min(selectedAsset.duration_s,start+120);
    render(await api('/api/session',{mode:'LOCAL',asset_id:selectedAsset.id,start_s:start}));
    video.src='/media/'+selectedAsset.id;
    const loaded=new Promise((resolve,reject)=>{video.addEventListener('loadedmetadata',resolve,{once:true});video.addEventListener('error',()=>reject(Error('browser decodeに失敗しました。backend decodeとは別の制限です。')),{once:true});});
    video.load();await loaded;
    await api('/api/assets/browser-ready',{asset_id:selectedAsset.id,duration_s:video.duration});
    if(start>0){const sought=new Promise(resolve=>video.addEventListener('seeked',resolve,{once:true}));video.currentTime=start;await sought;}
    $('seek').min=start;$('seek').max=analysisEnd;$('seek').value=start;$('replay-box').hidden=true;$('origin-badge').textContent='実観測';
    $('mode-note').textContent='実動画・実ローカル解析／評価のみMOCK。意味候補は実ASR・VLMから生成します。MOCK証拠評価は配線確認用で、下段の意味判断は評価不能として表示します。実Jevは未送信です。';
    $('video-label').textContent='LOCAL VIDEO / REAL OBSERVATIONS';$('upload-status').textContent=`解析区間 ${start.toFixed(1)}–${analysisEnd.toFixed(1)}秒。モデルready後に再生してください。`;
    clear($('inspection'));$('error').hidden=true;
  }finally{seeking=false;}
}
$('prepare-local').onclick=safe(prepareLocal);
$('live-start').onclick=safe(async()=>{await prepareLocal();render(await api('/api/live/start',{asset_id:selectedAsset.id}));$('mode-note').textContent='LIVE-JEV: 引用文章・計測・候補・場の派生情報を承認済みの送信先で評価します。元動画・音声・顔cropは送信しません。再生を押すと解析と評価を開始します。';});
$('delete-asset').onclick=safe(async()=>{if(!selectedAsset)return;await openMode('MOCK');await api('/api/assets/delete',{asset_id:selectedAsset.id});selectedAsset=null;$('range-controls').hidden=true;$('asset-info').textContent='専用領域の取込みコピーを削除しました。元ファイルは変更していません。';});
$('reader').onchange=safe(()=>api('/api/control',{epoch,action:'reader',enabled:$('reader').checked}));
$('replay').onclick=safe(async()=>{video.pause();replaying=true;while(busy)await new Promise(r=>setTimeout(r,20));const r=await api('/api/replay',{capture:true});$('replay-box').hidden=false;$('replay-index').max=Math.max(0,r.count-1);$('replay-index').value=0;$('replay-position').textContent=`1 / ${r.count}`;render(r.frame);});
$('replay-index').oninput=safe(async()=>{const r=await api('/api/replay',{index:Number($('replay-index').value)});$('replay-position').textContent=`${r.index+1} / ${r.count}`;render(r.frame);});
$('export').onclick=safe(async()=>{const response=await fetch('/api/export',{headers:{'X-Session-Token':token}});if(!response.ok)throw Error('export_failed');const url=URL.createObjectURL(await response.blob()),link=el('a');link.href=url;link.download='context-fields-session.jsonl';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
window.addEventListener('pagehide',()=>{if(token&&!debugMode)fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json','X-Session-Token':token},body:JSON.stringify({epoch,action:'stop'}),keepalive:true});});
(async()=>{const boot=await api('/api/bootstrap');token=boot.token;
  if(boot.debug){debugMode=true;replaying=true;stopped=true;const css=el('link');css.rel='stylesheet';css.href='/debug.css';document.head.append(css);const script=el('script');script.src='/debug.js';document.body.append(script);return;}
  $('live-start').hidden=!boot.live_enabled;$('session-id').textContent=boot.session;render(await api('/api/snapshot'));
  if(latest.mode==='LOCAL'&&latest.media?.id){selectedAsset=latest.media;analysisEnd=selectedAsset.analysis_end_s;epochStart=selectedAsset.analysis_start_s;stopped=latest.status==='stopped';
    $('range-controls').hidden=false;$('analysis-start').value=epochStart;$('seek').min=epochStart;
    const position=latest.clock.media_s;video.addEventListener('loadedmetadata',()=>{video.currentTime=position;frameTime=position;},{once:true});video.src='/media/'+selectedAsset.id;
    $('video-label').textContent='LOCAL VIDEO / REAL OBSERVATIONS';$('origin-badge').textContent='実観測';$('mode-note').textContent=latest.evaluation_label+' · 記録済みsessionを再表示しています。';
  }else video.src='/media/fixture';
  const recordedRun=new URLSearchParams(location.search).get('replay');
  if(recordedRun){replaying=true;const r=await api('/api/replay',{run_id:recordedRun});$('replay-box').hidden=false;$('replay-index').max=Math.max(0,r.count-1);$('replay-index').value=0;$('replay-position').textContent=`1 / ${r.count}`;render(r.frame);}
  setInterval(tick,100);
  const checkModels=async()=>{const m=await api('/api/models');modelsReady=m.status==='ready';if(!latest?.local_pipeline)$('model-status').textContent=`ローカルモデル: ${m.status}${m.error?' · '+m.error:''}`;};await checkModels();setInterval(()=>checkModels().catch(showError),2000);
})().catch(showError);
