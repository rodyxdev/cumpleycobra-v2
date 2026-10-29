import io
import zipfile

import pytest


@pytest.fixture(autouse=True)
def _isolated_work_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WORK_DIR", str(tmp_path / "work"))


def make_zip(entries: dict[str, bytes], *, symlinks: dict[str, str] | None = None) -> bytes:
    """ZIP en memoria. `entries` admite nombres arbitrarios (incluidos maliciosos)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            # ZipInfo explícito para que zipfile no "arregle" nombres maliciosos.
            zf.writestr(zipfile.ZipInfo(name), data, compress_type=zipfile.ZIP_DEFLATED)
        for name, target in (symlinks or {}).items():
            info = zipfile.ZipInfo(name)
            info.create_system = 3  # Unix
            info.external_attr = (0o120777 << 16)  # S_IFLNK
            zf.writestr(info, target)
    return buf.getvalue()


@pytest.fixture
def zip_factory(tmp_path):
    counter = iter(range(10_000))

    def _make(entries, **kw):
        path = tmp_path / f"upload-{next(counter)}.zip"
        path.write_bytes(make_zip(entries, **kw))
        return path

    return _make


ARBITER_TOKEN = "token-de-arbitro-para-tests"


@pytest.fixture(autouse=True)
def _isolated_chain(monkeypatch):
    """Ningún test toca testnet: se borra la configuración real y se usan billeteras aleatorias."""
    from stellar_sdk import Keypair

    for var in ("ESCROW_CONTRACT_ID", "ARBITER_SECRET", "USDC_CONTRACT_ID", "GITHUB_APP_ID",
                "GITHUB_APP_PRIVATE_KEY", "GITHUB_APP_PRIVATE_KEY_PATH"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ARBITER_TOKEN", ARBITER_TOKEN)
    monkeypatch.setenv("DEMO_CLIENT_SECRET", Keypair.random().secret)
    monkeypatch.setenv("DEMO_FREELANCER_SECRET", Keypair.random().secret)
