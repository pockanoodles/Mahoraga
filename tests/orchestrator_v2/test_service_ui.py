"""Tests for how the engine serves its UI: the history-API fallback, caching,
the legacy shell, an install with no UI, and headless mode.

Most run against a bare FastAPI app that has a JSON route on the same path as a
client-side route (`/runs`) and then `ui.install(app)`, because the UI is
chosen when the app is built. The real app gets the same collision checked
(`/settings`, `/runs`), and its headless wiring is checked in a fresh process,
since headless is decided at import.
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.orchestrator.service import ui

BROWSER = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,*/*;q=0.8"
INDEX = '<!doctype html><title>Mahoraga app</title><script src="/assets/app-3f9a1c.js"></script>'
REPO_ROOT = Path(__file__).resolve().parents[2]


def _point_at(monkeypatch, *, package: Path | None = None, dev: Path | None = None,
              legacy: Path | None = None) -> None:
    """Choose which UI builds exist: a path that isn't there means none."""
    missing = Path("/nonexistent-ui")
    monkeypatch.setattr(ui, "_PACKAGE_UI", package or missing)
    monkeypatch.setattr(ui, "_DEV_UI", dev or missing)
    monkeypatch.setattr(ui, "_LEGACY_UI", legacy or missing)


@pytest.fixture(autouse=True)
def _not_headless(monkeypatch):
    monkeypatch.delenv("MAHORAGA_HEADLESS", raising=False)


