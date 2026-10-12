"""Install a built wheel into a throwaway venv and check it works.

Run it on the exact file that is about to be published. PyPI never lets a
version be uploaded twice, so a wheel that fails here must not ship. It checks
what a user gets from `pip install`: the commands run, the web UI and the
bundled data are inside the package, `mahoraga serve` boots against an empty
HOME and serves the UI, and `mahoraga demo` serves the recorded bench.

    python scripts/smoke_wheel.py dist/mahoraga-2.1.0-py3-none-any.whl

Everything runs from an empty directory with an empty HOME, so the source tree
and the real ~/.mahoraga-v2 can never be what is being tested.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

HEALTH_TIMEOUT_S = 90

# Resolved inside the venv, against the installed copy only.
INSPECT = """
import json, pathlib, backend
root = pathlib.Path(backend.__file__).parent / "orchestrator"
demo = root / "demo" / "data"
print(json.dumps({
    "backend": str(pathlib.Path(backend.__file__).parent),
    "ui": (root / "service" / "ui" / "index.html").is_file(),
    "pricing": (root / "audit" / "pricing.json").is_file(),
    "demo": demo.is_dir() and any(demo.iterdir()),
}))
"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except (urllib.error.URLError, OSError):
        return 0, ""


def get_json(url: str) -> tuple[int, dict]:
    code, body = get(url)
    try:
        return code, json.loads(body)
    except ValueError:
        return code, {}


class Report:
    def __init__(self) -> None:
        self.failed: list[str] = []
        self.logs: list[Path] = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f"  ({detail})" if detail else ""), flush=True)
        if not ok:
            self.failed.append(name)


@contextlib.contextmanager
def running(cmd: list[str], *, cwd: Path, env: dict[str, str], port: int, log_path: Path,
            report: Report) -> Iterator[tuple[str, int]]:
    """Start a server, wait for /api/health, hand back its URL and the last status seen, stop it."""
    base = f"http://127.0.0.1:{port}"
    report.logs.append(log_path)
    with log_path.open("w") as log:
        server = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            status = 0
            deadline = time.monotonic() + HEALTH_TIMEOUT_S
            while time.monotonic() < deadline and server.poll() is None:
                status, _ = get(f"{base}/api/health")
                if status == 200:
                    break
                time.sleep(1)
            yield base, status
        finally:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("wheel", type=Path)
    args = ap.parse_args()
    wheel = args.wheel.resolve()
    name = re.match(r"[^-]+-([^-]+)-", wheel.name)
    if not wheel.is_file() or not name:
        print(f"not a wheel: {wheel}", file=sys.stderr)
        return 2
    version = name.group(1)

    report = Report()
    with tempfile.TemporaryDirectory(prefix="mahoraga-smoke-") as tmp:
        tmp_path = Path(tmp)
        home, cwd = tmp_path / "home", tmp_path / "cwd"
        home.mkdir()
        cwd.mkdir()
        # Nothing from the caller's setup (keys, bench roots, Ollama URLs) may leak in.
        env = {
            k: v for k, v in os.environ.items()
            if not k.startswith(("MAHORAGA_", "OLLAMA_")) and k not in ("PYTHONPATH", "ANTHROPIC_API_KEY")
        }
        env["HOME"] = str(home)

        print(f"Installing {wheel.name} into a fresh venv (Python {sys.version.split()[0]})", flush=True)
        subprocess.run([sys.executable, "-m", "venv", str(tmp_path / "venv")], check=True)
        bin_dir = tmp_path / "venv" / "bin"
        subprocess.run(
            [str(bin_dir / "python"), "-m", "pip", "install", "--disable-pip-version-check", "-q", str(wheel)],
            check=True,
        )

        def run(*cmd: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [str(bin_dir / cmd[0]), *cmd[1:]], cwd=cwd, env=env, capture_output=True, text=True, timeout=120
            )

        print("Checking the install", flush=True)
        for script in ("mahoraga", "orch"):
            report.check(f"`{script} --help` exits 0", run(script, "--help").returncode == 0)
        out = run("mahoraga", "version")
        report.check(f"`mahoraga version` prints {version}", out.returncode == 0 and version in out.stdout, out.stdout.strip()[:60])

        found = json.loads(run("python", "-c", INSPECT).stdout)
        report.check("the code runs from the installed copy", "site-packages" in found["backend"], found["backend"])
        report.check("web UI is in the package", found["ui"])
        report.check("audit/pricing.json is in the package", found["pricing"])
        report.check("demo dataset is in the package", found["demo"])

        port = free_port()
        print(f"Starting `mahoraga serve` on port {port}", flush=True)
        with running([str(bin_dir / "mahoraga"), "serve", "--port", str(port)], cwd=cwd, env=env, port=port,
                     log_path=tmp_path / "serve.log", report=report) as (base, status):
            report.check("/api/health returns 200", status == 200, f"HTTP {status}")
            code, page = get(f"{base}/")
            asset = re.search(r'(?:src|href)="(/assets/[^"]+)"', page)
            report.check("/ serves the web UI", code == 200 and asset is not None, f"HTTP {code}")
            if asset:
                report.check(f"{asset.group(1)} is served", get(base + asset.group(1))[0] == 200)
            code, engine = get_json(f"{base}/api/engine")
            report.check("/api/engine reports this version, with the UI",
                         engine.get("version") == version and engine.get("ui") is True, f"HTTP {code}")

        port = free_port()
        print(f"Starting `mahoraga demo` on port {port}", flush=True)
        with running([str(bin_dir / "mahoraga"), "demo", "--port", str(port), "--headless"], cwd=cwd, env=env,
                     port=port, log_path=tmp_path / "demo.log", report=report) as (base, status):
            report.check("the demo engine answers /api/health", status == 200, f"HTTP {status}")
            code, engine = get_json(f"{base}/api/engine")
            report.check("/api/engine says it is the demo", engine.get("demo") is True, f"HTTP {code}")
            code, benches = get_json(f"{base}/api/audit/benches")
            report.check("the demo bench is listed", bool(benches.get("benches")), f"HTTP {code}")

        if report.failed:
            for log in report.logs:
                print(f"--- {log.name} (tail) ---")
                print("\n".join(log.read_text().splitlines()[-25:]))

    print("PASS" if not report.failed else f"FAILED: {', '.join(report.failed)}")
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
