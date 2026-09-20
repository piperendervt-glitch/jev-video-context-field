"""Real gated media -> local observations -> explicitly MOCK evaluation -> fields."""
import copy
import hashlib
import math
import time
from concurrent.futures import ThreadPoolExecutor
from .clock import root_key
from .fields import Hypothesis
from .media import PTSDecoder
from .observers import AudioMeasure,MotionCut,FaceTrack
from .dispatcher import MockTransport

def local_mock_response(request):
    """Wiring evaluator only. No synthetic story or synthetic dominance scores."""
    import json
    payload=json.loads(request.payload_json)
    answers={}
    for key,q in payload['questions'].items():
        if q['type']=='noul':answers[key]={'type':'noul','noul':1.0};continue
        if q['type']=='score':
            answers[key]={'type':'score','score':0.,'confidence':1.,'legend':{str(i):v for i,v in enumerate(q['criteria'])},
                          'probabilities':{str(i):float(i==0) for i in range(5)}}
            continue
        keys=q['criteria']
        winner='support' if 'support' in keys else 'insufficient' if 'assessable' in keys else 'unknown' if request.role=='dominance' else next(k for k in keys if k!='unknown')
        answers[key]={'type':'choice','choice':winner,'confidence':1.,'probabilities':{k:float(k==winner) for k in keys}}
    return {'model':'MOCK-local-wiring-v1','answers':answers,'usage':None}

def video_sources(session,frame):
    root=root_key(session.ledger.media_id,session.clock.epoch,'video',frame['stream'],frame['media_s'])
    src={k:frame[k] for k in ('pts','time_base','media_origin_s','stream')}
    src.update(interval=[frame['media_s'],frame['media_s']],modality='video',frame_id=frame['frame_id'])
    return {root:src}

def audio_sources(session,audio):
    sources={}
    for chunk in audio['chunks']:
        # Split on actual sample indices. Last sample belongs to its physical 2s bin.
        first=chunk['start_s'];size=len(chunk['samples']);i=0
        while i<size:
            a=first+i/16000;boundary=(math.floor(a/2)+1)*2
            stop=min(size,max(i+1,math.ceil((boundary-first)*16000-1e-7)))
            b=first+(stop-1)/16000
            key=root_key(session.ledger.media_id,session.clock.epoch,'audio',chunk['stream'],a)
            src={k:chunk[k] for k in ('pts','time_base','media_origin_s','sample_rate','container_pts','container_time_base','stream')}
            src.update(sample_offset=chunk['sample_offset']+stop-1,interval=[a,b],modality='audio')
            if key in sources:src['interval'][0]=sources[key]['interval'][0]
            sources[key]=src;i=stop
    return sources

