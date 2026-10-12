"""Where the app's UI comes from, and what the engine does without one.

The engine is headless first: everything the UI shows comes from the HTTP API
(audit_api.py), and the UI is an optional client bundled with it. `serve
--headless` (MAHORAGA_HEADLESS=1) mounts none of it: no pages, no static files,
no fallback, and `/` says so in JSON.

Otherwise the UI is the first of these that has an index.html:

    service/ui/      built into the package, so a wheel carries it
    frontend/dist/   a source checkout after `npm run build`
    static/          the legacy shell, in a source checkout only

The first two are a single-page app with client-side routes (/runs, /runs/:id,
/settings, ...), and some of those paths are also JSON endpoints (GET /runs). A
browser that reloads or opens one asks for `text/html`; a program asks for `*/*`
or application/json. So a navigation gets the app and every programmatic client
keeps the endpoint: `HistoryFallback`, the usual history-API fallback. It
applies to the SPA only, because the legacy shell has no client-side routes.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

# Paths are built from this file, so they hold in a wheel as well as a checkout.
# The legacy shell is a fixed path, not a search upward for any `static/`: from
# site-packages that search would find a user's own project folder.
_HERE = Path(__file__).resolve().parent
_PACKAGE_UI = _HERE / "ui"
_DEV_UI = _HERE.parents[2] / "frontend" / "dist"
_LEGACY_UI = _HERE.parents[2] / "static"

_TRUE = ("1", "true", "yes", "on")

# What the engine itself answers. The first path segment of these is never a
# client-side route, whatever the browser asks for.
_NOT_A_PAGE = frozenset({"api", "assets", "static", "docs", "redoc", "openapi.json"})

# Namespaces of JSON endpoints: an unknown path under one is a JSON 404, not
# the app. (A browser navigation is answered by HistoryFallback before this.)
_API_PREFIXES = (
    "api/", "assets/", "static/", "logs/", "missions/",
    "runs/", "settings/", "approvals/", "batch/", "tasks/",
)

# Vite names every bundle by a hash of its content, so a URL under /assets
# never changes meaning and a browser can keep it for a year.
_IMMUTABLE = "public, max-age=31536000, immutable"

_NO_UI = """<!doctype html>
<meta charset="utf-8">
<title>Mahoraga</title>
<body style="font: 16px/1.5 system-ui, sans-serif; max-width: 38rem; margin: 4rem auto; padding: 0 1rem">
<h1>The Mahoraga engine is running</h1>
<p>This install has no UI built in, so there is nothing to show here. The engine works:
<a href="/api/engine">/api/engine</a> says what it is, and <a href="/docs">/docs</a> lists its API.</p>
<p>The packaged release bundles the UI. From a source checkout, build <code>frontend/</code>
first; <code>mahoraga serve --headless</code> serves the API alone.</p>
"""


def flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in _TRUE


def headless() -> bool:
    return flag("MAHORAGA_HEADLESS")


def spa_dir() -> Path | None:
    """The built single-page UI, if this install has one."""
    return next((d for d in (_PACKAGE_UI, _DEV_UI) if (d / "index.html").is_file()), None)


def ui_dir() -> Path | None:
    """What `/` shows: the built UI, else the legacy shell."""
    return spa_dir() or (_LEGACY_UI if (_LEGACY_UI / "index.html").is_file() else None)


def _page(ui: Path) -> HTMLResponse:
    # no-cache: the shell is tiny and names the hashed bundles, so a copy kept
    # past an upgrade would point the browser at files that are gone.
    return HTMLResponse((ui / "index.html").read_text(), headers={"Cache-Control": "no-cache"})


def _file_in(ui: Path, rel: str) -> Path | None:
    """`rel` as a file inside `ui` other than its index.html (that one is the
    page), or None if it is not one or climbs out of `ui`."""
    try:
        root = ui.resolve()
        f = (ui / rel).resolve()
        return f if f.is_file() and f.is_relative_to(root) and f != root / "index.html" else None
    except (OSError, ValueError):
        return None


class _ImmutableFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code in (200, 304):
            response.headers["Cache-Control"] = _IMMUTABLE
        return response


def _wants_page(scope: Scope) -> bool:
    if scope["method"] not in ("GET", "HEAD"):
        return False
    if scope["path"].lstrip("/").split("/", 1)[0] in _NOT_A_PAGE:
        return False
    accept = next((v for k, v in scope["headers"] if k == b"accept"), b"")
    return b"text/html" in accept.lower()


class HistoryFallback:
    """A browser navigation to any client-side route gets the app, even where a
    JSON endpoint has the same path. Real files in the UI (a favicon) are left
    to be served as themselves."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and _wants_page(scope):
            ui = spa_dir()
            if ui is not None and _file_in(ui, scope["path"].lstrip("/")) is None:
                await _page(ui)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def install(app: FastAPI) -> None:
    """Serve the UI, or say there isn't one. Call it after every other route is
    registered: the catch-all below has to be last."""
    if headless():
        @app.get("/", include_in_schema=False)
        def headless_root() -> dict:
            return {"name": "mahoraga", "headless": True, "api": "/api/engine"}
        return

    app.add_middleware(HistoryFallback)
    if _LEGACY_UI.is_dir():
        app.mount("/static", StaticFiles(directory=str(_LEGACY_UI)), name="static")
    spa = spa_dir()
    if spa is not None and (spa / "assets").is_dir():
        app.mount("/assets", _ImmutableFiles(directory=str(spa / "assets")),
                  name="frontend-assets")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index() -> Response:
        ui = ui_dir()
        return _page(ui) if ui else HTMLResponse(_NO_UI)

    # Any other GET that no route answered gets the app, so a client-side route
    # survives a hard reload from a client that doesn't ask for HTML either.
    @app.get("/{full_path:path}", response_class=HTMLResponse, include_in_schema=False)
    def catch_all(full_path: str) -> Response:
        if full_path.startswith(_API_PREFIXES):
            raise HTTPException(status_code=404, detail="not found")
        ui = ui_dir()
        if ui is None:
            raise HTTPException(status_code=404, detail="UI not built")
        spa = spa_dir()
        file = _file_in(spa, full_path) if spa else None
        return FileResponse(file) if file else _page(ui)
