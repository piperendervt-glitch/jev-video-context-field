from email.message import Message
from types import SimpleNamespace
from pathlib import Path
from context_fields.server import Handler

def handler(host="127.0.0.1:8876",origin="http://127.0.0.1:8876",token="dummy-session"):
    h=object.__new__(Handler);h.headers=Message();h.headers["Host"]=host
    if origin:h.headers["Origin"]=origin
    if token:h.headers["X-Session-Token"]=token
    h.client_address=("127.0.0.1",123)
    h.server=SimpleNamespace(server_port=8876,app=SimpleNamespace(token="dummy-session"))
    return h

def test_same_origin_and_token_required():
    assert handler().allowed(token=True)
    assert not handler(origin="https://evil.example").allowed(token=True)
    assert not handler(host="evil.example:8876").allowed(token=True)
    assert not handler(token="wrong").allowed(token=True)
    h=handler();h.headers["Sec-Fetch-Site"]="cross-site"
    assert not h.allowed()

def test_no_dotenv_loading_or_html_from_model():
    root=Path(__file__).parents[1]
    js=(root/"web"/"app.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js and "eval(" not in js
    for path in (root/"context_fields").glob("*.py"):
        text=path.read_text(encoding="utf-8")
        assert "load_dotenv(" not in text and "os.environ" not in text