class LocalPipeline:
    accepting=True
    def __init__(self,session,asset,models,start=0,*,end_s=None):
        if end_s is not None and (type(end_s) not in (int,float) or not start < end_s <= min(asset.metadata['duration_s'],start+120)):
            raise ValueError('analysis_end')
        self.s,self.asset,self.models=session,asset,models
        self.decoder=PTSDecoder(asset)
        self.cpu=ThreadPoolExecutor(max_workers=1,thread_name_prefix='local-decode-cpu')
        self.measure=AudioMeasure();self.motion=MotionCut();self.faces=FaceTrack()
        self.pending={};self.waiting={};self.decode_future=None;self.last_decode=-1
        self.accepting=True
        self.last_asr=self.last_vlm=self.last_audio_candidate=self.last_face=-math.inf
        self.asr_revision=0;self.serial=0;self.drop=0;self.errors=[];self.latest_frame=None;self.latest_asr=None
        self.epoch=session.clock.epoch
        self.start=start;self.end=min(asset.metadata['duration_s'],start+120) if end_s is None else end_s
        self.s.local_enabled=True
        self.s.dispatcher.transport=MockTransport(local_mock_response)
        self.s.ledger.media_id=asset.id
        self.s.clock.duration_s=asset.metadata['duration_s']
        self.s.clock.strict_continuity=True
        self.s.clock.seek(start);self.s.ledger.reset(self.s.clock.epoch);self.epoch=self.s.clock.epoch
        self.s.media_info={**asset.public(),'analysis_start_s':self.start,'analysis_end_s':self.end,'source_mode':'LOCAL','evaluation_mode':'MOCK'}
        self.s.ledger.event('local_asset',{k:v for k,v in self.s.media_info.items() if k!='name'})

    def reset(self):
        self.cancel('epoch_changed')
        self.epoch=self.s.clock.epoch
        self.pending.clear();self.waiting.clear();self.decode_future=None
        self.motion=MotionCut();self.faces=FaceTrack()
        self.last_decode=-1;self.last_asr=self.last_vlm=self.last_audio_candidate=self.last_face=-math.inf
        self.latest_asr=self.latest_frame=None

    def _observation(self,agent,text,sources,*,facet,subject,scope,reference_mode,basis='upstream_model_description',dependencies=None,evidence=None,extra=None):
        self.serial+=1
        record={'id':f'local:{self.s.clock.epoch}:{agent}:{self.serial}','kind':'observation','revision':1,
                'epoch':self.s.clock.epoch,'dependencies':dependencies or {},'scope':scope,'subject':subject,'facet':facet,
                'reference_mode':reference_mode,'agent':agent,'text':text[:4000],'basis':basis,'origin':'LOCAL',
                'model':self.models.public()['models'].get(agent,'cpu-measurement-v1'),
                'input_roots':sorted(sources),'evidence_roots':sorted(evidence or sources),
                'citations':evidence or {k:v['interval'][1] for k,v in sources.items()},
                'input_cutoff_s':self.s.clock.media_s,'root_sources':copy.deepcopy(sources),
                'source_refs':[{'root':k,**v} for k,v in sources.items()],**(extra or {})}
        return record

    def _candidate(self,obs,space,value,label,wall,job=None):
        if not self.accepting:return
        if not value or str(value).lower() in {'unknown','none','null'}:return
        key=f"{obs['scope']}|{obs['subject']}|{obs['facet']}|{value}|{obs['reference_mode']}"
        hid=f"h:{self.s.clock.epoch}:{hashlib.sha256(key.encode()).hexdigest()[:20]}"
        h=Hypothesis(hid,space,obs['scope'],obs['subject'],obs['facet'],str(value)[:120],obs['reference_mode'],str(label)[:140])
        actual=self.s.fields.register(h)
        if actual is None:return
        try:self.s.enqueue_evidence(obs,self.s.fields.hypotheses[actual],wall,job=job)
        except ValueError as error:self._error('EvidenceEvaluator',error)

    def _error(self,agent,error):
        item={'agent':agent,'error':str(error),'media_s':self.s.clock.media_s}
        self.errors.append(item);self.errors[:]=self.errors[-12:]
        self.s.ledger.event('local_error',item)

    def _decode(self,epoch,start,video_cutoff,audio_window,scope):
        started=time.monotonic()
        frame=self.decoder.frame(start,video_cutoff)
        audio=self.decoder.audio(*audio_window) if audio_window else None
        motion=faces=measure=None
        if frame:
            motion=self.motion.measure(frame['image'],frame['media_s'],epoch)
            faces=self.faces.measure(frame['image'],f'e{epoch}-{scope}')
        if audio:
            measure=self.measure.measure(audio['samples'][-3200:],16000)
        return {'frame':frame,'audio':audio,'motion':motion,'faces':faces,'measure':measure,'epoch':epoch,
                'scope':scope,'started':started,'completed':time.monotonic()}

    def _submit(self,kind,data,context,wall,reader=False):
        if not self.accepting:return False
        if kind in self.pending:return False
        # Unaccepted latest-input slots: coalescing does not restart a deadline.
        # Dict insertion order gives ASR/image/face a fair turn; ASR's text
        # continuation is prioritized before accepting a newer ASR revision.
        old=self.waiting.get(kind)
        source_s=context.get('frame',{}).get('media_s',context.get('audio',{}).get('end_s'))
        if source_s is None and context.get('asr_id'):
            source_s=max(self.s.ledger.records[context['asr_id']]['citations'].values())
        timing={'slot_first_requested_wall_s':old[1]['timing']['slot_first_requested_wall_s'] if old else wall,
                'input_requested_wall_s':wall,'source_available_wall_s':context.get('source_available_wall_s',wall),
                'source_media_s':source_s,'input_cursor':len(self.s.ledger.events),
                'coalesced_count':old[1]['timing']['coalesced_count']+1 if old else 0}
        timing['epoch']=self.s.clock.epoch
        if kind=='SpeechASR' and 'audio' in context:
            audio=context['audio']
            # Log the actual admitted/coalesced input, without changing scheduling
            # or retaining PCM. End time is the decoder's existing end convention.
            timing['input_audio_interval']=[audio['start_s'],audio['end_s']]
            timing['input_sample_count']=len(audio['samples'])
            timing['input_sample_rate']=16000
            timing['input_audio_sample_refs']=[{k:chunk[k] for k in (
                'start_s','pts','time_base','media_origin_s','sample_offset','sample_rate',
                'container_pts','container_time_base','stream') if k in chunk} |
                {'sample_count':len(chunk['samples'])} for chunk in audio.get('chunks',[])]
            timing['input_id']=f"{self.s.ledger.session}:{self.s.clock.epoch}:asr-input:{len(self.s.ledger.events)+1}"
        if old:self.s.ledger.event('local_slot_coalesced',{'agent':kind,'old_input':old[1]['timing'],'replacement':timing})
        context={**context,'timing':timing}
        self.waiting[kind]=(data,context,reader)
        self.s.ledger.event('local_slot_requested',{'agent':kind,**timing},source_s)
        return True

    def _dispatch_next(self,wall):
        if not self.accepting:return
        if self.pending or not self.waiting:return
        kind='ContextReader' if 'ContextReader' in self.waiting else 'TextContext' if 'TextContext' in self.waiting else next(iter(self.waiting))
        data,context,reader=self.waiting[kind]
        if kind=='TextContext' and context['asr_id'] not in self.s.ledger.active:
            self.s.ledger.event('local_slot_discarded',{'agent':kind,'reason':'superseded_asr',**context['timing']})
            del self.waiting[kind];self.drop+=1;return
        if 'frame' in context and context['scope']!=self.s.fields.shot:
            self.s.ledger.event('local_slot_discarded',{'agent':kind,'reason':'old_scope',**context['timing']})
            del self.waiting[kind];self.drop+=1;return
        future=self.models.submit('FrameInterpreter' if kind in {'FaceExpression','ContextReader'} else kind,data,accepted=wall)
        if future is None:return
        del self.waiting[kind]
        context['timing'].update(accepted_wall_s=wall,slot_wait_s=wall-context['timing']['slot_first_requested_wall_s'],
                                latest_input_wait_s=wall-context['timing']['input_requested_wall_s'])
        self.s.ledger.event('local_model_admitted',{'agent':kind,**context['timing']})
        if not reader:self.s.admission.admit(wall)
        self.pending[kind]=(future,context,self.s.clock.epoch)

    def pump(self,wall=None):
        wall=time.monotonic() if wall is None else wall
        s=self.s
        if s.stopped:return
        if s.clock.epoch!=self.epoch:self.reset()
        self.collect(wall)
        if not self.accepting:return
        if s.clock.media_s>=self.end:
            s.stop();s.analysis_status='range_complete';return
        if s.clock.state!='playing' or self.models.status!='ready' or not s.clock.synchronized(wall):
            self._dispatch_next(wall)
            return
        if self.decode_future is None and s.clock.media_s-self.last_decode>=.19:
            video=s.clock.window(.2,'video');audio=s.clock.window(6,'audio') if self.asset.metadata['has_audio'] else None
            if video:
                self.last_decode=s.clock.media_s
                self.decode_future=self.cpu.submit(self._decode,s.clock.epoch,s.clock.epoch_start_media_s,video[1],audio,s.fields.shot)
        if s.reader_enabled and self.latest_frame and not self.pending and s.admission.reader_capacity(wall):
            job=s.reader.propose(s.fields.snapshot(s.clock.media_s),s.clock,wall,target_frame=self.latest_frame)
            if job:
                # The proposal explicitly requests the latest already-published frame.
                # It is a new physical source when roots differ, not a retimed old one.
                stamp=job['target_media_s']
                frame=self.decoder.frame(s.clock.epoch_start_media_s,stamp)
                if frame and frame['media_s']==stamp:
                    self._submit('ContextReader',{'image':frame['image'],'question':job['question']},{'frame':frame,'scope':s.fields.shot,'job':job},wall,reader=True)
        self._dispatch_next(wall)

    def collect(self,wall):
        s=self.s
        if self.decode_future and self.decode_future.done():
            future=self.decode_future;self.decode_future=None
            try:
                data=future.result()
                if data['epoch']!=s.clock.epoch:self.drop+=1
                else:self._decoded(data,wall)
            except Exception as error:self._error('PTSDecoder',error)
        for kind,(future,context,epoch) in list(self.pending.items()):
            if not future.done():continue
            del self.pending[kind]
            try:
                result=future.result()
                timing=context.get('timing',{})
                s.ledger.event('local_model_completed',{'agent':kind,'epoch':epoch,'accepted':result['accepted'],
                    'started':result['started'],'completed':result['completed'],'expired':result['expired'],
                    'output':result['output'],**timing,
                    'source_to_completion_wall_s':result['completed']-timing['source_available_wall_s'] if timing.get('source_available_wall_s') is not None else None,
                    'source_age_at_completion_media_s':s.clock.media_s-timing['source_media_s'] if timing.get('source_media_s') is not None else None})
                if epoch!=s.clock.epoch or result['expired']:
                    s.ledger.event('local_result_discarded',{'agent':kind,'reason':'old_epoch' if epoch!=s.clock.epoch else 'request_deadline',**timing})
                    self.drop+=1;continue
                if kind=='SpeechASR':self._asr(result['output'],context,wall)
                elif kind=='TextContext':self._text(result['output'],context,wall)
                else:self._visual(result['output'],context,wall)
            except Exception as error:
                s.ledger.event('local_model_failed',{'agent':kind,'reason':str(error),'collected_wall_s':wall,**context.get('timing',{})})
                self._error(kind,error)

    def _decoded(self,data,wall):
        s=self.s;frame=data['frame'];audio=data['audio']
        s.ledger.event('decode_completed',{'started_wall_s':data.get('started'),'completed_wall_s':data.get('completed'),
            'epoch':data.get('epoch'),'frame_id':frame['frame_id'] if frame else None,'frame_media_s':frame['media_s'] if frame else None,
            'audio_interval':[audio['start_s'],audio['end_s']] if audio else None},s.clock.media_s)
        if frame:
            if not s.clock.permits(frame['media_s'],frame['media_s'],'video',s.clock.epoch):raise ValueError('decoded_future_frame')
            previous=self.latest_frame;self.latest_frame=frame
            s.local_observations['MotionCut']={**data['motion'],'media_s':frame['media_s'],'origin':'LOCAL'}
            sources=video_sources(s,frame)
            if previous:
                for root,source in video_sources(s,previous).items():
                    if root in sources:sources[root]['interval'][0]=source['interval'][0]
                    else:sources[root]=source
            measurement=self._observation('MotionCut',str(data['motion']),sources,facet='video.motion',subject='video-stream',scope='session',reference_mode='depicted',basis='measurement',extra={'measurement':data['motion']})
            s.ledger.add(measurement,s.clock)
            if data['motion'].get('cut'):
                s.fields.cut(data['motion']['boundary_s'],s.clock.media_s)
                self.faces=FaceTrack()
            scope=s.fields.shot
            if data['scope']!=scope:return
            s.local_observations['FaceTrack']={**data['faces'],'media_s':frame['media_s'],'origin':'LOCAL'}
            if frame['media_s']-self.last_vlm>=2 and 'FrameInterpreter' not in self.pending:
                if self._submit('FrameInterpreter',{'image':frame['image']},{'frame':frame,'scope':scope,'source_available_wall_s':data.get('completed',wall)},wall):self.last_vlm=frame['media_s']
            tracks=data['faces'].get('tracks',{})
            if tracks and frame['media_s']-self.last_face>=2:
                subject,box=next(iter(tracks.items()));x,y,w,h=box
                sources=video_sources(s,frame)
                obs=self._observation('FaceTrack','匿名の顔領域を検出',sources,facet='person.presence',subject=subject,scope=scope,reference_mode='depicted',basis='detector',extra={'roi':box})
                s.ledger.add(obs,s.clock);self._candidate(obs,'person','face_present','匿名の顔が見える',wall)
                if 'FaceExpression' not in self.pending:
                    # Uses the same VLM worker/model; only a detected face can enter this path.
                    self._submit('FaceExpression',{'image':frame['image'][y:y+h,x:x+w].copy(),'face':True},
                        {'frame':frame,'scope':scope,'subject':subject,'roi':box,'face':True,'source_available_wall_s':data.get('completed',wall)},wall)
                self.last_face=frame['media_s']
        if audio:
            s.local_observations['AudioMeasure']={**data['measure'],'media_s':audio['end_s'],'origin':'LOCAL','source':'backend_pts_decode'}
            sources=audio_sources(s,audio)
            measurement=self._observation('AudioMeasure',str(data['measure']),sources,facet='audio.level',subject='audio-stream01',scope='session',reference_mode='recorded_sound',basis='measurement',extra={'measurement':data['measure'],'measurement_window_s':.2})
            s.ledger.add(measurement,s.clock)
            if audio['end_s']-self.last_asr>=1 and 'SpeechASR' not in self.pending and 'TextContext' not in self.pending:
                if self._submit('SpeechASR',audio,{'audio':audio,'source_available_wall_s':data.get('completed',wall)},wall):self.last_asr=audio['end_s']

    def _asr(self,out,context,wall):
        s=self.s;audio=context['audio'];sources=audio_sources(s,audio)
        s.local_observations['SpeechASR']={**out,'observed_s':[audio['start_s'],audio['end_s']],'origin':'LOCAL'}
        old=[i for i in s.ledger.active if s.ledger.records[i]['kind']=='observation' and s.ledger.records[i].get('agent')=='SpeechASR'
             and s.ledger.records[i].get('origin')=='LOCAL' and s.ledger.records[i]['observed_s'][1]>=audio['start_s']]
        if not out['text']:
            if old:s.ledger.invalidate(old,'asr_silence_revision')
            s.transcripts.ingest(out,audio,audio_sample_refs=[{'root':k,**v} for k,v in sources.items()])
            return
        citations={}
        for root,source in sources.items():
            a,b=source['interval']
            overlaps=[segment for segment in out['segments'] if segment['start_s']<=b and segment['end_s']>=a]
            if overlaps:citations[root]=min(b,max(segment['end_s'] for segment in overlaps))
        if not citations:raise ValueError('asr_no_valid_citation')
        self.asr_revision+=1
        obs=self._observation('SpeechASR',out['text'],sources,facet='conversation.transcript',subject='audio-stream01',scope='session',
             reference_mode='quoted_speech',basis='asr_transcript',evidence=citations,
             extra={'revision':self.asr_revision,'segments':out['segments'],'observed_s':[audio['start_s'],audio['end_s']],'supersedes':old})
        if old:s.ledger.replace(old,[obs],s.clock)
        else:s.ledger.add(obs,s.clock)
        self.latest_asr=obs
        s.transcripts.ingest(out,audio,obs['id'],obs['source_refs'])
        self._submit('TextContext',{'text':obs['text']},{'asr_id':obs['id'],'source_available_wall_s':wall},wall)

    def _text(self,out,context,wall):
        s=self.s;oid=context['asr_id']
        if oid not in s.ledger.active:
            s.ledger.event('local_result_discarded',{'agent':'TextContext','reason':'superseded_asr','observation_id':oid})
            self.drop+=1;return
        asr=s.ledger.records[oid]
        s.local_observations['TextContext']={**out,'asr_id':oid,'origin':'LOCAL'}
        rows=[('conversation.topic',out.get('topic'),'話題: '+str(out.get('topic')),'conversation','quoted_speech')]
        register=out.get('register')
        if register=='mixed':rows.extend([('conversation.register','casual','日常的な会話様式','conversation','quoted_speech'),('conversation.register','business','業務的な会話様式','conversation','quoted_speech')])
        else:rows.append(('conversation.register',register,{'casual':'日常的な会話様式','business':'業務的な会話様式'}.get(register,'不明'),'conversation','quoted_speech'))
        if out.get('mentioned_place') not in (None,'unknown',''):
            rows.append(('conversation.mentioned_place',out['mentioned_place'],'言及された場所: '+out['mentioned_place'],'conversation','mentioned'))
        for facet,value,label,space,mode in rows:
            if value in (None,'unknown',''):continue
            obs=self._observation('TextContext',f"{label}。引用: {out['quote']}",asr['root_sources'],facet=facet,subject='audio-stream01',scope='session',reference_mode=mode,
                dependencies={oid:asr['revision']},evidence=asr['citations'],extra={'quote':out['quote'],'asr_id':oid,'model_raw':out['raw']})
            s.ledger.add(obs,s.clock);self._candidate(obs,space,value,label,wall)

    def _visual(self,out,context,wall):
        s=self.s;frame=context['frame'];scope=context['scope']
        if scope!=s.fields.shot or s.fields.scope_at(frame['media_s'])!=scope:
            s.ledger.event('local_result_discarded',{'agent':'FrameInterpreter','reason':'old_scope','frame_id':frame['frame_id'],'scope':scope})
            self.drop+=1;return
        sources=video_sources(s,frame)
        s.local_observations['FrameInterpreter']={**out,'frame_id':frame['frame_id'],'media_s':frame['media_s'],'scope':scope,'origin':'LOCAL'}
        if context.get('face'):
            expression=out.get('expression')
            rows=[('person.face_expression',expression,{'smile':'笑顔に見える','angry_looking':'怒って見える','neutral':'中立的な表情に見える'}.get(expression,'不明'),'person',context['subject'])]
        else:
            rows=[('place.scene_type',out.get('scene'),str(out.get('scene')),'place',f'scene-{scope}'),
                  ('place.apparent_time_of_day',out.get('time_of_day'),{'day':'昼光の手掛かり','night':'夜の照明の手掛かり','twilight':'薄明の手掛かり'}.get(out.get('time_of_day'),'不明'),'place',f'scene-{scope}')]
            # A lighting observation can be meaningful while time of day remains
            # unknown. Evaluate this actual description in its own facet.
            if context.get('job') and out['description'].strip().lower() not in {'unknown','unclear','unclear lighting'}:
                rows.append(('place.visible_lighting',out['description'],out['description'],'place',f'scene-{scope}'))
        for facet,value,label,space,subject in rows:
            if value in (None,'unknown',''):continue
            obs=self._observation('FrameInterpreter',out['description'],sources,facet=facet,subject=subject,scope=scope,reference_mode='depicted',
                                  extra={'model_raw':out['raw'],'frame_id':frame['frame_id'],'roi':context.get('roi')})
            s.ledger.add(obs,s.clock);self._candidate(obs,space,value,label,wall,context.get('job'))

    def public(self):
        return {'models':self.models.public(),'pending':list(self.pending),'waiting_latest_input':list(self.waiting),'dropped':self.drop,'errors':self.errors,
                'slot_timings':{k:{**v[1]['timing'],'waiting_now_s':time.monotonic()-v[1]['timing']['slot_first_requested_wall_s']} for k,v in self.waiting.items()},
                'source':'backend_pts_decode','analysis_start_s':self.start,'analysis_end_s':self.end,
                'has_audio':self.asset.metadata['has_audio'],'evaluation':self.s.evaluation_mode}

    def cancel(self,reason):
        now=time.monotonic()
        for agent,(_,context,_) in self.pending.items():
            self.s.ledger.event('local_job_cancelled',{'agent':agent,'phase':'admitted','reason':reason,
                'cancelled_wall_s':now,**context.get('timing',{}),'running_inference_may_complete':True})
        for agent,(_,context,_) in self.waiting.items():
            self.s.ledger.event('local_job_cancelled',{'agent':agent,'phase':'pre_admission_slot','reason':reason,
                'cancelled_wall_s':now,**context.get('timing',{})})
        self.pending.clear();self.waiting.clear()

    def close(self):
        self.cancel('pipeline_closed')
        self.cpu.shutdown(wait=True,cancel_futures=True)

    def begin_drain(self,reason):
        self.accepting=False
        for kind,(_,context,_) in self.waiting.items():
            self.s.ledger.event('local_model_cancelled',{'agent':kind,'phase':'pre_admission_slot','reason':reason,**context.get('timing',{})})
        self.waiting.clear()
