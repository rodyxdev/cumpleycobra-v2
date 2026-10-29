"""Ejecuta el runner de Playwright (confiable) contra la entrega servida en una red interna."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from backend.contract.models import TaskDraft
from backend.sandbox import Mount, SandboxLimits, SandboxResult, SandboxUnavailable, run_in_sandbox
from backend.sandbox.runner import InternalNetwork, _docker
from backend.web.serve import SiteBuild, serve_site

RUNNER = Path(__file__).parent / "runtime" / "web_runner.py"
RUNNER_IMAGE_DIR = Path(__file__).parent / "runner_image"
EVIDENCE_LIMIT_BYTES = 256 * 1024 * 1024


@dataclass
class WebRun:
    sandbox: SandboxResult
    results: dict | None
    evidence_dir: Path
    server_logs: str
    server_state: dict


def web_spec(draft: TaskDraft) -> dict:
    """Lo que necesita el runner: páginas y chequeos. Vive solo en el lado confiable."""
    return {
        "base_url": "http://web:8080",
        "pages": draft.web.pages,
        "checks": [
            {"criterion_id": c.id, "kind": c.web_check.kind, "page": c.web_check.page, "args": c.web_check.args}
            for c in draft.criteria if c.type == "test" and c.web_check
        ],
    }


def playwright_image() -> str:
    digest = hashlib.sha256((RUNNER_IMAGE_DIR / "Dockerfile").read_bytes()).hexdigest()[:12]
    tag = f"cumpleycobra-web-runner:{digest}"
    if _docker(["image", "inspect", tag]).returncode != 0:
        built = _docker(["build", "--tag", tag, str(RUNNER_IMAGE_DIR)], timeout_s=900)
        if built.returncode != 0:
            raise SandboxUnavailable(f"no se pudo construir {tag}: {built.stderr.strip()[-500:]}")
    return tag


def run_web_checks(site: SiteBuild, draft: TaskDraft, evidence_dir: Path) -> WebRun:
    image = playwright_image()
    base = SandboxLimits.from_settings()
    limits = replace(base, memory="2g", pids=1024, timeout_s=300, cpus=max(base.cpus, 2.0),
                     out_tmpfs_bytes=EVIDENCE_LIMIT_BYTES)
    with tempfile.TemporaryDirectory(prefix="cc-web-") as tmp:
        tmp = Path(tmp)
        (tmp / "rt").mkdir()
        shutil.copy2(RUNNER, tmp / "rt" / "web_runner.py")
        (tmp / "spec").mkdir()
        (tmp / "spec" / "spec.json").write_text(json.dumps(web_spec(draft), ensure_ascii=False), encoding="utf-8")
        network = InternalNetwork()
        server = None
        try:
            server = serve_site(site, network.name)
            result = run_in_sandbox(
                None, "python3 /cc/web_runner.py /spec/spec.json /out", evidence_dir,
                image=image, limits=limits, network=network.name,
                mounts=(Mount(str(tmp / "rt"), "/cc"), Mount(str(tmp / "spec"), "/spec")),
                env={"HOME": "/tmp", "PLAYWRIGHT_BROWSERS_PATH": "/ms-playwright"},
                extra=["--shm-size", "512m"], tmp_size="1g",
            )
            report = evidence_dir / "results.json"
            results = json.loads(report.read_text(encoding="utf-8")) if report.exists() else None
            return WebRun(result, results, evidence_dir, server.logs(), server.state())
        finally:
            if server is not None:
                server.remove()
            network.remove()
