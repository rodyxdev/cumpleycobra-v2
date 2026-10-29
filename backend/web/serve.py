"""BUILD y servido de una entrega web.

- Estática (sin package.json): se sirve el workspace tal cual (solo lectura).
- Node (con package.json + package-lock.json): se copia a un volumen y
  1. `npm ci --ignore-scripts` CON red (solo descarga; no corre preinstall/postinstall),
  2. `npm run build` SIN red, si el proyecto define el script `build`,
  3. se sirve la primera carpeta con index.html entre dist/, build/, out/, public/ o la raíz.
- El servidor (http.server de Python, código nuestro) corre en una red interna, sin internet,
  y solo lo alcanza el contenedor de Playwright.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, replace
from pathlib import Path

from backend.config import get_settings
from backend.sandbox import Mount, SandboxLimits, SandboxResult, SandboxUnavailable, ensure_image, run_in_sandbox
from backend.sandbox.runner import Service, _docker, start_service

NODE_IMAGE = "node:22-slim"
SITE_MOUNT = "/site"
_NPM_ENV = {"npm_config_cache": "/tmp/.npm", "npm_config_update_notifier": "false", "npm_config_fund": "false"}


@dataclass
class SiteBuild:
    kind: str                       # static | npm
    site_mount: Mount
    subdir: str = "."
    install: SandboxResult | None = None
    build: SandboxResult | None = None
    volume: str | None = None
    error: str | None = None
    scripts_ignored: bool | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def remove(self) -> None:
        if self.volume:
            _docker(["volume", "rm", "--force", self.volume])


def _limits(network_build: bool) -> SandboxLimits:
    s = get_settings()
    base = SandboxLimits.from_settings()
    return replace(base, timeout_s=s.build_timeout_s if network_build else s.sandbox_timeout_s * 2,
                   memory=s.build_memory, pids=512)


def prepare_site(workspace: Path) -> SiteBuild:
    if not (workspace / "package.json").exists():
        return SiteBuild("static", Mount(str(workspace), SITE_MOUNT))

    ensure_image(NODE_IMAGE)
    volume = f"cc-web-{uuid.uuid4().hex[:12]}"
    if _docker(["volume", "create", volume]).returncode != 0:
        raise SandboxUnavailable("no se pudo crear el volumen del sitio")
    site = SiteBuild("npm", Mount(volume, SITE_MOUNT, kind="volume"), volume=volume)
    rw = (Mount(volume, "/workspace", readonly=False, kind="volume"),)
    copy = _docker([
        "run", "--rm", "--network", "none", "--user", "0",
        "--mount", f"type=volume,source={volume},target=/workspace",
        "--mount", Mount(str(workspace), "/src").arg(),
        NODE_IMAGE, "sh", "-c", "cp -a /src/. /workspace/ && chown -R 65534:65534 /workspace",
    ], timeout_s=120)
    if copy.returncode != 0:
        site.error = f"no se pudo preparar el proyecto: {copy.stderr[-300:]}"
        return site

    with_tmp = dict(image=NODE_IMAGE, mounts=rw, env=_NPM_ENV, tmp_size="512m")
    site.install = run_in_sandbox(
        None,
        "cd /workspace && npm ci --ignore-scripts --no-audit --loglevel=error"
        " && (test -e /tmp/postinstall-ejecutado && echo SCRIPTS-EJECUTADOS || echo scripts-de-instalacion-ignorados)",
        None, network=True, limits=_limits(True), **with_tmp,
    )
    if site.install.exit_code != 0:
        site.error = "npm ci falló (¿lockfile desactualizado o paquete inexistente?)"
        return site
    site.scripts_ignored = "scripts-de-instalacion-ignorados" in site.install.stdout

    scripts = json.loads((workspace / "package.json").read_text(encoding="utf-8")).get("scripts", {})
    if "build" in scripts:
        site.build = run_in_sandbox(None, "cd /workspace && npm run build", None,
                                    network=False, limits=_limits(False), **with_tmp)
        if site.build.exit_code != 0 or site.build.timed_out:
            site.error = "npm run build falló (sin red)"
            return site

    found = run_in_sandbox(
        None,
        'cd /workspace && for d in dist build out public .; do [ -f "$d/index.html" ] && echo "$d" && exit 0; done; exit 1',
        None, network=False, limits=_limits(False), **with_tmp,
    )
    if found.exit_code != 0:
        site.error = "el build no produjo index.html (dist/, build/, out/, public/ o raíz)"
        return site
    site.subdir = found.stdout.strip().splitlines()[0]
    return site


def serve_site(site: SiteBuild, network: str) -> Service:
    limits = replace(SandboxLimits.from_settings(), timeout_s=600)
    return start_service(
        None,
        ["python", "-I", "-m", "http.server", "8080", "--bind", "0.0.0.0", "--directory", f"{SITE_MOUNT}/{site.subdir}"],
        image=get_settings().sandbox_image, limits=limits, network=network, alias="web", mounts=(site.site_mount,),
    )
