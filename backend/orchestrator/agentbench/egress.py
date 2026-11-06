"""The one way an agent's traffic leaves the machine, and the record of it.

An agent's sandbox opens no connection except to loopback ports the harness
gives it. A cloud arm gets one, this proxy, which opens an HTTPS tunnel (HTTP
CONNECT) only to the hosts its vendor serves the model from and records every
tunnel asked for, allowed or refused, with the bytes each way. The tunnels are
TLS end to end, so the record is where and how much, never what.

A local arm on Linux gets a `Relay` instead: its sandbox has its own network
namespace with no route out, so the relay carries its connections to Ollama
and nowhere else.

On macOS the sandbox allows localhost, so the proxy listens on 127.0.0.1. On
Linux the agent's namespace can't reach the host's loopback, so the proxy and
the relay listen on Unix sockets bound into the sandbox, and `bridge.py`
inside turns a loopback port into a connection to one of them.
"""
from __future__ import annotations

import json
import select
import socket
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

_HEAD_LIMIT = 16 * 1024
_CHUNK = 64 * 1024


@dataclass
class Tunnel:
    host: str
    port: int
    allowed: bool
    sent: int = 0  # bytes from the agent to the host
    received: int = 0  # bytes from the host to the agent
    secs: float = 0.0
    error: str = ""


def _pump(a: socket.socket, b: socket.socket, counts: list[int]) -> None:
    """Copy both ways until either side closes. counts = [a→b, b→a]."""
    pair = {a: (b, 0), b: (a, 1)}
    open_ = [a, b]
    while open_:
        try:
            ready, _, _ = select.select(open_, [], [], 60)
        except (OSError, ValueError):  # a socket closed under us: the proxy is stopping
            return
        if not ready:
            continue
        for s in ready:
            dst, i = pair[s]
            try:
                data = s.recv(_CHUNK)
            except OSError:
                data = b""
            if not data:
                return
            try:
                dst.sendall(data)
            except OSError:
                return
            counts[i] += len(data)


def _listen(unix: Path | None) -> socket.socket:
    if unix is not None:
        unix.unlink(missing_ok=True)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(str(unix))
    else:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
    s.listen(64)
    return s


class _Server:
    """Accepts on its own thread and handles each connection on another."""

    def __init__(self, unix: Path | None = None) -> None:
        self.unix = unix
        self._sock: socket.socket | None = None
        self._conns: set[socket.socket] = set()
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()

    @property
    def address(self) -> str:
        """`127.0.0.1:<port>`, or the socket's path."""
        if self.unix is not None:
            return str(self.unix)
        host, port = self._sock.getsockname()[:2]
        return f"{host}:{port}"

    def __enter__(self):
        self._sock = _listen(self.unix)
        threading.Thread(target=self._accept, daemon=True).start()
        return self

    def __exit__(self, *exc) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            sock.close()
        with self._lock:
            for c in list(self._conns):
                c.close()
            threads = list(self._threads)
        for t in threads:  # each records its tunnel as it ends
            t.join(timeout=5)
        if self.unix is not None:
            self.unix.unlink(missing_ok=True)

    def _accept(self) -> None:
        while (sock := self._sock) is not None:
            try:
                conn, _ = sock.accept()
            except OSError:
                return
            t = threading.Thread(target=self._serve, args=(conn,), daemon=True)
            with self._lock:
                self._conns.add(conn)
                self._threads.append(t)
            t.start()

    def _serve(self, conn: socket.socket) -> None:
        try:
            self.handle(conn)
        except (OSError, ValueError):
            pass
        finally:
            conn.close()
            with self._lock:
                self._conns.discard(conn)

    def handle(self, conn: socket.socket) -> None:
        raise NotImplementedError


def _read_head(conn: socket.socket) -> tuple[bytes, bytes]:
    """(request head, whatever followed it). Empty head = not HTTP."""
    buf = b""
    conn.settimeout(30)
    while b"\r\n\r\n" not in buf:
        data = conn.recv(4096)
        if not data or len(buf) + len(data) > _HEAD_LIMIT:
            return b"", b""
        buf += data
    conn.settimeout(None)
    head, _, rest = buf.partition(b"\r\n\r\n")
    return head, rest


def _split_target(target: str) -> tuple[str, int]:
    host, _, port = target.rpartition(":")
    return host.strip("[]").lower(), int(port) if port.isdigit() else 0


class Proxy(_Server):
    """An HTTP CONNECT proxy that tunnels only to `allow` ("host:port")."""

    def __init__(self, allow: Iterable[str], unix: Path | None = None) -> None:
        super().__init__(unix)
        self.allow = {a.lower() for a in allow}
        self.tunnels: list[Tunnel] = []

    def _record(self, t: Tunnel) -> None:
        with self._lock:
            self.tunnels.append(t)

    def handle(self, conn: socket.socket) -> None:
        t0 = time.monotonic()
        head, rest = _read_head(conn)
        method, target = "", ""
        if head:
            parts = head.split(b"\r\n", 1)[0].decode("latin-1").split()
            if len(parts) == 3:
                method, target = parts[0].upper(), parts[1]
        if method != "CONNECT":
            # A plain-HTTP request names a whole URL: record its host, refuse it.
            host = target.split("://", 1)[-1].split("/", 1)[0] if target else "?"
            h, p = _split_target(host if ":" in host else f"{host}:80")
            self._record(Tunnel(h, p, False, error=f"not a CONNECT: {method or 'unreadable'}"))
            conn.sendall(b"HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\n\r\n")
            return
        host, port = _split_target(target)
        if f"{host}:{port}" not in self.allow:
            self._record(Tunnel(host, port, False, error="not allowed"))
            conn.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            return
        t = Tunnel(host, port, True)
        try:
            upstream = socket.create_connection((host, port), timeout=30)
        except OSError as e:
            t.error, t.secs = f"connect failed: {e}"[:200], round(time.monotonic() - t0, 3)
            self._record(t)
            conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            return
        counts = [0, 0]
        try:
            upstream.settimeout(None)
            conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            if rest:
                upstream.sendall(rest)
                counts[0] += len(rest)
            _pump(conn, upstream, counts)
        finally:
            upstream.close()
            t.sent, t.received = counts
            t.secs = round(time.monotonic() - t0, 3)
            self._record(t)

    def summary(self) -> dict[str, int]:
        with self._lock:
            ok = [t for t in self.tunnels if t.allowed]
            return {"egress_tunnels": len(ok),
                    "egress_refused": len(self.tunnels) - len(ok),
                    "egress_bytes_sent": sum(t.sent for t in ok),
                    "egress_bytes_received": sum(t.received for t in ok)}

    def log_lines(self) -> str:
        """One JSON line per tunnel, for the end of the attempt's log."""
        with self._lock:
            return "".join(json.dumps({"type": "egress", **asdict(t)}) + "\n"
                           for t in self.tunnels)


class Relay(_Server):
    """Carries every connection to one fixed host:port, e.g. Ollama."""

    def __init__(self, host: str, port: int, unix: Path) -> None:
        super().__init__(unix)
        self.target = (host, port)

    def handle(self, conn: socket.socket) -> None:
        try:
            upstream = socket.create_connection(self.target, timeout=30)
        except OSError:
            return
        try:
            upstream.settimeout(None)
            _pump(conn, upstream, [0, 0])
        finally:
            upstream.close()
