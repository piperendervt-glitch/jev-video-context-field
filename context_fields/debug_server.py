"""Non-LIVE H0 entry point. No Session/LiveCapability/model/real-budget objects."""
import argparse
import json
import mimetypes
import secrets
import sys
import threading
import socket
import subprocess
import math
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
import hmac
from .server import Handler, ROOT
from .debug_review import DebugReview, frame_at
from .media import MAX_UPLOAD
from .activity import page as activity_page, row_at as activity_row
from .evaluation_lists import page as evaluation_lists_page


def install_readonly_guard():
    def guard(event, args):
        if event == 'open' and isinstance(args[0], (str, bytes)):
            name = str(args[0]).replace('\\', '/').lower()
            if name.rsplit('/', 1)[-1] == '.env' or name.endswith('/.env.local'):
                raise PermissionError('H0_no_env_read')
            if 'jev-campaign-v02' in name and (args[1] and any(c in args[1] for c in 'wax+') or args[2] & 3):
                raise PermissionError('H0_no_campaign_write')
        if event == 'socket.connect' and args[1][0] not in ('127.0.0.1', '::1'):
            raise PermissionError('H0_no_external_connection')
    sys.addaudithook(guard)


class DebugApp:
    def __init__(self, root=ROOT, fixture=False):
        self.token = secrets.token_urlsafe(32)
        self.review = DebugReview(root, fixture)
        self.assets = self.review.assets
        self.media = {}
        self.local_child = None
        self.local_child_lock = threading.Lock()

    def start_local(self, body):
        if body.get('mode_confirmation') != 'LOCAL_REAL_EVAL_MOCK':
            raise ValueError('explicit_local_mock_start_required')
        asset = self.assets.get(body['asset_id'])
        start = body.get('start_s', 0)
        if type(start) not in (float, int) or not math.isfinite(start) or not 0 <= start < asset.metadata['duration_s']:
            raise ValueError('analysis_start')
        with self.local_child_lock:
            if self.local_child and self.local_child.poll() is None:
                raise ValueError('local_analysis_already_running_use_existing_tab')
            with socket.socket() as probe_socket:
                try:
                    probe_socket.bind(('127.0.0.1', 8877))
                except OSError:
                    raise ValueError('port_8877_in_use_no_process_stopped') from None
            directory = ROOT / 'artifacts/human-debug-h0/local-start'
            directory.mkdir(parents=True, exist_ok=True)
            with (directory / 'stdout.log').open('ab') as stdout, (directory / 'stderr.log').open('ab') as stderr:
                self.local_child = subprocess.Popen([str(ROOT / '.venv/Scripts/python.exe'), '-X', 'utf8', '-m',
                    'context_fields.debug_local_server', '--asset-id', asset.id, '--asset-sha256', asset.sha256,
                    '--start', str(start), '--port', '8877'], cwd=ROOT, stdout=stdout, stderr=stderr,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            return {'status': 'starting', 'mode': 'LOCAL_REAL_EVAL_MOCK', 'url': 'http://127.0.0.1:8877/',
                    'pid': self.local_child.pid, 'analysis_interval': [start, min(asset.metadata['duration_s'], start + 120)],
                    'new_jev_allowed': False, 'instruction': 'Open the local tab after startup; wait for model ready, then explicitly play.'}


class DebugHandler(Handler):
    def do_GET(self):
        if not self.allowed():
            return self.reply(403, {'error': 'origin_or_host'})
        path = urlsplit(self.path).path
        query = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
        if path == '/api/bootstrap':
            self.bootstrap_cookie = True
            return self.reply(200, {'token': self.app.token, 'debug': True, 'session': 'H0 READ-ONLY', 'live_enabled': False,
                                    'max_upload_bytes': MAX_UPLOAD, 'build': self.app.review.build})
        static = {'/': 'index.html', '/app.js': 'app.js', '/debug.js': 'debug.js', '/style.css': 'style.css', '/debug.css': 'debug.css',
                  '/activity.js': 'activity.js', '/activity.css': 'activity.css', '/activity-view.js': 'activity-view.js'}
        if path in static:
            target = ROOT / 'web' / static[path]
            return self.reply(200, target.read_bytes(), mimetypes.guess_type(target.name)[0] + '; charset=utf-8')
        if path.startswith('/media/'):
            cookie = SimpleCookie(self.headers.get('Cookie', ''))
            value = cookie.get('context_session')
            if not value or not hmac.compare_digest(value.value, self.app.token):
                return self.reply(403, {'error': 'session_token'})
            return self.media(path.removeprefix('/media/'))
        if not self.allowed(token=True):
            return self.reply(403, {'error': 'session_token'})
        r = self.app.review
        try:
            if path == '/api/debug/runs':
                result = {'items': r.index.catalog()}
            elif path == '/api/debug/activity':
                result = activity_page(r.index, query['run'], query['position'], query.get('offset', 0))
            elif path == '/api/debug/evaluation-lists':
                result = evaluation_lists_page(r.index, query['run'], query['position'],
                    query.get('log_offset', 0), query.get('evaluation_offset', 0),
                    query.get('log_anchor', ''), query.get('evaluation_anchor', ''), query.get('keep', ''))
            elif path == '/api/debug/activity-row':
                result = activity_row(r.index, query['run'], query['cursor'], query['row_id'])
            elif path == '/api/debug/thumbnail':
                result = r.thumbnails.get(query['run'], query['cursor'], query['row_id'], query['revision'],
                                          query['ref_id'], query['asset_id'], query['generation'])
            elif path == '/api/debug/status':
                result = r.index.status(query['run'])
            elif path == '/api/debug/display':
                result = r.index.display(query['run'], query['cursor'])
            elif path == '/api/debug/adjacent-display':
                result = r.index.adjacent_display(query['run'], query['cursor'], query['direction'])
            elif path == '/api/debug/events':
                result = r.index.events(query['run'], query['cursor'], query.get('offset', 0), query.get('kind', ''), query.get('limit', 50))
            elif path == '/api/debug/event':
                seq = int(query['seq'])
                if seq > int(query['cursor']):
                    raise ValueError('future_event')
                result = r.index.event(query['run'], seq)
            elif path == '/api/debug/targets':
                result = r.index.targets(query['run'], query['cursor'], query.get('offset', 0), query.get('kind', ''), query.get('query', ''))
            elif path == '/api/debug/target':
                result = r.target(query['run'], query['cursor'], query['key'])
            elif path == '/api/debug/timeline':
                result = r.index.timeline(query['run'], query['cursor'])
            elif path == '/api/debug/match':
                result = r.match(query.get('run'), query.get('asset_id'))
            elif path == '/api/debug/frame':
                result = frame_at(r.assets.get(query['asset_id']), float(query['time']), query.get('direction', 'at'),
                                  int(query['stream']) if query.get('stream') is not None else None)
            elif path == '/api/debug/evidence-frame':
                result = r.evidence_frame(query['run'], query['cursor'], query['key'], query['asset_id'])
            elif path == '/api/debug/reviews':
                result = {'items': r.reviews.list(), 'issues': r.reviews.issues()}
            elif path == '/api/debug/capabilities':
                result = r.capabilities()
            elif path == '/api/debug/checks':
                result = r.checks()
            elif path == '/api/debug/diagnostics':
                result = r.diagnostics()
            elif path == '/api/debug/local-status':
                child = self.app.local_child
                result = {'status': 'not_started' if child is None else 'running' if child.poll() is None else 'exited',
                          'exit_code': child.poll() if child else None, 'mode': 'LOCAL_REAL_EVAL_MOCK'}
            elif path.startswith('/api/evidence/'):
                result = r.index.record(query['run'], query['replay_cursor'], unquote(path.removeprefix('/api/evidence/')))
                result = result['body'] if result else {'status': 'LEGACY_MISSING'}
            else:
                return self.reply(404, {'error': 'readonly_debug_route_only'})
            return self.reply(200, result)
        except (ValueError, KeyError, TypeError) as error:
            return self.reply(400, {'error': str(error) if isinstance(error, ValueError) else 'invalid_request'})
        except Exception as error:
            return self.reply(500, {'error': type(error).__name__})

    def do_POST(self):
        if not self.allowed(token=True):
            return self.reply(403, {'error': 'origin_host_or_token'})
        r = self.app.review
        try:
            if self.headers.get('Transfer-Encoding'):
                raise ValueError('transfer_encoding_unsupported')
            size = int(self.headers.get('Content-Length', 0))
            path = urlsplit(self.path).path
            if path == '/api/assets':
                if not 0 < size <= MAX_UPLOAD:
                    raise ValueError('media_size_limit_2GiB')
                return self.reply(200, r.assets.import_stream(self.rfile, size, unquote(self.headers.get('X-File-Name', ''))).public())
            if not 0 < size <= 100000:
                raise ValueError('request_size')
            body = json.loads(self.rfile.read(size))
            if path == '/api/debug/index':
                result = r.index.start(body['run'])
            elif path == '/api/debug/cancel':
                result = r.index.cancel(body['run'])
            elif path == '/api/debug/managed-asset':
                result = r.register_managed(body['run'])
            elif path == '/api/debug/pin':
                result = r.pin(body)
            elif path == '/api/debug/save':
                result = r.reviews.save(body)
            elif path == '/api/debug/issue':
                result = r.reviews.issue(body)
            elif path == '/api/debug/export':
                result = r.reviews.export()
            elif path == '/api/debug/start-local':
                result = self.app.start_local(body)
            elif path == '/api/debug/shutdown':
                for run in r.index.jobs:
                    if r.index.jobs[run]['status'] == 'indexing':
                        r.index.cancel(run)
                self.reply(200, {'status': 'stopping'})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            else:
                return self.reply(404, {'error': 'readonly_debug_route_only'})
            return self.reply(200, result)
        except (ValueError, KeyError, TypeError) as error:
            message = str(error) if isinstance(error, ValueError) else 'invalid_request'
            return self.reply(409 if 'conflict' in message else 400, {'error': message})
        except Exception as error:
            return self.reply(500, {'error': type(error).__name__})


def make_debug_server(port=8876, root=ROOT, fixture=False):
    server = ThreadingHTTPServer(('127.0.0.1', port), DebugHandler)
    server.daemon_threads = True
    server.app = DebugApp(root, fixture)
    return server


def main():
    parser = argparse.ArgumentParser(description='H0 read-only debug/replay; no live capability')
    parser.add_argument('--port', type=int, default=8876)
    args = parser.parse_args()
    install_readonly_guard()
    server = make_debug_server(args.port)
    print(f'H0 read-only debug/replay http://127.0.0.1:{server.server_port}/; no LIVE / no models loaded', flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
