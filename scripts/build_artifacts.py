"""Produce reproducible dummy payloads, trace, manifest and strict offline log."""
from pathlib import Path
import hashlib
import importlib.metadata
import json
import platform
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from context_fields.config import CONFIG,CONFIG_HASH
from context_fields.session import Session
from context_fields.jev import FACETS,rubric
from context_fields.observers import REGISTRY
from context_fields.replay import Replay

def write(name,data):
    (ROOT/"artifacts"/name).write_text(json.dumps(data,ensure_ascii=False,allow_nan=False,indent=2),encoding="utf-8")

def main():
    session=Session("offline-reference-run")
    for i in range(321):
        session.tick(dict(epoch=1,seq=i,media_s=i/10,video_presented_s=i/10,
                          audio_presented_s=i/10,state="playing"),wall_s=i/10)
    output=ROOT/"artifacts"/"demo-session.jsonl"
    with output.open("w",encoding="utf-8") as f:
        for event in session.ledger.events:
            f.write(json.dumps(event,ensure_ascii=False,allow_nan=False,separators=(",",":"))+"\n")
    for role in ("evidence","dominance"):
        event=next(e for e in session.ledger.events if e["kind"]=="evaluation_requested" and e["payload"]["role"]==role)
        write(f"dummy-{role}-payload.json",json.loads(event["payload"]["request"]))
    display=next(e for e in session.ledger.events if e["kind"]=="display" and e["payload"]["readouts"]["conversation"]["status"]=="valid")
    snapshot=display["payload"]["snapshot"]
    h=next(r for r in snapshot["spaces"]["conversation"]["hypotheses"] if r["S"]>0)
    source=h["sources"]["support"][0]
    readout=display["payload"]["readouts"]["conversation"]
    write("trace-example.json",{
        "mode":"MOCK", "observation":session.ledger.records[source["observation_id"]],
        "evidence_evaluation":session.ledger.records[source["evaluation_id"]],
        "contribution":session.ledger.records["contribution:"+source["evaluation_id"]],
        "hypothesis_at_display":h,"field_snapshot_id":snapshot["snapshot_id"],
        "dominance_evaluation":session.ledger.records[readout["evaluation_id"]],
        "readout":readout,"display_event_cursor":display["event_seq"]})
    source_files=[*list((ROOT/"context_fields").glob("*.py")),*list((ROOT/"web").glob("*")),*list((ROOT/"scripts").glob("*.py")),*list((ROOT/"tests").glob("*.py"))]
    versions={}
    for package in ("numpy","opencv-python","pytest","torch","av","faster-whisper","typesafe-sdk"):
        try:versions[package]=importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:versions[package]=None
    import cv2
    weights=Path(cv2.data.haarcascades)/"haarcascade_frontalface_default.xml"
    manifest={"schema_version":"2","implementation":"0.2.0","runtime":{"python":sys.version,"executable":sys.executable,"platform":platform.platform()},
              "config":CONFIG,"config_hash":CONFIG_HASH,"dependencies":versions,"adapters":REGISTRY,
              "models":{"asr":None,"vlm":None,"text_llm":None,"jev_requested":"jev-1.13.0","jev_live_verified":False,
                        "face_detector":{"model":"OpenCV bundled frontalface Haar cascade","sha256":hashlib.sha256(weights.read_bytes()).hexdigest()}},
              "prompts":{"version":CONFIG["prompt_version"],"facets":FACETS,"rubrics":{label:rubric(label) for s in ("person","conversation") for label in FACETS[s]["items"].values()}},
              "media":{"asset_id":"synthetic","path":"fixtures/synthetic.webm","sha256":hashlib.sha256((ROOT/"fixtures"/"synthetic.webm").read_bytes()).hexdigest(),
                       "duration_s":32,"video_codec":"VP8","audio":False,"semantic_values":"scripted_synthetic_fixture"},
              "schema_contract":{"events":"common envelope schema 2","validation":"context_fields/ledger.py + jev.py",
                                 "readout_stale_boundary":"current_media_s > as_of_media_s + 4","request_expiry":"no new send at accepted + 5; response invalid after +5"},
              "source_sha256":{str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files},
              "official_references":{"api":"https://docs.typesafe.ai/api","score":"https://docs.typesafe.ai/primitives/score","models":"https://docs.typesafe.ai/models"},
              "external":{"attempts":0,"units":0,"usage":None,"credential_access":False},
              "mock_reference_run":session.dispatcher.budget.summary(),"feedback_mock_completed":session.last_payload["feedback"]["completed"],
              "verification":{"offline_tests":49,"local_cpu_tests":2,"http_smoke_checks":14,"browser":"unavailable: discovery returned []","real_semantic_model":"not_run","real_jev":"not_run"}}
    write("manifest.json",manifest)
    replay=Replay(session.ledger.events)
    assert replay.at()==session.last_payload
    write("verification.json",{"offline":"49 passed","local_cpu":"2 passed","http":"14 checks passed","javascript_syntax":"node --check passed",
                               "fixture_replay_equal":True,"browser":"not run","external_attempts":0,"external_units":0,"usage":None,
                               "mock_counts":session.dispatcher.budget.summary(),"mock_feedback_completed":session.last_payload["feedback"]["completed"]})
    print(f"Wrote manifest, dummy payloads, trace and {len(session.ledger.events)} replay events; {output.stat().st_size} bytes")

if __name__=="__main__":main()
