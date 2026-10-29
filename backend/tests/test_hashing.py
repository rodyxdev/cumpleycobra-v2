import os
import time

from backend.ingest.hashing import hash_workspace


def write_tree(root, files: dict[str, bytes]):
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


FILES = {"README.md": b"# hola\n", "src/app.py": b"print('x')\n", "src/pkg/__init__.py": b""}


def test_same_content_same_hash_regardless_of_creation_order(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    write_tree(a, FILES)
    write_tree(b, dict(reversed(list(FILES.items()))))
    assert hash_workspace(a) == hash_workspace(b)
    assert hash_workspace(a).startswith("sha256:")


def test_hash_is_stable_across_calls_and_ignores_mtime(tmp_path):
    write_tree(tmp_path, FILES)
    first = hash_workspace(tmp_path)
    past = time.time() - 86_400
    os.utime(tmp_path / "README.md", (past, past))
    assert hash_workspace(tmp_path) == first


def test_content_change_changes_hash(tmp_path):
    write_tree(tmp_path, FILES)
    before = hash_workspace(tmp_path)
    (tmp_path / "src/app.py").write_bytes(b"print('y')\n")
    assert hash_workspace(tmp_path) != before


def test_rename_changes_hash(tmp_path):
    write_tree(tmp_path, FILES)
    before = hash_workspace(tmp_path)
    (tmp_path / "README.md").rename(tmp_path / "LEEME.md")
    assert hash_workspace(tmp_path) != before


def test_moving_bytes_between_files_changes_hash(tmp_path):
    # Evita colisiones por concatenación: "ab"+"" ≠ "a"+"b".
    a, b = tmp_path / "a", tmp_path / "b"
    write_tree(a, {"x": b"ab", "y": b""})
    write_tree(b, {"x": b"a", "y": b"b"})
    assert hash_workspace(a) != hash_workspace(b)


def test_git_dir_and_empty_dirs_are_ignored(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    write_tree(a, FILES)
    write_tree(b, {**FILES, ".git/HEAD": b"ref: refs/heads/main\n"})
    (b / "empty").mkdir()
    assert hash_workspace(a) == hash_workspace(b)


def test_empty_workspace_has_fixed_hash(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    assert hash_workspace(a) == hash_workspace(b)
