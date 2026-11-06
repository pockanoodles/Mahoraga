"""Inside a Linux sandbox: loopback ports that lead out through Unix sockets.

    python -I bridge.py PORT=SOCKET [PORT=SOCKET ...] -- CMD [ARG ...]

The sandbox has its own network namespace, so nothing in it can reach the
host's network or its loopback. Each PORT here listens on the namespace's own
127.0.0.1 and carries every connection to SOCKET, which the harness serves
from outside (egress.py): the cloud arm's proxy, or a relay to Ollama. Then it
runs CMD and exits with its status.

Run by path with `-I`, so it imports nothing from the harness or the clone:
standard library only.
"""
from __future__ import annotations

import select
import signal
import socket
import subprocess
import sys
import threading


def _pump(a: socket.socket, b: socket.socket) -> None:
    pair = {a: b, b: a}
    try:
        while True:
            ready, _, _ = select.select([a, b], [], [], 60)
            for s in ready:
                data = s.recv(65536)
                if not data:
                    return
                pair[s].sendall(data)
    except (OSError, ValueError):
        return
    finally:
        a.close()
        b.close()


def _serve(listener: socket.socket, path: str) -> None:
    while True:
        try:
            conn, _ = listener.accept()
        except OSError:
            return
        out = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            out.connect(path)
        except OSError:
            conn.close()
            out.close()
            continue
        threading.Thread(target=_pump, args=(conn, out), daemon=True).start()


def main(argv: list[str]) -> int:
    if "--" not in argv:
        print("usage: bridge.py PORT=SOCKET ... -- CMD ...", file=sys.stderr)
        return 2
    i = argv.index("--")
    forwards, cmd = argv[:i], argv[i + 1:]
    for spec in forwards:
        port, _, path = spec.partition("=")
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", int(port)))
        listener.listen(64)  # listening before CMD starts, so its first request can't race
        threading.Thread(target=_serve, args=(listener, path), daemon=True).start()
    child = subprocess.Popen(cmd)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda s, f: child.send_signal(s))
    return child.wait()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
