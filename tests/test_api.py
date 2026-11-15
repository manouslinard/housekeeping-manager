import base64
import hashlib
import hmac
import json
from datetime import timedelta

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app, valid_twilio_signature

from .conftest import TODAY, Env, ics


def make_client(env, **settings_overrides):
    for k, v in settings_overrides.items():
        setattr(env.settings, k, v)
    app = create_app(env.settings, agent=env.agent, background=False)
    return TestClient(app)


def seed(env):
    env.add_property("plaka", "Plaka Studio", 37.9715, 23.7300, {"airbnb": "airbnb://plaka"})
    env.add_cleaner("Maria", "+306900000001", 37.9755, 23.7348)
    out = TODAY + timedelta(days=3)
    env.feeds["airbnb://plaka"] = ics(("r1", out - timedelta(days=2), out, "Reserved"))


def test_twilio_webhook_accepts_job_in_dry_run():
    env = Env()
    seed(env)
    with make_client(env) as c:
        assert c.post("/admin/sync").status_code == 200
        r = c.post("/webhooks/twilio", data={"From": "+306900000001", "Body": "YES 1"})
        assert r.status_code == 200 and "<Response" in r.text
        assert c.get("/jobs").json()[0]["status"] == "assigned"


def test_twilio_signature_is_enforced_when_live():
    env = Env()
    seed(env)
    token = "secret"
    overrides = dict(dry_run=False, twilio_auth_token=token, public_base_url="https://x.example", admin_token="adm")
    with make_client(env, **overrides) as c:
        bad = c.post("/webhooks/twilio", data={"From": "+306900000001", "Body": "YES"}, headers={"X-Twilio-Signature": "nope"})
        assert bad.status_code == 403
        params = {"From": "+306900000001", "Body": "YES"}
        data = "https://x.example/webhooks/twilio" + "".join(k + params[k] for k in sorted(params))
        sig = base64.b64encode(hmac.new(token.encode(), data.encode(), hashlib.sha1).digest()).decode()
        assert valid_twilio_signature(token, "https://x.example/webhooks/twilio", params, sig)
        ok = c.post("/webhooks/twilio", data=params, headers={"X-Twilio-Signature": sig})
        assert ok.status_code == 200


def test_admin_endpoints_need_token_when_live():
    env = Env()
    with make_client(env, dry_run=False, admin_token="adm") as c:
        assert c.get("/jobs").status_code == 401
        assert c.get("/jobs", headers={"X-Admin-Token": "adm"}).status_code == 200
    env2 = Env()
    with make_client(env2, dry_run=False, admin_token="") as c:
        assert c.get("/jobs").status_code == 403


def test_viber_webhook_signature_and_message():
    env = Env()
    seed(env)
    env.db.execute("UPDATE cleaners SET channel='viber', viber_id='vb1' WHERE name='Maria'")
    token = "vtoken"
    with make_client(env, dry_run=False, viber_auth_token=token, admin_token="adm") as c:
        body = json.dumps({"event": "message", "sender": {"id": "vb1"}, "message": {"type": "text", "text": "yes"}}).encode()
        assert c.post("/webhooks/viber", content=body, headers={"X-Viber-Content-Signature": "bad"}).status_code == 403
        sig = hmac.new(token.encode(), body, hashlib.sha256).hexdigest()
        # offer first, using the fake messenger via the agent
        import asyncio
        asyncio.run(env.agent.sync_calendars())
        r = c.post("/webhooks/viber", content=body, headers={"X-Viber-Content-Signature": sig})
        assert r.status_code == 200
        assert c.get("/jobs", headers={"X-Admin-Token": "adm"}).json()[0]["status"] == "assigned"


def test_job_html_page_is_served():
    with make_client(Env()) as c:
        r = c.get("/job/7")
    assert r.status_code == 200 and "#7" in r.text and "<html" in r.text
