import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

from ai_usage_monitor import main

spec = importlib.util.spec_from_file_location(
    "publication", Path(__file__).resolve().parents[1] / "scripts/check_publication.py"
)
publication = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publication)


def test_standalone_server_is_private_by_default(monkeypatch):
    calls = []
    monkeypatch.setattr(main.uvicorn, "run", lambda *a, **kw: calls.append(kw))
    main.run()
    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["access_log"] is False


@pytest.mark.parametrize(
    "name",
    [
        "secrets/token",
        ".env",
        ".env.prod",
        "copy/codex-auth.json",
        "usage.sqlite3",
        "usage.db-wal",
        "private.pem",
    ],
)
def test_publication_rejects_private_paths(name):
    assert publication.path_problems(name)


@pytest.mark.parametrize(
    "name", [".env.example", "src/ai_usage_monitor/providers/credentials.py", "uv.lock"]
)
def test_publication_accepts_source_paths(name):
    assert not publication.path_problems(name)


def test_publication_rejects_configured_widget_without_echoing_token(tmp_path):
    path = tmp_path / "configured.kwgt"
    secret = "test-credential-never-print-this"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "preset.json",
            json.dumps({"preset_root": {"globals_list": {"token": {"value": secret}}}}),
        )
    problems = publication.widget_problems(path)
    assert problems == ["configured widget global: token"]
    assert secret not in str(problems)


def test_publication_rejects_archive_traversal(tmp_path):
    path = tmp_path / "unsafe.kwgt"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("../unsafe", "example")
    assert publication.widget_problems(path) == ["unsafe or duplicate archive member"]
