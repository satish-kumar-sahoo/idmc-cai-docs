import json
from pathlib import Path

from cai_docs import config as config_mod
from cai_docs import describe
from cai_docs.auth import ClaudeAuth
from cai_docs.classify import classify
from cai_docs.config import Config
from cai_docs.describe import _redacted_payload, describe_assets, static_summary
from cai_docs.extract import extract
from cai_docs.graph import build_graph
from cai_docs.models import RunReport
from cai_docs.xmlmodel import parse


def _asset(raw):
    doc = parse(raw)
    at, conf, sig = classify(doc)
    return extract(doc, at, conf, sig)


def _set_login(monkeypatch, auth):
    """Force config's auth resolution deterministically (logged-in or not)."""
    monkeypatch.setattr(config_mod, "resolve_auth", lambda: auth)


def test_static_summary_always_present(real_create):
    a = _asset(real_create)
    g = build_graph([a])
    s = static_summary(a, g)
    assert "createMultipleIdentifier" in s
    assert "subprocess" in s.lower()
    assert "SQL" in s


def test_no_llm_when_not_logged_in(real_create, tmp_path, monkeypatch):
    _set_login(monkeypatch, None)  # not logged in, no env key
    a = _asset(real_create)
    g = build_graph([a])
    cfg = Config(input_path=Path("."), output_dir=tmp_path, cache_dir=tmp_path / "c")
    assert cfg.llm_enabled is False
    rep = RunReport()
    describe_assets([a], g, cfg, rep)
    assert a.static_summary
    assert a.llm_narrative is None
    assert rep.llm_calls == 0


def test_no_llm_when_flag_off_even_if_logged_in(real_create, tmp_path, monkeypatch):
    _set_login(monkeypatch, ClaudeAuth(token="t", kind="oauth"))
    cfg = Config(input_path=Path("."), output_dir=tmp_path,
                 cache_dir=tmp_path / "c", use_llm=False)
    assert cfg.llm_enabled is False


def test_llm_enrichment_with_login_mocked_and_cached(real_create, tmp_path, monkeypatch):
    _set_login(monkeypatch, ClaudeAuth(token="oauth-token", kind="oauth"))
    calls = {"n": 0}

    def fake_call(prompt, config):
        calls["n"] += 1
        return "This process creates multiple identifiers."

    monkeypatch.setattr(describe, "_call_llm", fake_call)

    a = _asset(real_create)
    g = build_graph([a])
    cfg = Config(input_path=Path("."), output_dir=tmp_path, cache_dir=tmp_path / "c")
    assert cfg.llm_enabled is True
    rep = RunReport()

    describe_assets([a], g, cfg, rep)
    assert a.llm_narrative == "This process creates multiple identifiers."
    assert calls["n"] == 1 and rep.llm_calls == 1

    # second run hits cache, no extra API call
    a2 = _asset(real_create)
    rep2 = RunReport()
    describe_assets([a2], build_graph([a2]), cfg, rep2)
    assert a2.llm_narrative == "This process creates multiple identifiers."
    assert calls["n"] == 1
    assert rep2.llm_cache_hits == 1


def test_redacted_payload_excludes_secrets_and_sample_data(real_create):
    a = _asset(real_create)
    a.config["client_secret"] = "supersecret"
    payload = _redacted_payload(a)
    assert payload["config"].get("client_secret") == "<redacted>"
    # sample data is never part of what gets sent to the LLM
    assert "sample_data" not in payload
    assert "supersecret" not in json.dumps(payload)