@pytest.fixture
def built_ui(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "ui"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text(INDEX)
    (d / "assets" / "app-3f9a1c.js").write_text("console.log('app')")
    (d / "favicon.svg").write_text("<svg/>")
    _point_at(monkeypatch, package=d)
    return d


def _engine() -> FastAPI:
    """Stands in for the engine: JSON routes, two of which share a path with
    client-side routes, then the UI."""
    app = FastAPI()

    @app.get("/runs")
    def runs():
        return [{"id": "r1"}]

    @app.get("/runs/{run_id}")
    def run(run_id: str):
        return {"id": run_id}

    @app.get("/api/thing")
    def thing():
        return {"thing": True}

    ui.install(app)
    return app


# ── The history-API fallback ──────────────────────────────────────────────────


def test_a_browser_reloading_a_client_route_gets_the_app_not_the_json(built_ui):
    c = TestClient(_engine())
    for path in ("/runs", "/runs/r1", "/repositories", "/repositories/calc", "/arms",
                 "/router", "/settings"):
        r = c.get(path, headers={"Accept": BROWSER})
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/html"), path
        assert r.text == INDEX, path


def test_programs_keep_reaching_the_endpoints_that_share_those_paths(built_ui):
    c = TestClient(_engine())
    assert c.get("/runs").json() == [{"id": "r1"}]  # a plain GET: Accept */*
    assert c.get("/runs/r1").json() == {"id": "r1"}
    assert c.get("/runs", headers={"Accept": "application/json"}).json() == [{"id": "r1"}]
    assert c.get("/runs", headers={"Accept": "application/json, text/plain, */*"}).json() == [
        {"id": "r1"}]  # an axios-style client
    assert c.get("/runs", headers={"Accept": "text/event-stream"}).json() == [{"id": "r1"}]


def test_the_engines_own_paths_are_never_answered_with_the_app(built_ui):
    c = TestClient(_engine())
    assert c.get("/api/thing", headers={"Accept": BROWSER}).json() == {"thing": True}
    assert c.get("/openapi.json", headers={"Accept": BROWSER}).json()["openapi"]
    docs = c.get("/docs", headers={"Accept": BROWSER})
    assert "swagger" in docs.text.lower() and INDEX not in docs.text
    assert "redoc" in c.get("/redoc", headers={"Accept": BROWSER}).text.lower()
    js = c.get("/assets/app-3f9a1c.js", headers={"Accept": BROWSER})
    assert js.text == "console.log('app')"


def test_a_file_in_the_ui_is_served_as_itself_even_to_a_navigation(built_ui):
    c = TestClient(_engine())
    r = c.get("/favicon.svg", headers={"Accept": BROWSER})
    assert r.text == "<svg/>" and r.headers["content-type"].startswith("image/svg")
    assert c.get("/favicon.svg").text == "<svg/>"
    # index.html is the page, with the page's caching, not a plain file.
    assert c.get("/index.html", headers={"Accept": BROWSER}).headers["cache-control"] == "no-cache"


def test_nothing_outside_the_ui_folder_is_reachable_through_it(built_ui, tmp_path):
    (tmp_path / "secret.txt").write_text("not for the browser")
    c = TestClient(_engine())
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/..%2fsecret.txt", "/assets/../../secret.txt"):
        r = c.get(path, headers={"Accept": BROWSER})
        assert "not for the browser" not in r.text, path


def test_only_get_and_head_are_navigations(built_ui):
    c = TestClient(_engine())
    assert c.post("/runs", headers={"Accept": BROWSER}).status_code == 405
    head = c.head("/runs", headers={"Accept": BROWSER})
    assert head.status_code == 200 and head.headers["content-type"].startswith("text/html")


# ── Caching ───────────────────────────────────────────────────────────────────


def test_the_app_shell_is_never_cached_and_hashed_assets_always_are(built_ui):
    c = TestClient(_engine())
    for path, headers in (("/", {}), ("/", {"Accept": BROWSER}), ("/repositories", {}),
                          ("/runs", {"Accept": BROWSER})):
        assert c.get(path, headers=headers).headers["cache-control"] == "no-cache", path
    asset = c.get("/assets/app-3f9a1c.js")
    forever = "public, max-age=31536000, immutable"
    assert asset.status_code == 200 and asset.headers["cache-control"] == forever
    again = c.get("/assets/app-3f9a1c.js", headers={"If-None-Match": asset.headers["etag"]})
    assert again.status_code == 304 and again.headers["cache-control"] == forever
    missing = c.get("/assets/gone-123.js")
    assert missing.status_code == 404 and "cache-control" not in missing.headers


# ── The catch-all ─────────────────────────────────────────────────────────────


def test_a_client_that_does_not_ask_for_html_still_gets_the_app_on_an_unknown_path(built_ui):
    c = TestClient(_engine())
    assert c.get("/observatory").text == INDEX
    assert c.get("/").text == INDEX


def test_unknown_paths_in_the_apis_namespaces_are_json_404s(built_ui):
    c = TestClient(_engine())
    for path in ("/api/nope", "/api/audit/nope", "/runs/a/b", "/static/nope"):
        r = c.get(path)
        assert r.status_code == 404 and r.json() == {"detail": "not found"}, path
    gone = c.get("/assets/nope/x")  # answered by the mount, not the catch-all
    assert gone.status_code == 404 and gone.headers["content-type"] == "application/json"


# ── Other UIs, or none ────────────────────────────────────────────────────────


def test_the_dev_build_is_used_when_the_package_has_none(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(INDEX)
    _point_at(monkeypatch, dev=dist)
    c = TestClient(_engine())
    assert c.get("/").text == INDEX
    assert c.get("/runs", headers={"Accept": BROWSER}).text == INDEX


def test_the_package_build_wins_over_the_dev_build(tmp_path, monkeypatch):
    for name in ("pkg", "dist"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "index.html").write_text(f"<title>{name}</title>")
    _point_at(monkeypatch, package=tmp_path / "pkg", dev=tmp_path / "dist")
    assert TestClient(_engine()).get("/").text == "<title>pkg</title>"


def test_the_legacy_shell_is_served_but_is_not_given_client_routes(tmp_path, monkeypatch):
    legacy = tmp_path / "static"
    legacy.mkdir()
    (legacy / "index.html").write_text("<title>legacy</title>")
    (legacy / "app.js").write_text("legacy()")
    _point_at(monkeypatch, legacy=legacy)
    c = TestClient(_engine())
    assert c.get("/").text == "<title>legacy</title>"
    assert c.get("/static/app.js").text == "legacy()"
    assert c.get("/runs", headers={"Accept": BROWSER}).json() == [{"id": "r1"}]  # no fallback
    assert c.get("/observatory").text == "<title>legacy</title>"  # the old catch-all stays
    assert c.get("/app.js").text == "<title>legacy</title>"  # root files are the SPA's only


def test_with_no_ui_at_all_the_engine_says_so_instead_of_failing(monkeypatch):
    _point_at(monkeypatch)
    c = TestClient(_engine())
    home = c.get("/")
    assert home.status_code == 200 and "no UI built in" in home.text
    assert "/api/engine" in home.text
    assert c.get("/runs", headers={"Accept": BROWSER}).json() == [{"id": "r1"}]
    missing = c.get("/repositories")
    assert missing.status_code == 404 and missing.json() == {"detail": "UI not built"}


def test_a_ui_built_while_the_engine_runs_is_picked_up(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    _point_at(monkeypatch, dev=dist)
    c = TestClient(_engine())
    assert "no UI built in" in c.get("/").text
    dist.mkdir()
    (dist / "index.html").write_text(INDEX)
    assert c.get("/").text == INDEX
    assert c.get("/runs", headers={"Accept": BROWSER}).text == INDEX


# ── Headless ──────────────────────────────────────────────────────────────────


def test_headless_mounts_no_ui_and_no_catch_all(built_ui, monkeypatch):
    monkeypatch.setenv("MAHORAGA_HEADLESS", "1")
    app = _engine()
    c = TestClient(app)
    assert c.get("/").json() == {"name": "mahoraga", "headless": True, "api": "/api/engine"}
    assert not any(m.cls is ui.HistoryFallback for m in app.user_middleware)
    # Nothing serves HTML: a navigation reaches the endpoint, and the UI's files
    # and routes are plain 404s, though a built UI is sitting right there.
    assert c.get("/runs", headers={"Accept": BROWSER}).json() == [{"id": "r1"}]
    for path in ("/repositories", "/index.html", "/favicon.svg", "/assets/app-3f9a1c.js",
                 "/static/app.js", "/observatory"):
        r = c.get(path, headers={"Accept": BROWSER})
        assert r.status_code == 404 and r.json() == {"detail": "Not Found"}, path


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " 1 "])
def test_headless_flag_spellings(value, monkeypatch):
    monkeypatch.setenv("MAHORAGA_HEADLESS", value)
    assert ui.headless()


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "headless"])
def test_other_values_are_not_headless(value, monkeypatch):
    monkeypatch.setenv("MAHORAGA_HEADLESS", value)
    assert not ui.headless()
    monkeypatch.delenv("MAHORAGA_HEADLESS")
    assert not ui.headless()


