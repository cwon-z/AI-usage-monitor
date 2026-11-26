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
    assert calls[0]["server_header"] is False


@pytest.mark.parametrize(
    "name",
    [
        "secrets/token",
        "mcp-api-token",
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


@pytest.mark.parametrize(
    "text",
    [
        'API = "http://100.127.0.5:9010/api/v1"',
        "ssh 192.168.1.20",
        "bind 10.1.2.3:8000",
        "upstream 172.20.0.4;",
    ],
)
def test_publication_rejects_private_addresses_without_echoing_them(tmp_path, text):
    path = tmp_path / "script.py"
    path.write_text("# first line\n" + text + "\n")
    assert publication.address_problems(path) == ["private network address on line 2"]


def test_publication_accepts_public_documentation_and_range_literals(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text(
        "127.0.0.1 0.0.0.0 203.0.113.10 198.51.100.7 8.8.8.8 "
        "100.63.255.255 100.128.0.1 172.32.0.1 10.0.0.0/8 version 10.2.3.4.5\n"
    )
    assert publication.address_problems(path) == []
    binary = tmp_path / "image.png"
    binary.write_bytes(b"10.1.2.3")
    assert publication.address_problems(binary) == []
