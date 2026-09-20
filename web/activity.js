'use strict';
(async function startActivity(){
  const H=window.H0Review;
  const V=window.H11;
  if(!H)throw Error('H0 review service missing');
  document.body.classList.add('h1');document.title='解析ビューアー';
  const oldMain=document.querySelector('main');oldMain.hidden=true;
  const head=el('div','h1-header'),title=el('h1','','解析ビューアー'),mode=el('span','','保存記録を再生');head.append(title,mode);document.body.append(head);
  const main=el('main','h1-main'),left=el('section'),feed=el('section','h1-feed');main.append(left,feed);document.body.append(main);
  function button(label,parent,fn,id){const b=el('button','',label);if(id)b.id=id;b.onclick=safe(fn);parent.append(b);return b;}
  function detail(label,parent){const d=el('details');d.append(el('summary','',label));parent.append(d);return d;}
  function addText(id,parent,tag='div',cls=''){const n=el(tag,cls);n.id=id;parent.append(n);return n;}
  const fmt=t=>Number.isFinite(t)?`${Math.floor(t/60).toString().padStart(2,'0')}:${Math.floor(t%60).toString().padStart(2,'0')}`:'—';
  let running=false,position=0,duration=0,page=null,offset=0,pinned=null,savedPosition=null,loop=null,version=0,polling=false,last=0,lastPoll=0,match='missing',selectionSource=null;
  let loadVersion=0,selectedKey=null,selectedTargetText='',selectionVersion=0,displayGeneration=0,selectedRow=null,updateSequence=0;
  let thumbQueue=[],thumbActive=0;const thumbRequests=new Set();
  function protectDraft(){if(H.hasDraft?.())throw Error('入力中の判定を保存してから対象を変更してください');}
  const controls=el('div','h1-controls');
  left.append(video);video.controls=false;video.onratechange=null;
  const play=button('▶ 再生',controls,()=>togglePlayback(),'h1-play');play.className='primary';play.disabled=true;
  const back=button('記録に戻る',controls,returnToRecord,'h1-back');back.hidden=true;
  const time=addText('h1-time',controls,'span','h1-time');left.append(controls);
  const seek=addText('h1-seek',left,'input');seek.type='range';seek.min=0;seek.step=.01;seek.max=0;seek.value=0;seek.disabled=true;seek.setAttribute('aria-label','記録の再生位置');
  const source=addText('h1-source',left,'p','h1-source');source.textContent='「記録を開く」から始めます。';
  const selection=addText('h1-selection',left,'section','h1-selection');selection.hidden=true;
  const selectedLabel=addText('h1-selected-label',selection,'h2'),selectedText=addText('h1-selected-text',selection,'dl','h11-fields h11-selected-fields');
  const selectedItem=addText('h11-selected-item',selection,'p');
  const sourceImage=addText('h1-source-image',selection,'img','h1-source-image');sourceImage.alt='対象時刻の元動画から再構成したフレーム';sourceImage.hidden=true;
  const actions=el('div','h1-controls');selection.append(actions);
  const listen=button('この区間を聴く',actions,async()=>{if(!selectionSource?.length)return;loop={ranges:selectionSource,index:0};video.currentTime=loop.ranges[0][0];await video.play();},'h1-listen');listen.hidden=true;
  button('確認を記録',actions,openReview,'h1-review');actions.append($('h0-save-status'));
  const technical=detail('技術情報',selection),technicalText=addText('h1-technical',technical,'pre'),members=addText('h1-members',technical);
  const comparison=addText('h11-comparison',selection,'div');
  const originalInspection=detail('元の記録と根拠',technical);originalInspection.append($('inspection'));
  const rawEvents=detail('元の全イベント',technical);const rawList=addText('h1-event-list',rawEvents);let eventOffset=0;
  button('次の記録',rawEvents,async()=>{eventOffset+=50;await showRawEvents();});
  rawEvents.ontoggle=()=>{if(rawEvents.open)showRawEvents().catch(showError);};
  const paneOffsets={logs:0,evaluations:0},latestButtons={},containers={},emptyLabels={},counts={};
  const statusDialog=el('dialog','h1-dialog'),statusDetail=el('pre');statusDialog.append(el('h2','','評価状況の根拠'),statusDetail);document.body.append(statusDialog);button('閉じる',statusDialog,()=>statusDialog.close());
  let evaluationProgress;
  for(const [pane,label,id] of [['evaluations','Jev評価','h12-evaluations'],['logs','実行ログ','h1-rows']]){
    const section=el('section','h12-pane'),heading=el('div','h1-feed-head');heading.append(el('h2','',label));section.append(heading);feed.append(section);
    latestButtons[pane]=button('最新へ',heading,async()=>{paneOffsets[pane]=0;lists.latest(pane);await update();},'h12-latest-'+pane);latestButtons[pane].hidden=true;
    counts[pane]=el('span','h12-count');heading.append(counts[pane]);
    if(pane==='evaluations'){evaluationProgress=addText('cd-evaluation-progress',section,'button','cd-evaluation-progress');evaluationProgress.hidden=true;evaluationProgress.onclick=()=>{pause();statusDetail.textContent=JSON.stringify(page?.evaluation_progress||{},null,2);statusDialog.showModal();};}
    emptyLabels[pane]=el('p','h1-empty',pane==='evaluations'?'評価結果はまだありません':'再生すると、ここに結果が現れます。');section.append(emptyLabels[pane]);
    containers[pane]=addText(id,section,'div','h1-rows');containers[pane].tabIndex=0;containers[pane].setAttribute('aria-label',label);
    const paging=el('div','h1-more');section.append(paging);
    button('前の行',paging,async()=>{protectDraft();pause();paneOffsets[pane]=(page?.[pane]?.offset||0)+30;lists.anchors[pane].latest();await update();});
    button('新しい行',paging,async()=>{protectDraft();pause();paneOffsets[pane]=Math.max(0,(page?.[pane]?.offset||0)-30);lists.anchors[pane].latest();await update();});
  }
  const rows=containers.logs;
  const lists=new V.EvaluationLists(rows,containers.evaluations,row=>safe(()=>selectRow(row,page.cursor))(),(pane,browsing,manual)=>{
    if(manual&&!browsing){paneOffsets[pane]=0;lists.latest(pane);safe(update)();}
    latestButtons[pane].hidden=!browsing&&!paneOffsets[pane];
  });
  const view=lists.logs;
  const fields=detail('文脈の場',main);fields.className='h1-fields';fields.append($('panels'));
  fields.ontoggle=()=>{if(fields.open&&page){pause();H.showFields(pinned?.cursor||page.cursor).then(()=>refreshControls()).catch(showError);}};
  const saved=detail('保存した確認・書き出し',main);saved.className='h1-fields';saved.append($('h0-saved'));
  // Preparation is one place. No kind tabs, filters, search or future panels in this layout.
  const prep=el('dialog','h1-dialog');prep.id='h1-open-dialog';prep.append(el('h2','','記録を開く'));document.body.append(prep);
  const runLabel=el('label','','保存run');runLabel.append($('h0-run'));prep.append(runLabel);
  const progress=addText('h1-preparation-status',prep,'p');prep.append($('h0-index-status'));
  button('この記録を開く',prep,prepare,'h1-prepare');button('中止',prep,async()=>{loadVersion++;if(H.state().run)await H.post('cancel',{run:H.state().run});prep.close();});
  const choose=button('元動画を選択',prep,()=>$('file-input').click(),'h1-choose-media');choose.hidden=true;prep.append($('file-input'));
  button('閉じる',prep,()=>prep.close());button('記録を開く',head,()=>{protectDraft();pause();prep.showModal();},'h1-open');
  const reviewDialog=el('dialog','h1-dialog');reviewDialog.id='h1-review-dialog';reviewDialog.append(el('h2','','確認を記録'));document.body.append(reviewDialog);
  const reviewTarget=addText('h1-review-target',reviewDialog,'p');
  const reviewReference=detail('判定の固定対象・根拠',reviewDialog),reviewReferenceText=addText('h1-review-reference',reviewReference,'pre');
  const reviewForm=$('h0-review');reviewDialog.append(reviewForm);
  const more=detail('参照文・判定の条件',reviewForm);
  const visible=new Set(['h0-reviewer','h0-verdict','h0-note','h0-confirm']);
  const evidence=el('div','h1-evidence');
  for(const n of [...reviewForm.children]){if(n===more||n.tagName!=='LABEL')continue;const input=n.querySelector('input,select,textarea');if(input?.dataset.evidence)evidence.append(n);else if(!visible.has(input?.id))more.append(n);}
  reviewForm.insertBefore(evidence,$('h0-confirm').parentElement);
  const reviewActions=el('div','h1-actions');reviewDialog.append(reviewActions);button('閉じる',reviewActions,()=>reviewDialog.close());
  button('保存',reviewActions,async()=>{await H.save();reviewDialog.close();},'h1-save').className='primary';
  const errors=addText('h1-error',main,'div','h1-error');main.append($('error'));errors.hidden=true;
  function pause(){running=false;video.pause();loop=null;refreshControls();}
  function refreshControls(){play.textContent=running?'Ⅱ 一時停止':'▶ 再生';time.textContent=page?.meta.mode==='order'?`記録 ${page.cursor} / ${duration}`:'記録 '+fmt(position)+' / '+fmt(duration);seek.value=position;}
  function resetView(){displayGeneration++;thumbQueue=[];thumbRequests.clear();paneOffsets.logs=0;paneOffsets.evaluations=0;lists.reset(H.state().run,displayGeneration);}
  async function prepare(){
    protectDraft();pause();version++;selectionVersion++;pinned=null;selection.hidden=true;back.hidden=true;sourceImage.hidden=true;choose.hidden=true;play.disabled=true;seek.disabled=true;
    const ticket=++loadVersion;progress.textContent='記録と動画を準備しています…';
    if(!$('h0-run').value)throw Error('保存runを選択してください。');
    await H.openRun({simple:true});if(ticket!==loadVersion)return;
    const state=H.state();if(state.indexInfo?.status!=='ready')return;
    try{await H.setAsset(await H.post('managed-asset',{run:state.run}));if(ticket!==loadVersion)return;match=(await H.get('match',{run:state.run,asset_id:H.state().asset.id})).media_match;}
    catch(error){match='missing';progress.textContent='対応する管理動画を開けません。元動画を選ぶか、別のrunへ変更してください。';choose.hidden=false;return;}
    if(ticket!==loadVersion)return;if(match!=='hash_verified'){progress.textContent='一致情報が不足しています。元動画を選ぶか別runへ変更してください。';choose.hidden=false;return;}await ready();
  }
  $('file-input').onchange=safe(async()=>{const file=$('file-input').files[0];if(!file)return;await H.setAsset(await uploadFile(file));match=(await H.get('match',{run:H.state().run,asset_id:H.state().asset.id})).media_match;
    if(match==='mismatch'){progress.textContent='動画が記録と一致しません。元動画を選ぶか、runを変更してください。';return;}
    if(match!=='hash_verified')progress.textContent='一致情報がないため未確認です。原資料の判定提出は制限されます。';await ready();});
  async function ready(){position=0;offset=0;page=null;pinned=null;resetView();duration=H.state().indexInfo.activity.duration;seek.max=duration;seek.disabled=false;play.disabled=false;selection.hidden=true;back.hidden=true;source.textContent=match==='hash_verified'?'':'動画との一致情報なし・判定提出を制限';await update();prep.close();}
  async function update(){
    if(!H.state().run)return;const ticket=version;const requestSequence=++updateSequence;const positionAtRequest=position;
    const data=await H.get('evaluation-lists',{run:H.state().run,position:positionAtRequest,log_offset:paneOffsets.logs,evaluation_offset:paneOffsets.evaluations,
      log_anchor:lists.anchors.logs.capture()?.id||'',evaluation_anchor:lists.anchors.evaluations.capture()?.id||'',keep:pinned?.row_id||''});
    if(ticket!==version||requestSequence!==updateSequence||pinned)return;page=data;mode.textContent='保存記録を再生'+(data.meta.diagnostic_kind?' · '+data.meta.diagnostic_kind:data.meta.mode==='order'?' · 記録順で表示':'');
    evaluationProgress.hidden=!data.evaluation_progress?.hard_error;
    fields.hidden=!!data.meta.diagnostic_kind;
    evaluationProgress.textContent=data.evaluation_progress?.hard_error?data.evaluation_progress.label:'';
    evaluationProgress.title=data.evaluation_progress?.event_seq?'根拠イベント #'+data.evaluation_progress.event_seq+' · '+(data.evaluation_progress.reason||''):'';
    if(view.run!==H.state().run)resetView();
    const items=[...data.evaluations.items,...data.logs.items];
    lists.transaction({run_id:H.state().run,generation:displayGeneration,rows:items,snapshot:true});
    for(const pane of ['logs','evaluations']){emptyLabels[pane].hidden=!!lists[pane].rows.size;counts[pane].textContent=data[pane].total+'件';paneOffsets[pane]=data[pane].offset;latestButtons[pane].hidden=!lists.anchors[pane].anchor&&!paneOffsets[pane];}
    const activeThumbs=new Set(items.flatMap(row=>(row.source_refs||[]).slice(0,2).map(ref=>[displayGeneration,row.row_id,row.revision,ref.ref_id].join(':'))));
    for(const id of thumbRequests)if(!activeThumbs.has(id))thumbRequests.delete(id);
    for(const row of items)queueThumbnails(row,data.cursor);
    refreshControls();syncVideo();
  }
  function queueThumbnails(row,cursor){
    if(match!=='hash_verified')return;
    for(const ref of (row.source_refs||[]).filter(r=>r.modality==='video').slice(0,2)){
      const id=[displayGeneration,row.row_id,row.revision,ref.ref_id].join(':');if(thumbRequests.has(id))continue;
      thumbRequests.add(id);if(thumbQueue.length<120)thumbQueue.push({id,row,ref,cursor,generation:displayGeneration,asset:H.state().asset.id});
    }drainThumbnails();
  }
  function drainThumbnails(){while(thumbActive<2&&thumbQueue.length){const job=thumbQueue.shift();const targetView=job.row.pane==='evaluations'?lists.evaluations:lists.logs;if(job.generation!==displayGeneration||targetView.rows.get(job.row.row_id)?.revision!==job.row.revision)continue;thumbActive++;
    H.get('thumbnail',{run:job.row.run_id,cursor:job.cursor,row_id:job.row.row_id,revision:job.row.revision,ref_id:job.ref.ref_id,asset_id:job.asset,generation:job.generation})
      .then(data=>{lists.accept({type:'thumbnail',...data});if(data.state==='pending')thumbRequests.delete(job.id);})
      .catch(()=>lists.accept({type:'thumbnail',run_id:job.row.run_id,generation:job.generation,epoch:job.row.epoch,row_id:job.row.row_id,revision:job.row.revision,ref_id:job.ref.ref_id,state:'error'}))
      .finally(()=>{thumbActive--;drainThumbnails();});}}
  function mediaAt(){if(!page?.clock)return null;const c=page.clock,n=page.next_clock;let t=c.media;
    if(page.meta.mode==='elapsed'&&c.state==='playing'&&n&&n.epoch===c.epoch&&n.media>=c.media&&n.elapsed>c.elapsed)t=Math.min(n.media,c.media+Math.max(0,position-c.elapsed));
    return t;
  }
  function syncVideo(){if(pinned||!H.state().asset||!page?.clock)return;const t=mediaAt();if(!Number.isFinite(t))return;
    if(Math.abs(video.currentTime-t)>.45||(!running&&Math.abs(video.currentTime-t)>.03))video.currentTime=t;
    if(running&&page.clock.state==='playing'&&video.readyState>=2){if(video.paused)video.play().catch(e=>{pause();showError(e);});}else video.pause();
  }
  async function togglePlayback(){protectDraft();if(pinned){await returnToRecord();}if(!page)return;if(position>=duration){position=0;resetView();}running=!running;last=performance.now();if(!running)video.pause();await update();}
  seek.oninput=safe(async()=>{protectDraft();pause();version++;selectionVersion++;pinned=null;resetView();selection.hidden=true;back.hidden=true;reviewDialog.close();position=Number(seek.value);offset=0;await update();});
  video.ontimeupdate=()=>{if(loop&&video.currentTime>=loop.ranges[loop.index][1]){loop.index=(loop.index+1)%loop.ranges.length;video.currentTime=loop.ranges[loop.index][0];}};
  async function selectRow(row,at){
    protectDraft();pause();const ticket=++version;selectionVersion++;savedPosition=position;pinned={run:H.state().run,cursor:at,row_id:row.row_id};lists.freeze(row.row_id);
    $('h1-review').disabled=true;
    const selected=await H.get('activity-row',pinned);if(ticket!==version||!pinned||pinned.row_id!==row.row_id)return;
    selection.hidden=false;back.hidden=false;sourceImage.hidden=true;listen.hidden=true;selectedLabel.textContent=row.producer;selectedRow=selected;
    V.fields(selectedText,[...(selected.result_fields||[]),...(selected.original_fields||[]).map(f=>({...f,label:'元結果 · '+f.label}))],f=>safe(()=>selectMember(f.target_ref,f.label+'：'+V.value(f)))());
    technicalText.textContent=JSON.stringify({...selected,thumbnail_preparation:row.thumbnail_results||null},null,2);clear(members);eventOffset=0;
    for(const key of selected.members)button(key,members,()=>selectMember(key));
    for(const original of selected.original_activities||[])button('元の解析結果 · '+original.producer,selectedText,()=>selectRow(original,original.inspection_cursor));
    const first=selected.result_fields?.[0],key=first?.target_ref||[...selected.members].reverse().find(k=>k.startsWith('r:'))||selected.target_key;
    await selectMember(key,first?first.label+'：'+V.value(first):'処理の記録');
    clear(comparison);if(selected.comparison){comparison.append(el('p','',selected.comparison.missing?'比較画像の片方が記録不足':Number.isFinite(selected.comparison.interval_s)?`比較間隔 ${selected.comparison.interval_s.toFixed(3)} 秒`:'比較間隔の記録なし'));
      for(const ref of selected.source_refs.filter(r=>r.modality==='video'&&Number.isFinite(r.media_s)))button('元画像 '+V.stamp(ref.media_s),comparison,async()=>{pause();const captured=version;const f=await H.get('frame',{asset_id:H.state().asset.id,time:ref.media_s,stream:ref.stream});if(captured!==version)return;if(f.jpeg){sourceImage.src='data:image/jpeg;base64,'+f.jpeg;sourceImage.hidden=false;video.currentTime=f.media_s;}});}
    for(const pane of Object.values(containers))for(const n of pane.children)n.classList.toggle('selected',n.dataset.row===row.row_id);
  }
  async function selectMember(key,caption){
    protectDraft();if(!pinned)return;pause();const ticket=++selectionVersion,snapshot={...pinned};$('h1-review').disabled=true;
    const t=await H.get('target',{run:snapshot.run,cursor:snapshot.cursor,key});if(ticket!==selectionVersion||!pinned||pinned.cursor!==snapshot.cursor||pinned.row_id!==snapshot.row_id)return;
    clear($('inspection'));$('inspection').append(el('pre','',JSON.stringify(t,null,2)));
    for(const link of t.links.items)if(link.key)button('根拠 '+link.key,$('inspection'),()=>selectMember(link.key));
    selectedKey=key;selectedTargetText=caption||'技術情報の元記録';selectedItem.textContent='判定対象 · '+selectedTargetText;selectionSource=null;listen.hidden=true;sourceImage.hidden=true;
    const refs=t.source_refs||[],allAudioRefs=refs.filter(r=>r.kind==='audio_interval'&&r.media_range),imageRef=refs.find(r=>r.kind==='video_frame'&&r.media_range);
    const sampleRefs=allAudioRefs.filter(r=>!r.missing_reasons?.includes('segment_times_not_sample_exact')),audioRefs=sampleRefs.length?sampleRefs:allAudioRefs;
    if(audioRefs.length){
      const segment=t.body||{},lo=Number.isFinite(segment.media_start_s)?segment.media_start_s:-Infinity,hi=Number.isFinite(segment.media_end_s)?segment.media_end_s:Infinity;
      const ranges=audioRefs.map(r=>[Math.max(lo,r.media_range.start_s),Math.min(hi,r.media_range.end_s)]).filter(([a,b])=>b>a).sort((a,b)=>a[0]-b[0]);
      selectionSource=[];for(const r of ranges){const prior=selectionSource.at(-1);if(prior&&r[0]<=prior[1]+1e-6)prior[1]=Math.max(prior[1],r[1]);else selectionSource.push(r);}
      source.textContent=selectionSource.length?'元音声 '+selectionSource.map(([a,b])=>`${a.toFixed(3)}–${b.toFixed(3)}`).join(' / ')+' 秒':'元音声の対応区間なし';
      listen.hidden=match!=='hash_verified'||!selectionSource.length;if(!listen.hidden)video.currentTime=selectionSource[0][0];
    }
    else if(imageRef&&match==='hash_verified'){
      const f=await H.get('evidence-frame',{run:pinned.run,cursor:pinned.cursor,key,asset_id:H.state().asset.id});
      if(ticket!==selectionVersion||!pinned||pinned.row_id!==snapshot.row_id)return;
      if(f.jpeg){sourceImage.src='data:image/jpeg;base64,'+f.jpeg;sourceImage.hidden=false;video.currentTime=f.media_s;source.textContent=`元画像 ${f.media_s.toFixed(3)} 秒`;}
    }else source.textContent='元資料の時刻・対応情報が記録にありません。';
    const fixed=await H.post('pin',{run:snapshot.run,cursor:snapshot.cursor,key,asset_id:H.state().asset?.id,playhead:H.state().asset?video.currentTime:null});
    if(ticket!==selectionVersion||!pinned)return;await H.applyPin(fixed);$('h1-review').disabled=false;
  }
  function showReviewTarget(fixed){reviewTarget.textContent=selectedTargetText||fixed.target.target_id;reviewReferenceText.textContent=JSON.stringify(fixed,null,2);}
  async function openReview(){if(!pinned||!H.state().pin)return;pause();showReviewTarget(H.state().pin);reviewDialog.showModal();}
  async function returnToRecord(){protectDraft();pause();version++;selectionVersion++;pinned=null;lists.freeze(null);selectedKey=null;selection.hidden=true;back.hidden=true;sourceImage.hidden=true;source.textContent='';reviewDialog.close();position=savedPosition??position;await update();}
  async function selectField(key,label){protectDraft();pause();const ticket=++version;savedPosition=position;pinned={run:H.state().run,cursor:page.cursor,row_id:key};selection.hidden=false;back.hidden=false;selectedLabel.textContent=label;clear(selectedText);clear(members);clear(comparison);await selectMember(key,label);if(ticket!==version||!pinned)return;const t=await H.get('target',{run:pinned.run,cursor:pinned.cursor,key});if(ticket!==version)return;
    const known=['label','subject','facet','C','S','R','E','UNKNOWN','status'];V.fields(selectedText,known.filter(k=>t.body?.[k]!==undefined).map(k=>({label:k,value:t.body[k],type:typeof t.body[k]==='number'?'number':'text'})));}
  inspect=async row=>{const space=Object.keys(latest.snapshot.spaces).find(s=>latest.snapshot.spaces[s].hypotheses.some(h=>h.id===row.id));if(space)await selectField('s:'+space+':hypothesis:'+row.id,names[space]);};
  inspectUnknown=space=>selectField('s:'+space+':unknown:UNKNOWN',names[space]+' UNKNOWN').catch(showError);
  async function showRawEvents(){if(!pinned)return;const data=await H.get('events',{run:pinned.run,cursor:pinned.cursor,offset:eventOffset});clear(rawList);for(const r of data)button(`${r.seq} ${r.kind}`,rawList,async()=>{const e=await H.get('event',{run:pinned.run,cursor:pinned.cursor,seq:r.seq});technicalText.textContent=JSON.stringify(e,null,2);});}
  function tick(now){const dt=last?Math.min((now-last)/1000,.3):0;last=now;
    if(running&&!pinned&&!reviewDialog.open&&page){if(page.meta.mode==='elapsed'){position=Math.min(duration,position+dt);}else if(now-lastPoll>300&&page.next_cursor){position=page.next_cursor;}else if(!page.next_cursor)running=false;
      if(position>=duration)running=false;if(now-lastPoll>180&&!polling){lastPoll=now;polling=true;update().catch(e=>{pause();showError(e);}).finally(()=>{polling=false;});}refreshControls();}
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);refreshControls();
  window.H1Activity={prepare,selectRow,returnToRecord,update,
    restoreReview(row){pause();version++;selectionVersion++;pinned=null;selection.hidden=true;back.hidden=true;sourceImage.hidden=true;reviewTarget.textContent=row.annotation?.judgment.actual_text||row.pin.target.target_id;reviewReferenceText.textContent=JSON.stringify(row.pin,null,2);reviewDialog.showModal();},
    state:()=>({running,position,pinned,page}),setPosition:async value=>{protectDraft();pause();version++;selectionVersion++;if(value<position)resetView();position=value;await update();}};
})().catch(showError);
