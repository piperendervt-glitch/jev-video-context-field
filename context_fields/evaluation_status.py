"""Saved-event-only progress projection; sticky explicit stop reasons, no gap inference."""
import copy
import json

class EvaluationStatus:
    def __init__(self,db):
        self.db=db
        self.value={'state':'unknown','label':'評価状況の記録なし','event_seq':None,'reason':None}
        self.fatal=False
        db.executescript('DROP TABLE IF EXISTS evaluation_status; CREATE TABLE evaluation_status(seq INTEGER PRIMARY KEY,body TEXT);')

    def consume(self,e):
        p=e['payload'];kind=e['kind'];state=label=reason=None
        if kind=='jev_lifecycle' and p.get('stage')=='global_stop' and p.get('hard_error'):
            self.fatal=True;state='stopped';reason=p.get('reason','unknown');label='Jev停止：'+('確率合計が不正' if 'probability_sum' in reason else reason)
        elif kind=='dispatcher_result' and p.get('status')=='error':
            self.fatal=True;state='stopped';reason=p.get('reason','unknown')
            label='停止 · '+('予算上限' if p['status']=='budget_stop' else 'Score整合性' if 'score_expectation' in reason else '応答形式' if 'live_readout_schema' in reason else reason)
        elif not self.fatal and self.value['state']!='ended':
            if kind=='evaluation_end':
                reason=p.get('reason');state='ended';label='終了 · '+{'range_complete':'指定区間終了','manual_stop':'手動停止'}.get(reason,reason or '理由未記録')
            elif kind=='stop':state='stopped';label='停止 · 理由記録なし';reason='stop'
            elif kind=='evaluation_requested':state='waiting';label='評価待ち'
            elif kind=='dispatcher_result' and p.get('status')=='accepted':state='active';label='評価進行中'
            elif kind=='player_release' and p.get('state')=='paused':state='paused';label='一時停止'
        if state:
            # Normal shutdown must not erase the explicit end of an interval.
            if kind=='stop' and self.value['state']=='ended':return
            self.value={'state':state,'label':label,'reason':reason,'event_seq':e['event_seq'],'event_kind':kind,'source':copy.deepcopy(p),'hard_error':self.fatal}
            self.db.execute('INSERT INTO evaluation_status VALUES (?,?)',(e['event_seq'],json.dumps(self.value,ensure_ascii=False)))

def at(db,cursor):
    row=db.execute('SELECT body FROM evaluation_status WHERE seq<=? ORDER BY seq DESC LIMIT 1',(cursor,)).fetchone()
    return json.loads(row[0]) if row else {'state':'unknown','label':'評価状況の記録なし','event_seq':None}
