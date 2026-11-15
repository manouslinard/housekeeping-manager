import base64
import hashlib
import hmac
import json
from datetime import timedelta

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

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


def test_admin_endpoints_need_token_when_live():
    env = Env()
    with make_client(env, dry_run=False, admin_token="adm") as c:
        assert c.get("/jobs").status_code == 401
        assert c.get("/jobs", headers={"X-Admin-Token": "adm"}).status_code == 200
    env2 = Env()
    with make_client(env2, dry_run=False, admin_token="") as c:
        assert c.get("/jobs").status_code == 403


def test_job_html_page_is_served():
    with make_client(Env()) as c:
        r = c.get("/job/7")
    assert r.status_code == 200 and "#7" in r.text and "<html" in r.text
