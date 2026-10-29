"""Ejecución aislada de comandos en un contenedor Docker.

Aislamiento aplicado a cada ejecución:
- `--network none`: sin red.
- `--cpus`, `--memory` (= `--memory-swap`, sin swap) y `--pids-limit`.
- `--user 65534:65534` (nobody), `--cap-drop ALL`, `--security-opt no-new-privileges`.
- raíz del contenedor de solo lectura (`--read-only`) con un `/tmp` tmpfs pequeño.
- workspace montado en `/workspace` de solo lectura y `/out` como único directorio escribible.
- límite de tiempo de pared: al vencer se mata el contenedor por nombre (matar el
  proceso `docker` del host no detiene el contenedor).

stdout/stderr se leen en streaming y se conservan sólo los primeros N bytes, para
que una salida infinita no agote la memoria del backend.
"""

from __future__ import annotations

import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from backend.config import get_settings

SANDBOX_UID = "65534:65534"
WORKSPACE_MOUNT = "/workspace"
OUTPUT_MOUNT = "/out"
_CHUNK = 64 * 1024


class SandboxUnavailable(RuntimeError):
    """Docker no está disponible o la imagen no se pudo preparar."""


@dataclass(frozen=True)
class SandboxLimits:
    cpus: float
    memory: str
    pids: int
    timeout_s: float
    output_limit: int

    @classmethod
    def from_settings(cls) -> "SandboxLimits":
        s = get_settings()
        return cls(s.sandbox_cpus, s.sandbox_memory, s.sandbox_pids, s.sandbox_timeout_s, s.sandbox_output_limit)


@dataclass(frozen=True)
class SandboxResult:
    exit_code: int | None  # None si se mató por timeout
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    duration_s: float
    timed_out: bool
    oom_killed: bool
    image: str
    command: str


class _CappedReader(threading.Thread):
    """Drena un pipe completo guardando sólo los primeros `limit` bytes."""

    def __init__(self, stream, limit: int):
        super().__init__(daemon=True)
        self.stream, self.limit = stream, limit
        self.buf = bytearray()
        self.total = 0

    def run(self) -> None:
        while chunk := self.stream.read(_CHUNK):
            self.total += len(chunk)
            room = self.limit - len(self.buf)
            if room > 0:
                self.buf += chunk[:room]

    @property
    def truncated(self) -> bool:
        return self.total > self.limit

    def text(self) -> str:
        return self.buf.decode("utf-8", errors="replace")


def _docker(args: list[str], timeout_s: float = 30) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout_s)
    except FileNotFoundError as exc:
        raise SandboxUnavailable("no se encontró el binario 'docker'") from exc
    except subprocess.TimeoutExpired as exc:
        raise SandboxUnavailable(f"'docker {args[0]}' no respondió en {timeout_s}s") from exc


def docker_available() -> bool:
    try:
        return _docker(["version", "--format", "{{.Server.Version}}"], timeout_s=15).returncode == 0
    except SandboxUnavailable:
        return False


def ensure_image(image: str, pull_timeout_s: float = 600) -> None:
    """Descarga la imagen si falta (fuera del tiempo medido de la ejecución)."""
    if _docker(["image", "inspect", image]).returncode == 0:
        return
    pulled = _docker(["pull", image], timeout_s=pull_timeout_s)
    if pulled.returncode != 0:
        raise SandboxUnavailable(f"no se pudo descargar {image}: {pulled.stderr.strip()[-300:]}")


def build_docker_args(
    name: str, image: str, workspace: Path, output_dir: Path, command: str, limits: SandboxLimits
) -> list[str]:
    return [
        "docker", "run",
        "--name", name,
        "--pull", "never",
        "--network", "none",
        "--cpus", str(limits.cpus),
        "--memory", limits.memory,
        "--memory-swap", limits.memory,
        "--pids-limit", str(limits.pids),
        "--user", SANDBOX_UID,
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--read-only",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m",
        "--mount", f"type=bind,source={workspace.resolve()},target={WORKSPACE_MOUNT},readonly",
        "--mount", f"type=bind,source={output_dir.resolve()},target={OUTPUT_MOUNT}",
        "--workdir", WORKSPACE_MOUNT,
        "--env", "HOME=/tmp",
        "--env", "PYTHONDONTWRITEBYTECODE=1",
        "--env", f"OUTPUT_DIR={OUTPUT_MOUNT}",
        image,
        "sh", "-c", command,
    ]


def run_in_sandbox(
    workspace: Path,
    command: str,
    output_dir: Path,
    *,
    image: str | None = None,
    limits: SandboxLimits | None = None,
) -> SandboxResult:
    """Ejecuta `command` (vía `sh -c`) dentro de un contenedor aislado."""
    image = image or get_settings().sandbox_image
    limits = limits or SandboxLimits.from_settings()
    for p in (workspace, output_dir):
        if "," in str(p.resolve()):
            raise ValueError(f"ruta no montable (contiene ','): {p}")
    output_dir.mkdir(parents=True, exist_ok=True)
    ensure_image(image)

    name = f"cc-sbx-{uuid.uuid4().hex[:12]}"
    args = build_docker_args(name, image, workspace, output_dir, command, limits)
    started = time.monotonic()
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
    out = _CappedReader(proc.stdout, limits.output_limit)
    err = _CappedReader(proc.stderr, limits.output_limit)
    out.start()
    err.start()

    timed_out = False
    try:
        proc.wait(timeout=limits.timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        _docker(["kill", name])
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    duration = time.monotonic() - started
    out.join(timeout=5)
    err.join(timeout=5)

    oom = _docker(["inspect", "--format", "{{.State.OOMKilled}}", name]).stdout.strip() == "true"
    _docker(["rm", "--force", name])

    return SandboxResult(
        exit_code=None if timed_out else proc.returncode,
        stdout=out.text(),
        stderr=err.text(),
        stdout_truncated=out.truncated,
        stderr_truncated=err.truncated,
        duration_s=round(duration, 3),
        timed_out=timed_out,
        oom_killed=oom,
        image=image,
        command=command,
    )
