"""Loopback-only local Viewer, same-origin token, explicit media allowlist.

HTTP polling replaces optional FastAPI/WebSockets; no additional dependency.
Selected media is copied to a dedicated localhost-only asset directory.
"""
import argparse
import base64
import hmac
import json
import mimetypes
import secrets
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from http.cookies import SimpleCookie
from .config import json_text
from .session import Session
from .replay import Replay, read_events
from .observers import ObserverPool, MotionCut, FaceTrack, AudioMeasure
from .media import AssetStore, MAX_UPLOAD
from .score_policy import C0, CD, policy_record
from .dispatch_policy import P1,load_policy

ROOT = Path(__file__).resolve().parents[1]

class App:
    def __init__(self, media=None, log_dir=None, load_models=False, live_capability=None, adoption_policy_id=C0, dispatch_policy=P1):
        self.dispatch_policy=dispatch_policy
        self.adoption_policy_id=policy_record(adoption_policy_id)['adoption_policy_id']
        self.live_capability=live_capability
        self.token = secrets.token_urlsafe(32)
        self.media = {"fixture": ROOT/"fixtures"/"synthetic.webm"}
        if media:
            path = Path(media).resolve(strict=True)
            if path.suffix.lower() not in {".mp4", ".webm", ".mov", ".m4v"} or not path.is_file():
                raise ValueError("unsupported_media_path")
            self.media["local"] = path
        self.log_dir = Path(log_dir) if log_dir else ROOT/"artifacts"/"sessions"
        self.assets = AssetStore(ROOT/"artifacts"/"media")
        self.models = None
        if load_models:
            from .local_models import LocalModels
            self.models = LocalModels()
        self.lock = threading.RLock()
        self.session = None
        self.replay = None
        self.pool = ObserverPool()
        self.motion, self.faces, self.audio = MotionCut(), FaceTrack(), AudioMeasure()
        self.pending = {}
        self.last_frame_s = self.last_audio_s = -1
        self.new_session("MOCK")

    def new_session(self, mode, asset_id=None, start=0):
        if mode not in {'LOCAL','MOCK'}:raise ValueError('invalid_source_mode')
        if asset_id and mode!='LOCAL':raise ValueError('real_asset_requires_local_mode')
        if mode == "LOCAL" and asset_id is None and "local" not in self.media:
            raise ValueError("no_explicit_local_media")
        asset = self.assets.get(asset_id) if asset_id else None
        if asset and (not isinstance(start,(int,float)) or not 0 <= start < asset.metadata['duration_s']):
            raise ValueError("invalid_analysis_start")
        if asset and self.models is None:
            raise ValueError("start_with_dedicated_local_models")
        if self.session:
            self.session.stop()
            self.session.close_remote()
            if self.session.local_pipeline:self.session.local_pipeline.close()
        sid = f"run-{time.time_ns()}"
        self.session = Session(sid, mode, self.log_dir/f"{sid}.jsonl", adoption_policy_id=self.adoption_policy_id,dispatch_policy=self.dispatch_policy)
        if self.live_capability:self.session.campaign_budget=self.live_capability.budget
        if asset:
            from .local_pipeline import LocalPipeline
            self.session.local_pipeline = LocalPipeline(self.session,asset,self.models,start)
        self.motion.reset()
        self.pending.clear()
        self.last_frame_s = self.last_audio_s = -1
        return self.session.view()

    def collect(self):
        for key, (future, epoch, stamp) in list(self.pending.items()):
            if not future.done():
                continue
            del self.pending[key]
            if epoch != self.session.clock.epoch:
                continue
            try:
                output = future.result()
                output.update(media_s=stamp, mode="LOCAL", original_pts=None, pts_reason="browser_timestamp_only")
                self.session.local_observations[key] = output
                self.session.ledger.event("local_measurement", {"agent": key, "result": output}, stamp)
                if key == "MotionCut" and output.get("cut") and self.session.mode == "LOCAL":
                    self.session.fields.cut(output["boundary_s"], self.session.clock.media_s)
            except Exception as error:
                self.session.local_observations[key] = {"status": "error", "reason": type(error).__name__}

    def observe(self, body):
        s = self.session
        if s.local_pipeline:raise ValueError("backend_decode_is_sole_analysis_source")
        if s.stopped or s.clock.state != "playing" or body.get("epoch") != s.clock.epoch:
            raise ValueError("observation_state")
        stamp = body["media_s"]
        if not s.clock.permits(stamp, stamp, "video", s.clock.epoch):
            raise ValueError("frame_not_released")
        if stamp-self.last_frame_s < .095:
            return {"status": "throttled"}
        import cv2
        import numpy as np
        encoded = base64.b64decode(body["jpeg"], validate=True)
        if len(encoded) > 150000:
            raise ValueError("image_size")
        frame = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None or frame.shape[0] > 360 or frame.shape[1] > 640:
            raise ValueError("image_dimensions")
        self.last_frame_s = stamp
        jobs = [("MotionCut", self.motion.measure, (frame, stamp, s.clock.epoch)),
                ("FaceTrack", self.faces.measure, (frame, f"e{s.clock.epoch}-{s.fields.shot}"))]
        for name, fn, args in jobs:
            if name not in self.pending:
                future = self.pool.submit(name, fn, *args)
                if future:
                    self.pending[name] = (future, s.clock.epoch, stamp)
        return {"status": "accepted", "basis": "real_pixels", "semantic_evaluation": "disabled"}

    def measure_audio(self, body):
        s = self.session
        if s.local_pipeline:raise ValueError("backend_decode_is_sole_analysis_source")
        start, end = body["start_s"], body["end_s"]
        if s.stopped or s.clock.state != "playing" or not s.clock.permits(start,end,"audio",body["epoch"]):
            raise ValueError("audio_not_released")
        if end-self.last_audio_s < .19 or "AudioMeasure" in self.pending:
            return {"status": "throttled"}
        self.last_audio_s = end
        future = self.pool.submit("AudioMeasure", self.audio.measure, body["samples"], body["sample_rate"])
        if future:
            self.pending["AudioMeasure"] = (future,s.clock.epoch,end)
        return {"status": "accepted"}

