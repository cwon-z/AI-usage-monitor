"""Check tracked + non-ignored files without printing their contents.

This supplements (does not replace) Gitleaks and dependency auditing. It never
contacts providers, opens ignored credentials, stages files, or changes Git.
"""

from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_DIRS = {"secrets", "data", ".venv", ".claude", ".codex", "__pycache__"}
PRIVATE_NAMES = {"auth.json", "widget-api-token", ".DS_Store"}
PRIVATE_SUFFIXES = {
    ".db",
    ".sqlite",
    ".sqlite3",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".log",
}


def path_problems(name: str) -> list[str]:
    path = PurePosixPath(name)
    private = (
        bool(PRIVATE_DIRS.intersection(path.parts))
        or path.name in PRIVATE_NAMES
        or (path.name.startswith(".env") and path.name != ".env.example")
        or path.suffix in PRIVATE_SUFFIXES
        or path.name.endswith(
            ("-auth.json", "credentials.json", "-wal", "-shm", "-journal")
        )
    )
    return ["private/runtime file must not be published"] if private else []


def widget_problems(path: Path) -> list[str]:
    problems = []
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or any(
                PurePosixPath(name).is_absolute()
                or ".." in PurePosixPath(name).parts
                or "\\" in name
                for name in names
            ):
                return ["unsafe or duplicate archive member"]
            if any(entry.file_size > 20_000_000 for entry in archive.infolist()):
                return ["oversized archive member"]
            if sum(entry.file_size for entry in archive.infolist()) > 50_000_000:
                return ["oversized archive"]
            if archive.testzip() is not None:
                return ["archive integrity failed"]
            preset = json.loads(archive.read("preset.json"))
            globals_ = preset["preset_root"].get("globals_list", {})
            for key, expected in {
                "server": "",
                "token": "",
                "data": "{}",
                "bound": "",
                "checked": "0",
                "attempt": "0",
            }.items():
                if key in globals_ and globals_[key].get("value") != expected:
                    problems.append(f"configured widget global: {key}")
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile):
        problems.append("unreadable widget archive")
    return problems


def main() -> int:
    output = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
    )
    names = sorted(set(output.decode().split("\0")) - {""})
    failures = []
    for name in names:
        path = ROOT / name
        problems = path_problems(name)
        if path.is_symlink():
            problems.append("symlink requires manual publication review")
        elif path.is_file() and path.suffix == ".kwgt":
            problems.extend(widget_problems(path))
        failures.extend(f"{name}: {problem}" for problem in problems)
    if failures:
        print("Publication check FAILED (contents redacted):")
        print("\n".join(failures))
        return 1
    print(f"Publication hygiene passed: {len(names)} tracked/non-ignored files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
