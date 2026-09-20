"""Mechanical UNKNOWN diagnostics; never evidence and never a semantic classifier."""
REASONS = {'NO_OBSERVATION','OBSERVATION_UNRELIABLE','EVALUATION_PENDING','STALE_OR_EXPIRED',
 'BUDGET_OR_CAPACITY','READOUT_NOT_REGISTERED','CATEGORY_GAP','FACET_GAP','CONFLICT',
 'DECAY_OR_WEAK_SUPPORT','SOURCE_OR_SCOPE_MISMATCH','GENUINE_AMBIGUITY','LEGACY_MISSING'}


def build_triage(session, snapshot, readouts, transcripts, profiles):
    ledger=session.ledger; epoch=session.clock.epoch
    records=ledger.records
    evaluations={}; contributions={}
    for r in records.values():
        if r['kind']=='evidence_evaluation':evaluations.setdefault(r['observation_id'],[]).append(r)
        if r['kind']=='contribution':contributions.setdefault(r['evaluation_id'],[]).append(r['id'])
    transcript_refs={}
    for r in transcripts['history']:
        transcript_refs.setdefault(r['observation_id'],[]).append({'segment_id':r['segment_id'],'revision':r['revision']})
    index=[]; spaces={s:{'flags':[],'observation_ids':[],'semantic_analysis':'M3b_not_started'} for s in snapshot['spaces']}
    def flag(space,code,refs,explanation,next_action):
        spaces[space]['flags'].append({'reason_code':code,'basis':'mechanical','refs':refs,
                                     'explanation':explanation,'next_action':next_action})
    for obs in records.values():
        if obs['kind']!='observation' or obs['epoch']!=epoch:continue
        facet=obs.get('facet','');space=facet.split('.')[0]
        if space not in spaces:continue  # CPU measurements are visible separately; not semantic observations.
        active=obs['id'] in ledger.active
        evs=evaluations.get(obs['id'],[])
        hids=sorted({e['hypothesis_id'] for e in evs})
        # Include candidates waiting for their first evaluation (explicit link).
        hids=sorted(set(hids)|set(getattr(session,'candidate_links',{}).get(obs['id'],[])))
        profs=[p for p in profiles if set(p['category_ids']) & set(hids)]
        connected=bool(profs) or facet in {'conversation.register','person.face_expression','place.apparent_time_of_day'}
        mapped=bool(hids)
        active_evs=[e for e in evs if e['id'] in ledger.active]
        state='superseded' if not active else 'evaluated' if active_evs else 'evaluation_pending' if mapped else 'no_candidate'
        if active and mapped and not connected:state='readout_not_registered' if active_evs else 'evaluation_pending'
        refs=transcript_refs.get(obs.get('asr_id',obs['id']),[])
        item={'observation_id':obs['id'],'epoch':epoch,'space':space,'facet':facet,'scope':obs['scope'],
              'subject':obs['subject'],'active':active,'text':obs['text'],'citations':obs['citations'],
              'transcript_refs':refs,'hypothesis_ids':hids,'category_ids':hids,
              'evaluation_ids':[e['id'] for e in evs],
              'contribution_ids':[c for e in evs for c in contributions.get(e['id'],[])],
              'profile_ids':[p['id'] for p in profs],
              'readout_ids':[r['id'] for r in readouts.values() if r.get('id') and set(r.get('included_ids',[]))&set(hids)],
              'processing_state':state,'semantic_status':'not_inferred' if mapped else 'M3b_analysis_pending'}
        index.append(item);spaces[space]['observation_ids'].append(obs['id'])
    for space,body in spaces.items():
        current=[r for r in index if r['space']==space and r['active']]
        ids=[r['observation_id'] for r in current]
        if not current:flag(space,'NO_OBSERVATION',[], '現在のepoch/scopeに有効な意味観測がありません。','公開済み入力とadapterを確認')
        pending=[r['observation_id'] for r in current if r['processing_state']=='evaluation_pending']
        if pending:flag(space,'EVALUATION_PENDING',pending,'候補に対する有効な評価がまだありません。','評価queue・採否ログを確認')
        missing=[r['observation_id'] for r in current if r['processing_state']=='readout_not_registered']
        if missing:flag(space,'READOUT_NOT_REGISTERED',missing,'既存候補には評価がありますが対応する読取りが未接続です。','入力と既存templateの適合を確認。意味定義不足はM3b待ち')
        expired=[r['id'] for r in readouts.values() if r.get('space')==space and r.get('id') and r['status'] in {'stale','superseded'}]
        old=[r['observation_id'] for r in current if session.clock.media_s-max(r['citations'].values())>8]
        if expired or old:flag(space,'STALE_OR_EXPIRED',expired+old,'根拠は8秒の新規適用TTLを超過、またはreadoutが失効。既存寄与は規則通り減衰します。','入力時刻・現在時刻・再生状態を別々に確認')
        rows=snapshot['spaces'][space]['hypotheses']
        conflict=[r['id'] for r in rows if r['conflict']]
        if conflict:flag(space,'CONFLICT',conflict,'同じ仮説に支持と反証が併存しています。','両方の出典を確認')
        decay=[r['id'] for r in rows if r['S'] or r['R']]
        if decay:flag(space,'DECAY_OR_WEAK_SUPPORT',decay,'寄与にはroot配分と時刻減衰が適用され、不明の基準質量U=1が残ります。カテゴリ不足の判定ではありません。','支持・反証・元時刻とroot配分を確認')
        if session.dispatcher.stopped and not session.stopped:
            flag(space,'BUDGET_OR_CAPACITY',session.dispatcher.log[-3:],'評価送信が停止しています。エラー・上限の具体的理由は参照ログを確認してください。','停止原因の修正後、残予算内で明示的に開始')
        deferred=[e['event_id'] for e in ledger.events[-100:] if e['kind']=='candidate_deferred']
        if deferred:flag(space,'BUDGET_OR_CAPACITY',deferred,'候補上限により保留されました。','上限を維持して待機')
        mismatch=[r['observation_id'] for r in index if r['space']==space and not r['active'] and r['scope'] not in {'session',snapshot['shot']}]
        if mismatch:flag(space,'SOURCE_OR_SCOPE_MISMATCH',mismatch,'旧shotの観測は現在の場へ適用されません。','旧scopeの根拠と現在scopeを区別')
        if space=='conversation' and transcripts['state'] in {'recognition_error','no_audio'}:
            flag(space,'OBSERVATION_UNRELIABLE' if transcripts['state']=='recognition_error' else 'NO_OBSERVATION',[],transcripts['reason'] or transcripts['state'],'原文・音声track・ASR処理状態を確認')
        if space=='conversation' and transcripts['state']=='processing':
            flag(space,'EVALUATION_PENDING',[], 'ASRまたは文章解析が処理中です。','受付前slotと推論待機を確認')
        pipe=session.local_pipeline
        errors=[e for e in (pipe.errors if pipe else []) if e['agent'] in ({'SpeechASR','TextContext'} if space=='conversation' else {'FrameInterpreter','FaceExpression'})]
        if errors:flag(space,'OBSERVATION_UNRELIABLE',errors,'モデル出力または引用の検証に失敗した記録があります。最新原文・処理時刻と照合してください。','原観測とlocal_errorを確認')
        waits=[{'agent':k,**v[1]['timing']} for k,v in (pipe.waiting.items() if pipe else [])
               if k in ({'SpeechASR','TextContext'} if space=='conversation' else {'FrameInterpreter','FaceExpression','ContextReader'})]
        if waits:flag(space,'BUDGET_OR_CAPACITY',waits,'共有ローカル推論枠の受付前slotで待機しています。','slot開始・最新入力・受付時刻を比較')
    return {'schema_version':'3','epoch':epoch,'input_cursor':len(ledger.events),
            'meaning':'UNKNOWNは未分類文章の割合ではありません。原因flagは割合の内訳ではありません。',
            'spaces':spaces,'unmapped_observation_index':index}