class Handler(BaseHTTPRequestHandler):
    server_version = "ContextFields/0.2"

    @property
    def app(self):
        return self.server.app

    def log_message(self, *args):
        pass  # Never log token, paths, text, credentials, or request bodies.

    def allowed(self, token=False):
        host = self.headers.get("Host", "")
        expected = f"127.0.0.1:{self.server.server_port}"
        if host != expected or self.client_address[0] != "127.0.0.1":
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin != f"http://{expected}":
            return False
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            return False
        if token and not hmac.compare_digest(self.headers.get("X-Session-Token", ""), self.app.token):
            return False
        return True

    def reply(self, status, body, content_type="application/json; charset=utf-8"):
        data = json_text(body).encode() if not isinstance(body, bytes) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if getattr(self, "bootstrap_cookie", False):
            self.send_header("Set-Cookie", f"context_session={self.app.token}; HttpOnly; SameSite=Strict; Path=/")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self.allowed():
            return self.reply(403, {"error": "origin_or_host"})
        path = urlsplit(self.path).path
        if path == "/api/bootstrap":
            self.bootstrap_cookie = True
            return self.reply(200, {"token": self.app.token, "local_media": "local" in self.app.media,
                                    "session": self.app.session.ledger.session, "live_enabled": self.app.live_capability is not None,
                                    "local_models": self.app.models.public() if self.app.models else None, "max_upload_bytes": MAX_UPLOAD})
        static = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}
        if path in static:
            target = ROOT/"web"/static[path]
            return self.reply(200, target.read_bytes(), mimetypes.guess_type(target.name)[0] + "; charset=utf-8")
        if path.startswith("/media/"):
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            value = cookie.get("context_session")
            if not value or not hmac.compare_digest(value.value, self.app.token):
                return self.reply(403, {"error": "session_token"})
            return self.media(path.removeprefix("/media/"))
        if not self.allowed(token=True):
            return self.reply(403, {"error": "session_token"})
        if path == "/api/models":
            return self.reply(200, self.app.models.public() if self.app.models else {"status":"not_loaded"})
        with self.app.session.ledger.lock:
            if path == "/api/snapshot":
                self.app.collect()
                return self.reply(200, self.app.session.view(record=False))
            if path.startswith("/api/evidence/"):
                from urllib.parse import unquote
                rid = unquote(path.removeprefix("/api/evidence/"))
                record = self.app.session.ledger.records.get(rid)
                from urllib.parse import parse_qs
                query=parse_qs(urlsplit(self.path).query)
                if 'replay_cursor' in query:
                    try:cursor=int(query['replay_cursor'][0])
                    except ValueError:return self.reply(400,{'error':'replay_cursor'})
                    record=self.app.replay.record_at(rid,cursor) if self.app.replay else None
                return self.reply(200 if record else 404, record or {"error": "not_found"})
            if path == "/api/export":
                data = "\n".join(json_text(e) for e in self.app.session.ledger.events).encode()
                return self.reply(200, data, "application/x-ndjson")
        self.reply(404, {"error": "not_found"})

    def media(self, opaque_id):
        path = self.app.media.get(opaque_id)
        if path is None and opaque_id in self.app.assets.assets:
            path = self.app.assets.get(opaque_id).path
        if path is None or not path.is_file():
            return self.reply(404, {"error": "media_not_allowed"})
        size = path.stat().st_size
        start, end = 0, size-1
        value = self.headers.get("Range")
        if value:
            import re
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", value)
            if not match or not any(match.groups()):
                return self.reply(416, {"error": "range"})
            a,b = match.groups()
            if a:
                start, end = int(a), min(int(b),end) if b else end
            else:
                start = max(0,size-int(b))
            if start > end or start >= size:
                return self.reply(416, {"error": "range"})
        self.send_response(206 if value else 200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(end-start+1))
        self.send_header("Accept-Ranges", "bytes")
        if value:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with path.open("rb") as f:
                f.seek(start)
                remaining = end-start+1
                while remaining:
                    data = f.read(min(65536,remaining))
                    if not data:
                        break
                    self.wfile.write(data)
                    remaining -= len(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_POST(self):
        if not self.allowed(token=True):
            return self.reply(403, {"error": "origin_host_or_token"})
        try:
            if self.headers.get("Transfer-Encoding"):
                raise ValueError("chunked_not_supported")
            length = int(self.headers.get("Content-Length", "0"))
            path = urlsplit(self.path).path
            if path == "/api/assets":
                if self.headers.get_content_type() != "application/octet-stream":
                    raise ValueError("media_content_type")
                from urllib.parse import unquote
                try:
                    asset = self.app.assets.import_stream(self.rfile,length,unquote(self.headers.get("X-File-Name","")))
                    return self.reply(200, asset.public())
                except Exception as error:
                    self.close_connection = True
                    return self.reply(400,{"error":"media_import_or_decode_failed: "+str(error)})
            if not 0 < length <= 1_000_000 or self.headers.get_content_type() != "application/json":
                raise ValueError("input_size_or_type")
            body = json.loads(self.rfile.read(length), parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
            with self.app.lock, self.app.session.ledger.lock:
                if path == "/api/session":
                    return self.reply(200, self.app.new_session(body["mode"],body.get("asset_id"),body.get("start_s",0)))
                if path == '/api/live/start':
                    if self.app.live_capability is None:raise ValueError('live_not_authorized')
                    if not self.app.session.media_info or body.get('asset_id')!=self.app.session.media_info['id']:
                        raise ValueError('explicit_selected_asset_required')
                    self.app.session.enable_live(self.app.live_capability)
                    return self.reply(200,self.app.session.view())
                if path == '/api/assets/browser-ready':
                    from .clock import finite
                    asset=self.app.assets.get(body['asset_id'])
                    if abs(finite(body['duration_s'])-asset.metadata['duration_s'])>.25:
                        raise ValueError('browser_backend_duration_mapping_mismatch')
                    asset.metadata['browser_decode']='metadata_ready'
                    if self.app.session.media_info and self.app.session.media_info['id']==asset.id:
                        self.app.session.media_info['browser_decode']='metadata_ready'
                        self.app.session.ledger.event('browser_media_metadata',{'asset_id':asset.id,'duration_s':body['duration_s']})
                    return self.reply(200,{'status':'metadata_ready'})
                if path == "/api/assets/delete":
                    if self.app.session.media_info and self.app.session.media_info['id'] == body['asset_id']:
                        self.app.new_session('MOCK')
                    self.app.assets.delete(body['asset_id'])
                    return self.reply(200,{'status':'deleted_local_copy'})
                if path == "/api/shutdown":
                    self.app.session.stop()
                    threading.Thread(target=self.server.shutdown,daemon=True).start()
                    return self.reply(200,{'status':'shutting_down'})
                if path == "/api/clock":
                    receipt=body.pop('viewer_receipt',None)
                    if receipt is not None:
                        from .clock import finite
                        cursor=receipt.get('display_event_cursor')
                        if type(cursor) is not int or not 1<=cursor<=len(self.app.session.ledger.events):raise ValueError('viewer_receipt_cursor')
                        timings={k:finite(receipt[k]) for k in ('roundtrip_ms','render_ms')}
                        if any(not 0<=v<=60000 for v in timings.values()):raise ValueError('viewer_receipt_timing')
                        self.app.session.ledger.event('viewer_receipt',{'display_event_cursor':cursor,**timings,
                            'clock_domain':'browser_duration_only'},self.app.session.clock.media_s)
                    self.app.collect()
                    return self.reply(200, self.app.session.tick(body))
                if path == "/api/control":
                    if body["epoch"] != self.app.session.clock.epoch:
                        raise ValueError("old_epoch")
                    action = body["action"]
                    if action == "seek":
                        pipeline=self.app.session.local_pipeline
                        if pipeline and not pipeline.start <= body['target'] <= pipeline.end:
                            raise ValueError('seek_outside_analysis_range')
                        self.app.session.seek(body["target"])
                        self.app.last_frame_s = self.app.last_audio_s = -1
                    elif action == "stop":
                        self.app.session.stop()
                    elif action == "reader":
                        self.app.session.reader_enabled = bool(body["enabled"])
                    elif action == "subject":
                        self.app.session.selected_subject = str(body["subject"])
                    else:
                        raise ValueError("control_action")
                    return self.reply(200, self.app.session.view())
                if path == "/api/observe":
                    return self.reply(200, self.app.observe(body))
                if path == "/api/audio":
                    return self.reply(200, self.app.measure_audio(body))
                if path == "/api/replay":
                    if body.get('run_id'):
                        import re
                        run_id=body['run_id']
                        if not isinstance(run_id,str) or not re.fullmatch(r'run-\d{1,20}',run_id):raise ValueError('invalid_run_id')
                        log=(self.app.log_dir/(run_id+'.jsonl')).resolve()
                        if log.parent!=self.app.log_dir.resolve() or not log.is_file() or log.stat().st_size>512*1024**2:raise ValueError('replay_log_unavailable')
                        self.app.session.stop()
                        self.app.replay=Replay(read_events(log))
                    if body.get("capture"):
                        self.app.session.stop()
                        self.app.replay = Replay(self.app.session.ledger.events)
                    if not self.app.replay:
                        raise ValueError("no_replay")
                    frames = self.app.replay.frames
                    index = min(max(int(body.get("index",0)),0),max(0,len(frames)-1))
                    frame = self.app.replay.at(event_cursor=frames[index]["event_seq"]) if frames else None
                    if frame:
                        frame["mode"] = "REPLAY"
                        frame['replay_event_cursor']=frames[index]['event_seq']
                    return self.reply(200, {"frame": frame, "count": len(frames), "index": index})
            self.reply(404, {"error": "not_found"})
        except (ValueError, TypeError, KeyError) as error:
            self.reply(400, {"error": str(error)})

def make_server(port=8876, media=None, log_dir=None, load_models=False, live_capability=None, adoption_policy_id=C0,dispatch_policy=P1):
    server = ThreadingHTTPServer(("127.0.0.1",port), Handler)
    server.daemon_threads = True
    server.app = App(media, log_dir, load_models, live_capability, adoption_policy_id,dispatch_policy)
    return server

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8876)
    parser.add_argument("--media", help="Explicit local video path; never scans other folders")
    parser.add_argument("--local-models", action="store_true", help="Load installed ASR and shared VLM in offline mode")
    parser.add_argument('--approved-live-v02',action='store_true',help='Use only AFTER explicit approval of docs/LIVE_APPROVAL_JA.md')
    parser.add_argument('--live-phase',choices=['initial','demo'],default='initial')
    parser.add_argument('--adoption-policy',choices=[C0,CD],default=C0)
    parser.add_argument('--dispatch-policy',help='Explicit versioned scheduler JSON; campaign/authorization caps still apply')
    args = parser.parse_args()
    capability=None
    if args.approved_live_v02:
        from .live import LiveCapability
        capability=LiveCapability(approved=True,phase=args.live_phase)
    server = make_server(args.port, args.media, load_models=args.local_models,live_capability=capability,adoption_policy_id=args.adoption_policy,dispatch_policy=load_policy(args.dispatch_policy) if args.dispatch_policy else P1)
    print(f"Viewer: http://127.0.0.1:{server.server_port} | LIVE capability {'enabled; explicit asset start required' if capability else 'disabled'}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.app.session.stop()
        server.app.session.close_remote()
        server.app.pool.close()
        if server.app.session.local_pipeline:server.app.session.local_pipeline.close()
        if server.app.models:server.app.models.close()
        if capability:capability.close()
        server.server_close()

if __name__ == "__main__":
    main()
