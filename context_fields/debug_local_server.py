"""Explicit H0 local-real / evaluation-MOCK child. Never a LIVE entry point."""
import argparse
import re
from http.server import ThreadingHTTPServer
from .server import App, Handler, ROOT
from .debug_server import install_readonly_guard
from .media import Asset, SUPPORTED, probe
from .review_index import digest


class SelectedLocalApp(App):
    def new_session(self, mode, asset_id=None, start=0):
        # App's initial synthetic session is unnecessary for explicit selected media.
        if self.session is None and mode == 'MOCK' and asset_id is None:
            return None
        return super().new_session(mode, asset_id, start)


def main():
    parser = argparse.ArgumentParser(description='Explicit local models + MOCK evaluation; no Jev capability')
    parser.add_argument('--asset-id', required=True)
    parser.add_argument('--asset-sha256', required=True)
    parser.add_argument('--start', type=float, default=0)
    parser.add_argument('--port', type=int, default=8877)
    args = parser.parse_args()
    if not re.fullmatch(r'asset-[0-9a-f]{32}', args.asset_id) or not re.fullmatch(r'[0-9a-f]{64}', args.asset_sha256):
        raise ValueError('asset_identity')
    install_readonly_guard()
    # Existing LocalModels uses explicit directories and local_files_only=True.
    # The process audit guard also rejects remote connections and .env access.
    paths = [ROOT / 'artifacts/media' / (args.asset_id + ext) for ext in SUPPORTED
             if (ROOT / 'artifacts/media' / (args.asset_id + ext)).is_file()]
    if len(paths) != 1 or paths[0].resolve().parent != (ROOT / 'artifacts/media').resolve() or digest(paths[0]) != args.asset_sha256:
        raise ValueError('asset_identity_mismatch')
    metadata = probe(paths[0])
    if not 0 <= args.start < metadata['duration_s']:
        raise ValueError('analysis_start')
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.daemon_threads = True
    app = SelectedLocalApp(load_models=True, live_capability=None)
    server.app = app
    app.assets.assets[args.asset_id] = Asset(args.asset_id, paths[0], paths[0].name, paths[0].stat().st_size, args.asset_sha256, metadata)
    app.new_session('LOCAL', args.asset_id, args.start)
    print(f'LOCAL_REAL_EVAL_MOCK http://127.0.0.1:{args.port}/; selected media; paused; no LIVE', flush=True)
    try:
        server.serve_forever()
    finally:
        app.session.stop()
        if app.session.local_pipeline:
            app.session.local_pipeline.close()
        app.pool.close()
        if app.models:
            app.models.close()
        server.server_close()


if __name__ == '__main__':
    main()
