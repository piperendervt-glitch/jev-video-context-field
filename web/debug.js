'use strict';
// This script is loaded only after a debug bootstrap. No clock/observe/session APIs.
(async function startH0(){
  document.body.classList.add('h0');
  document.title='Jev · Human Debug H0';
  let run=null,cursor=0,indexInfo=null,asset=null,target=null,pin=null,revision=0,idem=null,pendingSave=null,reviewDirty=false;
  let targetOffset=0,eventOffset=0,frameStamp=null,repeat=null,display=null,generation=0,modelSeen=false,activeReviews=[];
  const qp=o=>new URLSearchParams(Object.fromEntries(Object.entries(o).filter(([,v])=>v!==null&&v!==undefined))).toString();
  const get=(route,args={})=>api('/api/debug/'+route+'?'+qp(args));
  const post=(route,data={})=>api('/api/debug/'+route,data);
  function section(id,title,parent){const n=el('section','h0-panel');n.id=id;n.append(el('h2','',title));(parent||$('local-input')).append(n);return n;}
  function btn(text,fn,parent,id){const b=el('button','',text);if(id)b.id=id;b.onclick=safe(fn);parent.append(b);return b;}
  function text(id,parent,tag='div'){const n=el(tag);n.id=id;parent.append(n);return n;}
  function input(id,label,parent,type='text'){const l=el('label','',label),n=el(type==='textarea'?'textarea':'input');n.id=id;if(type!=='textarea')n.type=type;l.append(n);parent.append(l);return n;}
  function select(id,label,options,parent){const l=el('label','',label),n=el('select');n.id=id;for(const [v,name] of options){const o=el('option','',name);o.value=v;n.append(o);}l.append(n);parent.append(l);return n;}
  function json(parent,value){const p=el('pre','',JSON.stringify(value,null,2));parent.append(p);return p;}
  function details(title,value,parent){const d=el('details');d.append(el('summary','',title));json(d,value);parent.append(d);return d;}
  const blank=()=>{for(const p of Object.values(panels)){clear(p.tiles);clear(p.readout);clear(p.dynamic);clear(p.triage);p.tiles.append(el('p','','保存run未接続／結果なし'));}clear($('transcript-current'));clear($('transcript-history'));};
  for(const id of ['demo','replay','live-start','step','range-controls','reader','export'])$(id).hidden=true;
  $('model-status').textContent='H0: 推論・Jev・予算writerなし。導入済みモデルは読込みません。';
  $('mode').textContent='DEBUG / REPLAY · 非LIVE';$('origin-badge').textContent='保存結果';$('session-id').textContent='run未接続';
  $('mode-note').textContent='C0維持・新しい評価なし。人間の意味確認はpending。動画と保存runを選択してください。';
  $('video-label').textContent='実動画を選択';$('play-state').textContent='レビュー専用';$('time').textContent='未選択';
  const historicalNote=el('p','h0-warning','下の実行状況・API累計は選択runの当時の記録です。現在の永続予算ではありません。');$('stats').before(historicalNote);
  $('seek').disabled=true;video.controls=true;video.onratechange=null;video.onplay=null;video.onpause=null;video.onseeking=null;video.onseeked=null;
  $('play').onclick=safe(async()=>{if(!asset)throw Error('動画を選択してください');video.paused?await video.play():video.pause();});
  $('stop').onclick=()=>{video.pause();repeat=null;};
  $('seek').onchange=()=>{video.currentTime=Number($('seek').value);};
  video.onloadedmetadata=()=>{$('seek').max=video.duration;$('seek').disabled=false;$('play').disabled=false;};
  video.ontimeupdate=()=>{
    $('seek').value=video.currentTime;$('time').textContent=video.currentTime.toFixed(3)+' / '+(video.duration||0).toFixed(3)+' s';
    $('h0-player-clock').textContent=video.currentTime.toFixed(3)+' s';
    if(repeat&&video.currentTime>=repeat[1])video.currentTime=repeat[0];
  };
  const clock=el('div','h0-clocks');clock.append(el('span','','レビュー再生位置: '));text('h0-player-clock',clock).textContent='未選択';clock.append(el('span','','出典／入力: '));text('h0-source-clock',clock).textContent='未選択';clock.append(el('span','','当時の表示: '));text('h0-display-clock',clock).textContent='未選択';$('local-input').before(clock);
  const open=section('h0-open','H0a · 実動画と保存run');
  const runSelect=select('h0-run','保存run（大きいログは索引を作成）',[['','動画のみ / MEDIA_ONLY_REVIEW']],open);
  const runs=await get('runs');for(const r of runs.items){const o=el('option','',r.id+' · '+(r.bytes/1024**2).toFixed(1)+' MiB');o.value=r.id;runSelect.append(o);}
  btn('保存runを開く',openRun,open,'h0-open-run');btn('索引の中止',()=>run&&post('cancel',{run}),open,'h0-cancel-index');
  btn('このrunの管理動画をhash確認して開く',async()=>{if(!run)throw Error('runを選択してください');await setAsset(await post('managed-asset',{run}));},open,'h0-managed');
  text('h0-index-status',open);text('h0-match',open);
  btn('新しい保存runを一覧へ追加',async()=>{const rows=await get('runs');const known=new Set([...runSelect.options].map(o=>o.value));for(const r of rows.items)if(!known.has(r.id)){const o=el('option','',r.id+' · '+(r.bytes/1024**2).toFixed(1)+' MiB');o.value=r.id;runSelect.append(o);}},open);
  open.append(el('p','','MP4/WebM等、backendとbrowserの両方がdecode可能な動画。最大2 GiB。元ファイルは変更しません。旧runにhashがなければ未確認です。'));
  $('local').onclick=()=>$('file-input').click();
  $('file-input').onchange=safe(async()=>{const f=$('file-input').files[0];if(!f)return;video.pause();$('upload-progress').hidden=false;try{await setAsset(await uploadFile(f));}finally{$('upload-progress').hidden=true;$('file-input').value='';}});
  async function setAsset(a){asset=a;repeat=null;video.src='/media/'+a.id;video.load();$('video-label').textContent='LOCAL REAL VIDEO · REVIEW';$('asset-info').textContent=a.name+' · '+a.sha256+' · '+a.duration_s.toFixed(3)+' s';await showMatch();}
  async function showMatch(){const m=await get('match',{run,asset_id:asset?.id});$('h0-match').textContent='媒体照合: '+m.media_match;$('h0-match').className=m.media_match==='hash_verified'?'h0-good':'h0-warning';}
  async function openRun(options={}){const ticket=++generation;run=runSelect.value||null;window.h0Run=run;target=null;display=null;cursor=0;targetOffset=eventOffset=0;blank();clear($('h0-targets'));clear($('h0-events'));if(!run){await showMatch();return;}
    indexInfo=await post('index',{run});
    while(indexInfo.status==='indexing'&&ticket===generation){$('h0-index-status').textContent='索引 '+(100*indexInfo.bytes/indexInfo.total).toFixed(1)+'% · '+indexInfo.events+' events';await new Promise(r=>setTimeout(r,350));indexInfo=await get('status',{run});}
    if(ticket!==generation)return;$('h0-index-status').textContent=indexInfo.status+' · '+indexInfo.events+' events';if(indexInfo.status!=='ready')return;
    cursor=indexInfo.events;$('h0-cursor').max=cursor;$('h0-cursor').value=cursor;$('session-id').textContent=run;
    await showMatch();if(!options.simple)await loadCursor();
  }
  const playback=section('h0-playback','原音声・対象frame');
  select('h0-rate','レビュー速度',[['1','1倍'],['0.5','0.5倍'],['0.75','0.75倍'],['1.25','1.25倍']],playback).onchange=()=>{video.playbackRate=Number($('h0-rate').value);};
  input('h0-audio-start','反復開始（秒）',playback,'number').value=0;input('h0-audio-end','反復終了（秒）',playback,'number').value=5;
  btn('区間を実音声で反復',async()=>{if(!asset)throw Error('動画を選択してください');const a=Number($('h0-audio-start').value),b=Number($('h0-audio-end').value);if(!(a>=0&&b>a&&b<=video.duration))throw Error('区間を確認してください');repeat=[a,b];video.currentTime=a;await video.play();},playback,'h0-repeat');
  btn('反復解除',()=>{repeat=null;video.pause();},playback);
  for(const [d,title] of [['prev','前の実PTS frame'],['at','現在位置のframe'],['next','次の実PTS frame']])btn(title,()=>loadFrame(d),playback);
  const frameImage=text('h0-frame',playback,'img');frameImage.className='h0-frame';frameImage.alt='元動画から再構成したフレーム';frameImage.hidden=true;text('h0-frame-meta',playback,'pre');
  const cropImage=text('h0-crop',playback,'img');cropImage.className='h0-frame';cropImage.alt='保存ROIから再構成したcrop（モデル入力原画ではない）';cropImage.hidden=true;
  async function loadFrame(direction='at',time=null){if(!asset)throw Error('動画を選択してください');const f=await get('frame',{asset_id:asset.id,time:time??(direction==='at'?video.currentTime:frameStamp??video.currentTime),direction});if(!f.jpeg){$('h0-frame-meta').textContent=JSON.stringify(f);return;}frameStamp=f.media_s;frameImage.src='data:image/jpeg;base64,'+f.jpeg;frameImage.hidden=false;const {jpeg,...meta}=f;$('h0-frame-meta').textContent='元frame再構成／モデル入力画素はLEGACY_MISSING。crop・rotation同一性は保証しません。\n'+JSON.stringify(meta,null,2);video.pause();video.currentTime=f.media_s;}
  const records=section('h0-records','H0b · 当時のcursorと全対象',document.querySelector('.fields-column'));
  input('h0-cursor','event cursor（同じ秒でもepoch・改訂で別状態）',records,'number').value=0;
  btn('このcursorを読む',()=>{cursor=Number($('h0-cursor').value);targetOffset=eventOffset=0;return loadCursor();},records,'h0-load-cursor');
  btn('前の表示event',()=>moveDisplay(-1),records);btn('次の表示event',()=>moveDisplay(1),records);
  text('h0-display-info',records);
  const filter=select('h0-kind','全保存対象（旧版・棄却・低濃度も含む）',[['','全種類'],['transcript','ASR原文'],['observation','観測'],['hypothesis','候補'],['evidence_evaluation','Evidence'],['contribution','寄与'],['field_snapshot','Snapshot'],['dominance_evaluation','Dominance'],['dominance_readout','Readout']],records);
  input('h0-search','ID／原文で検索',records);btn('対象を検索',()=>{targetOffset=0;return listTargets();},records);
  text('h0-target-count',records);text('h0-targets',records).className='h0-list';btn('対象の前頁',()=>{targetOffset=Math.max(0,targetOffset-50);return listTargets();},records);btn('対象の次頁',()=>{targetOffset+=50;return listTargets();},records);
  text('h0-all-fields',records).className='h0-list';
  text('h0-events',records).className='h0-list';btn('処理eventの前頁',()=>{eventOffset=Math.max(0,eventOffset-50);return listEvents();},records);btn('処理eventの次頁',()=>{eventOffset+=50;return listEvents();},records);
  async function moveDisplay(delta){if(!run)return;const next=await get('adjacent-display',{run,cursor,direction:delta<0?'prev':'next'});if(next.cursor){cursor=next.cursor;$('h0-cursor').value=cursor;await loadCursor();}}
  async function loadCursor(){if(!run)return;const at=await get('display',{run,cursor});display=at.frame;modelSeen=true;if(display){display.replay_event_cursor=cursor;render(display);$('h0-display-info').textContent='cursor '+cursor+' / display '+at.cursor+' / epoch '+display.clock.epoch;$('h0-display-clock').textContent=display.clock.media_s.toFixed(3)+' s · epoch '+display.clock.epoch+' · cursor '+cursor;$('mode').textContent='REPLAY_EXACT · '+display.evaluation_label;$('seek').value=video.currentTime;}
    await listTargets();await listEvents();renderAllFields();renderRaw();
  }
  async function listTargets(){if(!run)return;const data=await get('targets',{run,cursor,offset:targetOffset,kind:filter.value,query:$('h0-search').value});clear($('h0-targets'));$('h0-target-count').textContent=data.total+' 件 / offset '+targetOffset;for(const t of data.items)btn(t.kind+' · '+t.id+' / rev '+t.revision+' / cursor '+t.seq+' · '+t.label,()=>inspectTarget(t.key),$('h0-targets'));}
  async function listEvents(){if(!run)return;const data=await get('events',{run,cursor,offset:eventOffset});clear($('h0-events'));for(const e of data)btn('event '+e.seq+' · '+e.kind+' · epoch '+e.epoch+' · '+e.media,()=>inspectTarget('e:'+e.seq),$('h0-events'));}
  function renderAllFields(){const box=$('h0-all-fields');clear(box);if(!display)return;for(const [space,data] of Object.entries(display.snapshot.spaces)){box.append(el('h3','',names[space]+' · 全facet／人物／低濃度'));for(const h of data.hypotheses)btn(h.subject+' · '+h.facet+' · '+h.label+' · C '+h.C,()=>inspectTarget('s:'+space+':hypothesis:'+h.id),box);btn(space+' UNKNOWN '+data.UNKNOWN,()=>inspectTarget('s:'+space+':unknown:UNKNOWN'),box);btn(space+' 保存readout（error/null保持）',()=>inspectTarget('s:'+space+':readout:readout'),box);}btn('保存FieldSnapshotを確認',()=>inspectTarget('s:person:snapshot:snapshot'),box);}
  function renderRaw(){if(!display)return;const box=$('transcript-current');clear(box);const rows=display.transcripts?.current||[];if(!rows.length)box.append(el('p','','原文なし / LEGACY_MISSINGまたは未認識。無音と判定しません。'));for(const t of rows){const b=el('button','transcript-entry',`${t.media_start_s}–${t.media_end_s} s · ${t.segment_id} / rev ${t.revision}\n${t.text}`);b.onclick=safe(async()=>{const q=await get('targets',{run,cursor,kind:'transcript',query:t.segment_id});const found=q.items.find(x=>String(x.revision)===String(t.revision));if(found)await inspectTarget(found.key);else throw Error('LEGACY_MISSING: transcript record');});box.append(b);}}
  inspect=async row=>{const space=Object.keys(display.snapshot.spaces).find(s=>display.snapshot.spaces[s].hypotheses.some(h=>h.id===row.id));if(space)await inspectTarget('s:'+space+':hypothesis:'+row.id);};
  inspectUnknown=space=>inspectTarget('s:'+space+':unknown:UNKNOWN').catch(showError);
  inspectTranscript=segment=>get('targets',{run,cursor,kind:'transcript',query:segment.segment_id}).then(rows=>{const t=rows.items.find(r=>String(r.revision)===String(segment.revision));if(!t)throw Error('LEGACY_MISSING: transcript record');return inspectTarget(t.key);}).catch(showError);
  async function inspectTarget(key){const selectedRun=run,selectedCursor=cursor;const fetched=await get('target',{run:selectedRun,cursor:selectedCursor,key});if(run!==selectedRun||cursor!==selectedCursor)return;target={...fetched,run:selectedRun,cursor:selectedCursor};const box=$('inspection');clear(box);box.append(el('h3','',target.id+' / rev '+target.revision+' / epoch '+target.epoch));json(box,target.body);$('h0-source-clock').textContent=target.source_refs.map(r=>r.media_range?`${r.media_range.start_s}–${r.media_range.end_s} s`:'LEGACY_MISSING').join(', ')||(target.source_as_of_media_s===null?'LEGACY_MISSING':target.source_as_of_media_s+' s');
    for(const ref of target.source_refs){const span=ref.media_range;if(!span)continue;btn(ref.kind+' 出典 '+span.start_s+'–'+span.end_s+' 秒を開く',async()=>{if(!asset)throw Error('同じ動画を選択してください');const m=await get('match',{run,asset_id:asset.id});if(m.media_match!=='hash_verified')throw Error('媒体hash未検証。このrunの出典として再生できません。動画単独レビューは可能です。');$('h0-audio-start').value=span.start_s;$('h0-audio-end').value=span.end_s;video.currentTime=span.start_s;if(ref.kind==='video_frame')await loadFrame('at',span.start_s);},box);}
    details('入力ref・欠損・crop／track情報',target.source_refs,box);
    if(target.source_refs.some(r=>r.kind==='video_frame'))btn('元frame・匿名track・保存ROI cropを確認',async()=>{if(!asset)throw Error('動画を選択してください');const f=await get('evidence-frame',{run:selectedRun,cursor:selectedCursor,key,asset_id:asset.id});if(f.jpeg){frameImage.src='data:image/jpeg;base64,'+f.jpeg;frameImage.hidden=false;frameStamp=f.media_s;}cropImage.hidden=!f.crop;if(f.crop)cropImage.src='data:image/jpeg;base64,'+f.crop.jpeg;const {jpeg,crop,...meta}=f;$('h0-frame-meta').textContent=JSON.stringify({...meta,crop:crop?{...crop,jpeg:'[preview shown separately]'}:'LEGACY_MISSING:ROI/model_input_pixels'},null,2);},box);
    for(const link of target.links.items){if(link.key)btn(link.direction+' → '+(link.id||link.kind||'')+' '+link.key,()=>inspectTarget(link.key),box);else box.append(el('p','','LEGACY_MISSING '+link.id));}
    btn('この箇所を確認（対象版を固定）',pinTarget,box,'h0-pin-target');
  }
  const timeline=section('h0-coverage','入力coverage・待ち・停止',document.querySelector('.fields-column'));
  btn('このcursorまでの処理を読む',async()=>{if(!run)return;const data=await get('timeline',{run,cursor});clear($('h0-coverage-data'));const box=$('h0-coverage-data'),duration=asset?.duration_s||Math.max(1,display?.clock.media_s||120);box.append(el('p','','青: ASR実入力（導出は明記）／緑: 認識segment。認識されなかった部分の内容は不明です。'));
    for(const [title,spans,cls] of [['ASR入力',data.asr_inputs.filter(x=>x.interval).map(x=>x.interval),''],['認識segment',(display?.transcripts?.current||[]).map(t=>[t.media_start_s,t.media_end_s]),'recognized'],['player公開済み',display?.clock.released.audio||[],'']]){box.append(el('h3','',title));const band=el('div','h0-band '+cls);for(const [a,b] of spans){const n=el('span');n.style.left=(a/duration*100)+'%';n.style.width=((b-a)/duration*100)+'%';n.title=a+'–'+b;band.append(n);}box.append(band);}
    box.append(el('p','h0-warning',data.asr_input_status||'ASR入力記録を確認'));details('ASR入力：direct／derived／LEGACY_MISSING',data.asr_inputs,box);box.append(el('p','','slot_wait_sは受付前、inference_sは受付後。停止と後続TTLは別eventです。直近200件。全履歴は処理eventでページング。'));for(const state of data.states){const d=details(state.seq+' '+state.kind+' '+(state.agent||'')+' slot '+state.slot_wait_s+' / infer '+state.inference_s+' s',state.payload,box);btn('この処理eventを確認',()=>inspectTarget('e:'+state.seq),d);}},timeline,'h0-load-coverage');text('h0-coverage-data',timeline);
  const review=section('h0-review','H0a · 人間の判定を別保存');
  btn('動画だけのこの区間を確認',async()=>{pin=await post('pin',{asset_id:asset?.id,playhead:video.currentTime,interval:[Number($('h0-audio-start').value),Number($('h0-audio-end').value)],model_output_exposure:modelSeen?'shown_before_annotation':'not_shown_before_annotation'});revision=0;resetSave();showPin();},review,'h0-pin-media');
  text('h0-pin',review,'pre');
  async function pinTarget(){if(!target)throw Error('対象を選択してください');pin=await post('pin',{run:target.run,cursor:target.cursor,key:target.key,asset_id:asset?.id,playhead:asset?video.currentTime:null});revision=0;resetSave();showPin();}
  function resetSave(){idem=null;pendingSave=null;$('h0-confirm').checked=false;for(const n of review.querySelectorAll('[data-evidence]'))n.checked=false;$('h0-save-status').textContent='未保存';}
  function showPin(){$('h0-pin').textContent=JSON.stringify(pin,null,2);$('h0-exposure').value=pin.context.model_output_exposure;$('h0-basis').value=pin.context.review_basis;}
  input('h0-reviewer','確認者（ローカル自己申告）',review);
  select('h0-verdict','意味の判定',[['unreviewed','未確認'],['consistent_with_evidence','一致'],['partially_inconsistent','一部不一致'],['contradicted_by_evidence','不一致'],['ambiguous','曖昧'],['not_assessable','判断不能']],review);
  select('h0-review-status','確認状態',[['draft','途中'],['submitted','判定を提出']],review);
  input('h0-expected','人間の参照文／期待（ASR原文へ書き戻しません）',review,'textarea');input('h0-actual','実際に起きたこと',review,'textarea');input('h0-note','メモ／再現操作／未確認事項',review,'textarea');
  select('h0-severity','重要度',[['not_set','未設定'],['blocking','blocking'],['major','major'],['minor','minor'],['observation','観察']],review);
  select('h0-tag','問題分類',[['','なし'],...['asr_mismatch','input_gap','visual_mismatch','wrong_subject','wrong_time','unsupported_inference','stale_display','numeric_contract','ui_problem','category_gap_candidate','unknown_appropriate','missing_evidence','other'].map(x=>[x,x])],review);
  select('h0-action','依頼',[['none','なし'],['investigate','調査'],['repair_pipeline','配線修正'],['review_definition','定義検討'],['verify_again','再確認'],['retain_unknown','UNKNOWN保持']],review);
  for(const id of ['numeric','pipeline'])select('h0-'+id,id+'（意味判定と別）',[['unknown','未確認'],['pass','確認できた'],['fail','問題あり'],['not_applicable','非該当']],review);
  select('h0-basis','確認に使った範囲',[['recorded_input','記録入力'],['posthoc_past_media','事後に元資料を確認'],['hindsight','後続内容も参照'],['diagnostic_only','数値診断のみ'],['unknown','不明']],review);
  select('h0-exposure','モデル出力を先に見たか',[['shown_before_annotation','先に見た'],['not_shown_before_annotation','先に見ていない'],['unknown','不明']],review);
  select('h0-future','後続内容への露出',[['unknown','不明'],['none_reported','見ていないと申告'],['seen','見た']],review);
  for(const [id,label] of [['audio_listened','音声を聴いた'],['video_watched','映像を見た'],['frame_viewed','frameを見た'],['transcript_read','原文を読んだ'],['diagnostic_values_read','診断数値を確認した']]){const n=input('h0-evidence-'+id,label,review,'checkbox');n.dataset.evidence=id;}
  input('h0-confirm','上の資料を私自身が確認した（自動再生・テストを人間確認としない）',review,'checkbox');
  btn('判定を保存',saveReview,review,'h0-save');text('h0-save-status',review);
  async function saveReview(){if(!pin)throw Error('先に対象を固定してください');if(!pendingSave){idem=crypto.randomUUID();pendingSave={review_id:pin.review_id,expected_revision:revision,idempotency_key:idem,reviewer:$('h0-reviewer').value,
      exposure:{review_basis:$('h0-basis').value,model_output_exposure:$('h0-exposure').value,future_content_exposure:$('h0-future').value,reviewed_evidence:[...review.querySelectorAll('[data-evidence]:checked')].map(n=>n.dataset.evidence),human_evidence_confirmation:$('h0-confirm').checked},
      judgment:{review_status:$('h0-review-status').value,semantic_verdict:$('h0-verdict').value,numeric_check:{status:$('h0-numeric').value,evidence_ids:[],check_version:null},pipeline_check:{status:$('h0-pipeline').value,evidence_ids:[],check_version:null},issue_tags:$('h0-tag').value?[$('h0-tag').value]:[],severity:$('h0-severity').value,expected_text:$('h0-expected').value||null,actual_text:$('h0-actual').value||null,note:$('h0-note').value,requested_action:$('h0-action').value}};}
    const ack=await post('save',pendingSave);revision=ack.record.record_revision;pendingSave=null;reviewDirty=false;$('h0-save-status').textContent='backend保存ACK · revision '+revision+' · '+ack.record.record_id;await reloadReviews();}
  // A network retry retains its key/body. Intentional edits form a new request.
  review.addEventListener('input',()=>{pendingSave=null;reviewDirty=true;});review.addEventListener('change',()=>{pendingSave=null;reviewDirty=true;});
  window.addEventListener('beforeunload',event=>{if(reviewDirty){event.preventDefault();event.returnValue='';}});
  const saved=section('h0-saved','保存した判定・問題・再確認');text('h0-saved-list',saved).className='h0-list';
  btn('判定一覧を再読込',reloadReviews,saved);btn('次の未確認',async()=>{const p=activeReviews.find(r=>!r.annotation||r.annotation.judgment.review_status!=='submitted');if(p){restoreReview(p);window.H1Activity?.restoreReview(p);}else $('h0-save-status').textContent='保存pinに未確認なし。全動画確認済みではありません。';},saved,'h0-next-unreviewed');
  async function reloadReviews(){const data=await get('reviews');activeReviews=data.items;clear($('h0-saved-list'));for(const row of data.items)btn(row.pin.target.target_id+' / '+(row.annotation?.judgment.semantic_verdict||'未保存'),()=>{restoreReview(row);window.H1Activity?.restoreReview(row);},$('h0-saved-list'));$('h0-issues').textContent=JSON.stringify(data.issues,null,2);}
  function restoreReview(row){if(reviewDirty)throw Error('Save the current review first');pin=row.pin;const a=row.annotation;revision=a?.record_revision||0;resetSave();showPin();if(a){$('h0-reviewer').value=a.reviewer.label;for(const [id,key] of [['verdict','semantic_verdict'],['review-status','review_status'],['severity','severity'],['expected','expected_text'],['actual','actual_text'],['note','note'],['action','requested_action']])$('h0-'+id).value=a.judgment[key]||'';for(const id of ['numeric','pipeline'])$('h0-'+id).value=a.judgment[id+'_check'].status;for(const [id,key] of [['basis','review_basis'],['exposure','model_output_exposure'],['future','future_content_exposure']])$('h0-'+id).value=a.view_context[key];for(const n of review.querySelectorAll('[data-evidence]'))n.checked=a.view_context.reviewed_evidence.includes(n.dataset.evidence);$('h0-confirm').checked=a.view_context.human_evidence_confirmation;$('h0-tag').value=a.judgment.issue_tags[0]||'';$('h0-save-status').textContent='読込済み revision '+revision;}}
  select('h0-issue-state','固定中のreviewのissue状態',['open','triaged','fixed_pending_human','verified_by_human','reopened','deferred'].map(x=>[x,x]),saved);
  input('h0-fix-build','修正build（fixed_pending_human時は必須）',saved);input('h0-new-run','修正確認run（任意）',saved);input('h0-verification-record','新しい人間再確認record ID（verified時に必須）',saved);input('h0-issue-reason','変更理由',saved,'textarea');
  btn('issue状態を別記録',async()=>{if(!pin)throw Error('reviewを選択してください');const data=await get('reviews'),rows=data.issues.filter(x=>x.review_id===pin.review_id),last=rows.at(-1);await post('issue',{review_id:pin.review_id,expected_revision:last?.revision||0,status:$('h0-issue-state').value,reason:$('h0-issue-reason').value,fix_build:$('h0-fix-build').value,new_run:$('h0-new-run').value,verification_record_id:$('h0-verification-record').value});await reloadReviews();},saved);
  text('h0-issues',saved,'pre');
  btn('Codex向けJSONL・Markdownを生成',async()=>{const data=await post('export');for(const [key,name] of [['jsonl','findings.jsonl'],['markdown','codex-feedback.md']]){const blob=new Blob([data[key]],{type:'text/plain;charset=utf-8'}),url=URL.createObjectURL(blob),a=el('a','',name+' を保存');a.href=url;a.download=name;saved.append(a);a.append(el('br'));}text('h0-export-status',saved).textContent='human-review/export に生成済み・未送信。'+data.records+' records。共有前に内容を確認してください。';},saved,'h0-export');
  const diag=section('h0-score','Score · C0維持／静的診断',document.querySelector('.fields-column'));
  btn('保存済み数値比較を開く',async()=>{const d=await get('diagnostics');clear($('h0-score-data'));$('h0-score-data').append(el('p','','CP/CD未適用。case_01は合成の原本取得診断であり動画時刻を持ちません。'));for(const r of d.items){const row=details(r.sample_id+' · '+r.evidence_stage,r,$('h0-score-data'));btn('この数値診断を確認（動画と結合しない）',async()=>{pin=await post('pin',{diagnostic_id:r.sample_id});revision=0;resetSave();showPin();},row);}},diag);text('h0-score-data',diag);
  const caps=section('h0-capabilities','H0c · 機能と人間チェック',document.querySelector('.fields-column'));
  caps.append(el('p','h0-warning','human_acceptance = pending。自動試験・実素材接続・人間操作・意味確認は別です。'));
  const capabilities=await get('capabilities');for(const c of capabilities)caps.append(el('p','',c.feature_id+' · '+c.status+' · '+(c.reason||c.results||'結果なし')));
  select('h0-feature-check','操作確認する機能',capabilities.filter(c=>c.status==='implemented'&&c.feature_id!=='LOCAL_REAL_EVAL_MOCK').map(c=>[c.feature_id,c.feature_id]),caps);
  btn('この機能の人間チェックを固定',async()=>{if(!asset&&!target)throw Error('動画または保存対象を選択してください');pin=await post('pin',{run:target?.run||null,cursor:target?.cursor,key:target?.key,asset_id:asset?.id,playhead:asset?video.currentTime:null,interval:[Number($('h0-audio-start').value),Number($('h0-audio-end').value)],feature_check:$('h0-feature-check').value,model_output_exposure:modelSeen?'shown_before_annotation':'not_shown_before_annotation'});revision=0;resetSave();showPin();},caps);
  details('build別チェック（影響機能のみ再確認）',await get('checks'),caps);
  const localSection=section('h0-new-local','任意 · 実ローカル解析＋評価MOCK');
  localSection.append(el('p','h0-warning','以下の明示操作で、選択動画を使う別process・新runを起動します。既存ローカルモデルのみ。Jev・実キー・予算予約なし。評価はMOCKであり実Jevの意味品質ではありません。H0の保存run閲覧とは別です。'));
  input('h0-local-start','解析開始位置（秒・最大120秒）',localSection,'number').value=0;
  btn('選択動画で実ローカル解析＋MOCKを明示開始',async()=>{if(!asset)throw Error('動画を選択してください');const state=await post('start-local',{asset_id:asset.id,start_s:Number($('h0-local-start').value),mode_confirmation:'LOCAL_REAL_EVAL_MOCK'});$('h0-local-status').textContent=JSON.stringify(state);const link=el('a','','ローカル解析Viewerを別tabで開く（モデルready後に再生）');link.href=state.url;link.target='_blank';link.rel='noopener';localSection.append(link);},localSection,'h0-start-local');
  btn('ローカル解析process状態を確認',async()=>{$('h0-local-status').textContent=JSON.stringify(await get('local-status'));},localSection);text('h0-local-status',localSection,'pre');
  blank();await reloadReviews();
  // H1 replaces the primary layout while reusing the H0 forms, renderers and storage.
  window.H0Review={get,post,openRun,setAsset,reloadReviews,hasDraft:()=>reviewDirty,
    state:()=>({run,cursor,asset,indexInfo,target,pin}),
    setCursor(value){cursor=value;},
    async select(key,at){cursor=at;await inspectTarget(key);return target;},
    async pinSelected(){await pinTarget();return pin;},
    async showFields(at){cursor=at;await loadCursor();},
    async applyPin(value){if(reviewDirty)throw Error('先に入力中の判定を保存してください');pin=value;revision=0;resetSave();showPin();
      for(const [id,v] of Object.entries({'review-status':'submitted',verdict:'unreviewed',note:'',expected:'',actual:'',severity:'not_set',tag:'',action:'none',numeric:'unknown',pipeline:'unknown'}))$('h0-'+id).value=v;},
    save:saveReview};
  const h1css=el('link');h1css.rel='stylesheet';h1css.href='/activity.css';document.head.append(h1css);
  const h11view=el('script');h11view.src='/activity-view.js';h11view.onload=()=>{const h1script=el('script');h1script.src='/activity.js';document.body.append(h1script);};document.body.append(h11view);
})().catch(showError);
