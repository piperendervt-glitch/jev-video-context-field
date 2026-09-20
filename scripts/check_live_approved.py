"""Explicit approved connection check with public, synthetic contract text only.

Two real roles, no video / ASR / personal input. Debits shared campaign cap.
"""
import argparse
import json
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--approved-live-v02',action='store_true');args=parser.parse_args()
    if not args.approved_live_v02:raise PermissionError('explicit_approval_required')
    from context_fields.live import LiveCapability
    from context_fields.session import Session
    from context_fields.replay import Replay
    cap=LiveCapability(approved=True,phase='initial')
    report={'purpose':'initial connection; synthetic non-personal text; not user-video acceptance','responses':[]}
    try:
        transport=cap.transport()
        s=Session('live-connection-contract',mode='MOCK')
        s.clock.notify(s.clock.epoch,1,2,2,'playing',0,audio_presented_s=2)
        obs,h=s.observation(1,'place','day','Synthetic connection-check description: bright daylight is visible through a window. This is test data, not an observation of a real video.')
        request=s.evidence.build([obs],[h]);generation=s.evidence.generation('connection')
        cap.budget.reserve(request);accepted=time.monotonic()
        raw=transport.send(request,5);completed=time.monotonic()
        report['responses'].append({'role':'evidence','request':json.loads(request.payload_json),'raw':raw,'elapsed_s':completed-accepted})
        evaluated=s.evidence.accept(request,raw,[h],generation,'connection',accepted,completed,2,mode='LIVE-JEV')
        if not evaluated[0]:raise ValueError('evidence_contract_rejected')
        s.fields.contribute(evaluated[0]['id'],h.id,2,completed)
        snap=s.fields.snapshot(2);request=s.dominance.build(snap)
        if request is None:raise ValueError('no_dominance_input_after_evidence')
        cap.budget.reserve(request);accepted=time.monotonic()
        raw=transport.send(request,5);completed=time.monotonic()
        report['responses'].append({'role':'dominance','request':json.loads(request.payload_json),'raw':raw,'elapsed_s':completed-accepted})
        outputs=s.dominance.accept(request,raw,snap,s.clock,s.fields.scope_revision,accepted,completed,mode='LIVE-JEV')
        if any(r['status']=='error' for r in outputs):raise ValueError('dominance_contract_rejected')
        s.view();replay=Replay(s.ledger.events)
        report.update(status='passed',replay_frames=len(replay.frames),snapshot=snap.data(),readouts=outputs)
    except Exception as error:
        report.update(status='failed',error=type(error).__name__+': '+str(error))
    finally:
        report['campaign']=cap.budget.summary();cap.close()
        (ROOT/'artifacts/live-connection-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:report[k] for k in ('status','campaign')}|({'error':report['error']} if 'error'in report else {})))
    if report['status']!='passed':raise SystemExit(1)

if __name__=='__main__':main()
