"""Prove actual Python, subprocess, pytest and spawn import/guard inheritance."""

import importlib.util
import json
import multiprocessing as mp
import os
import socket
import sys


def observed():
    blocked = {}
    for name, operation in {
        "inet_socket": lambda: socket.socket(socket.AF_INET),
        "dns": lambda: socket.getaddrinfo("example.invalid", 443),
    }.items():
        try:
            operation()
        except PermissionError:
            blocked[name] = True
        else:
            blocked[name] = False
    return {
        "pid": os.getpid(),
        "python": sys.version,
        "executable": sys.executable,
        "package_origin": importlib.util.find_spec("btc_microstructure").origin,
        "network_denied": blocked,
    }


def child(connection):
    connection.send(observed())
    connection.close()


def test_pytest_source_and_guard():
    result = observed()
    assert all(result["network_denied"].values())
    assert result["package_origin"].startswith(os.environ["R204_EXPECTED_SOURCE"])
    print("PYTEST_IMPORT_EVIDENCE " + json.dumps(result))


if __name__ == "__main__":
    context = mp.get_context("spawn")
    parent, remote = context.Pipe(duplex=False)
    process = context.Process(target=child, args=(remote,))
    process.start()
    assert parent.poll(5), "spawn import probe timed out"
    result = {"parent": observed(), "spawn": parent.recv()}
    process.join(5)
    assert process.exitcode == 0
    assert all(all(row["network_denied"].values()) for row in result.values())
    print(json.dumps(result, indent=2))
