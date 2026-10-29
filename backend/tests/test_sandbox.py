import time
from pathlib import Path

import pytest

from backend.sandbox import SandboxLimits, build_docker_args, docker_available, run_in_sandbox

LIMITS = SandboxLimits(cpus=0.5, memory="256m", pids=64, timeout_s=5, output_limit=1024)

requires_docker = pytest.mark.skipif(not docker_available(), reason="Docker no está disponible")


def test_docker_args_enforce_isolation(tmp_path):
    ws, out = tmp_path / "ws", tmp_path / "out"
    args = build_docker_args("cc-sbx-test", "img:1", ws, out, "echo hi", LIMITS)
    joined = " ".join(args)
    assert ["--network", "none"] == args[args.index("--network"): args.index("--network") + 2]
    assert "--read-only" in args
    assert "--cap-drop ALL" in joined
    assert "--security-opt no-new-privileges" in joined
    assert "--user 65534:65534" in joined
    assert "--cpus 0.5" in joined
    assert "--memory 256m --memory-swap 256m" in joined
    assert "--pids-limit 64" in joined
    assert f"source={ws.resolve()},target=/workspace,readonly" in joined
    out_mount = next(a for a in args if "target=/out" in a)
    assert "readonly" not in out_mount
    assert args[-3:] == ["sh", "-c", "echo hi"]


@pytest.fixture
def workspace(tmp_path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "hello.txt").write_bytes(b"hola desde el workspace\n")  # bytes: write_text pondría \r\n en Windows
    return ws


@requires_docker
def test_timeout_kills_container(workspace, tmp_path):
    started = time.monotonic()
    result = run_in_sandbox(workspace, "echo start; sleep 60; echo never", tmp_path / "out", limits=LIMITS)
    elapsed = time.monotonic() - started
    assert result.timed_out is True
    assert result.exit_code is None
    assert "start" in result.stdout and "never" not in result.stdout
    assert elapsed < LIMITS.timeout_s + 15  # se mató, no esperó los 60 s
    assert 4.5 <= result.duration_s < LIMITS.timeout_s + 15


@requires_docker
def test_runs_command_and_captures_output(workspace, tmp_path):
    result = run_in_sandbox(workspace, "cat hello.txt; echo err >&2; exit 3", tmp_path / "out", limits=LIMITS)
    assert result.exit_code == 3
    assert result.stdout == "hola desde el workspace\n"
    assert result.stderr == "err\n"
    assert result.timed_out is False


@requires_docker
def test_isolation_inside_container(workspace, tmp_path):
    out = tmp_path / "out"
    cmd = (
        "id -u; "
        "touch /workspace/x 2>/dev/null && echo ws-writable || echo ws-readonly; "
        "touch /etc/x 2>/dev/null && echo root-writable || echo root-readonly; "
        "echo result > /out/result.txt && echo out-writable; "
        "python -c \"import socket; socket.create_connection(('1.1.1.1', 53), timeout=3)\" "
        "2>/dev/null && echo net-on || echo net-off"
    )
    result = run_in_sandbox(workspace, cmd, out, limits=LIMITS)
    lines = result.stdout.split()
    assert lines == ["65534", "ws-readonly", "root-readonly", "out-writable", "net-off"]
    assert (out / "result.txt").read_text().strip() == "result"
    assert not (workspace / "x").exists()


@requires_docker
def test_output_is_truncated(workspace, tmp_path):
    result = run_in_sandbox(workspace, "yes abcdefgh | head -c 100000", tmp_path / "out", limits=LIMITS)
    assert result.exit_code == 0
    assert len(result.stdout.encode()) == LIMITS.output_limit
    assert result.stdout_truncated is True
    assert result.stderr_truncated is False