# ── The real app ──────────────────────────────────────────────────────────────


class _NoRuns:
    async def list_all_runs(self):
        return []

    async def get_run(self, run_id):
        return None


class _Store:
    missions = _NoRuns()


@pytest.fixture
def engine(built_ui):
    from backend.orchestrator.service.app import app, get_store

    app.dependency_overrides[get_store] = lambda: _Store()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_the_real_app_hands_browsers_the_app_where_its_endpoints_collide(engine):
    for path in ("/", "/runs", "/runs/r1", "/settings", "/repositories/calc"):
        r = engine.get(path, headers={"Accept": BROWSER})
        assert r.text == INDEX and r.headers["cache-control"] == "no-cache", path
    assert engine.get("/runs").json() == []
    assert engine.get("/runs/r1").json() == {"detail": "Run not found"}
    assert engine.get("/settings").json()["executor_model"]
    assert engine.get("/settings", headers={"Accept": "application/json"}).json()["executor_model"]


def test_the_real_app_leaves_its_api_and_docs_to_the_engine(engine):
    assert engine.get("/api/health/nope", headers={"Accept": BROWSER}).json() == {"detail": "not found"}
    assert engine.get("/openapi.json", headers={"Accept": BROWSER}).json()["info"]["title"]
    assert "swagger" in engine.get("/docs", headers={"Accept": BROWSER}).text.lower()


def test_the_real_app_allows_the_vite_dev_server_and_no_one_else(engine):
    ask = {"Access-Control-Request-Method": "GET"}
    for origin in ("http://localhost:5173", "http://127.0.0.1:5173"):
        r = engine.options("/settings", headers={"Origin": origin, **ask})
        assert r.status_code == 200 and r.headers["access-control-allow-origin"] == origin
        got = engine.get("/settings", headers={"Origin": origin})
        assert got.headers["access-control-allow-origin"] == origin
    for origin in ("http://localhost:3000", "http://localhost:5174", "https://example.com",
                   "http://localhost:5173.evil.test"):
        r = engine.options("/settings", headers={"Origin": origin, **ask})
        assert r.status_code == 400 and "access-control-allow-origin" not in r.headers, origin
        got = engine.get("/settings", headers={"Origin": origin})
        assert "access-control-allow-origin" not in got.headers, origin


def test_the_real_app_in_headless_mode_serves_no_ui(tmp_path):
    """Headless is decided when the app is imported, so it needs its own process."""
    script = textwrap.dedent("""
        import json
        from fastapi.testclient import TestClient
        from backend.orchestrator.service import ui
        from backend.orchestrator.service.app import app

        c = TestClient(app)
        html = {"Accept": "text/html"}
        paths = ("/repositories", "/settings", "/index.html")
        print(json.dumps({
            "root": c.get("/").json(),
            "navigation": {p: [c.get(p, headers=html).status_code,
                               c.get(p, headers=html).headers["content-type"]] for p in paths},
            "fallback": any(m.cls is ui.HistoryFallback for m in app.user_middleware),
            "openapi": c.get("/openapi.json").status_code,
        }))
    """)
    run = subprocess.run([sys.executable, "-c", script], cwd=REPO_ROOT, capture_output=True,
                         text=True, timeout=120,
                         env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "MAHORAGA_HEADLESS": "1"})
    assert run.returncode == 0, run.stderr[-2000:]
    out = json.loads(run.stdout.strip().splitlines()[-1])
    assert out["root"] == {"name": "mahoraga", "headless": True, "api": "/api/engine"}
    assert out["fallback"] is False and out["openapi"] == 200
    # a navigation reaches the endpoint (/settings) or a JSON 404; none gets HTML
    assert out["navigation"] == {"/repositories": [404, "application/json"],
                                 "/settings": [200, "application/json"],
                                 "/index.html": [404, "application/json"]}
