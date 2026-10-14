"""Opt-in offline acceptance guard, inherited by Python/spawn children."""

import importlib.util
import json
import os
from pathlib import Path
import sys


if os.environ.get("R204_OFFLINE_GUARD") == "1":
    evidence = os.environ.get("R204_IMPORT_EVIDENCE")
    spec = importlib.util.find_spec("btc_microstructure")
    origin = None if spec is None else spec.origin
    expected = Path(os.environ["R204_EXPECTED_SOURCE"]).resolve()
    if origin is None or not Path(origin).resolve().is_relative_to(expected):
        os._exit(92)
    if evidence:
        record = {
            "pid": os.getpid(),
            "ppid": os.getppid(),
            "python": sys.version.split()[0],
            "executable": sys.executable,
            "package_origin": origin,
            "guard": "INET_SOCKET_DNS_DENIED_UNIX_IPC_ALLOWED",
        }
        descriptor = os.open(evidence, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(descriptor, (json.dumps(record) + "\n").encode())
        finally:
            os.close(descriptor)

    def deny_internet(event, args):
        denied = event in {
            "socket.getaddrinfo",
            "socket.gethostbyname",
            "socket.gethostbyaddr",
            "socket.getnameinfo",
        }
        # AF_INET=2; AF_INET6=30 on macOS, 10 on Linux. UNIX socketpair is
        # required by asyncio and remains local IPC, with no DNS or IP access.
        if event == "socket.__new__" and len(args) > 1:
            denied = args[1] in {2, 10, 30}
        if event in {"socket.connect", "socket.bind", "socket.sendto"}:
            denied = getattr(args[0], "family", None) in {2, 10, 30}
        if denied:
            raise PermissionError("R204_OFFLINE_GUARD: internet sockets and DNS forbidden")

    sys.addaudithook(deny_internet)
