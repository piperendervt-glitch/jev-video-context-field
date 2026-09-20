import hashlib
import json

CONFIG = {
    "schema_version": "2", "field_rule_id": "evidence-mass-v1.1",
    "B": 1.0, "U": 1.0, "kappa": 1.0, "root_seconds": 2,
    "half_life": {"person": 8, "place": 12, "conversation": 20},
    "retention_s": 180, "hypothesis_limit": 128, "tiles": 12,
    "field_hz": 10, "render_max_hz": 30, "readout_interval_s": 2,
    "readout_ttl_s": 4, "evidence_ttl_s": 8, "request_deadline_s": 5,
    "reader_interval_s": 5, "reader_fraction": 0.2,
    "max_attempts": 240, "max_units": 720, "max_chars": 2880000,
    "request_units": 3, "request_questions": 12, "request_chars": 12000,
    "retries": 0, "heavy_concurrency": 1, "heavy_waiting": 8,
    "semantic_diffusion": False, "attributes_enabled": False,
    "live_enabled": False, "max_media_s": 120, "playback_rate": 1,
    "model": "jev-1.13.0", "prompt_version": "ja-facets-v1",
}

def json_text(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)

CONFIG_HASH = hashlib.sha256(json_text(CONFIG).encode()).hexdigest()
