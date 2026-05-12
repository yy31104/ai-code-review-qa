from __future__ import annotations

import locale
import subprocess
import sys
from pathlib import Path

from schemas import TestResult


def run_tests(repo_path: str | Path) -> TestResult:
    repo = Path(repo_path).resolve()
    if not repo.exists() or not repo.is_dir():
        return TestResult(
            project_type="unknown",
            command="",
            passed=False,
            exit_code=1,
            error=f"Repository path does not exist: {repo}",
        )

    project_type, command, display_command, test_cwd = detect_test_command(repo)
    if not command:
        return TestResult(
            project_type=project_type,
            command="",
            passed=False,
            exit_code=1,
            output="",
            error="No supported test configuration found.",
        )

    try:
        result = subprocess.run(
            command,
            cwd=test_cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
        )
    except FileNotFoundError as exc:
        return TestResult(
            project_type=project_type,
            command=display_command,
            passed=False,
            exit_code=127,
            error=f"Test command was not found: {exc}",
        )
    except subprocess.TimeoutExpired as exc:
        output = _sanitize_test_output(
            _decode_output(exc.stdout) + _decode_output(exc.stderr),
            _find_repo_root(repo),
        )
        return TestResult(
            project_type=project_type,
            command=display_command,
            passed=False,
            exit_code=124,
            output=output,
            error="Test command timed out after 120 seconds.",
        )

    combined_output = "\n".join(
        part for part in [_decode_output(result.stdout), _decode_output(result.stderr)] if part
    ).strip()
    combined_output = _sanitize_test_output(combined_output, _find_repo_root(repo))
    return TestResult(
        project_type=project_type,
        command=display_command,
        passed=result.returncode == 0,
        exit_code=result.returncode,
        output=combined_output,
        error=None if result.returncode == 0 else "Automated tests failed.",
        test_summary=_extract_summary(combined_output, project_type),
    )


def detect_test_command(repo: Path) -> tuple[str, list[str], str, Path]:
    if any(repo.rglob("*.csproj")):
        return "dotnet", ["dotnet", "test"], "dotnet test", repo

    if (repo / "package.json").exists():
        return "node", ["npm", "test"], "npm test", repo

    python_project = _find_python_project(repo)
    if python_project:
        return "python", [sys.executable, "-m", "pytest"], "pytest", python_project

    return "unknown", [], "", repo


def _find_python_project(repo: Path) -> Path | None:
    ignored_dirs = {".git", ".pytest_cache", "__pycache__", "node_modules", ".venv"}
    markers = ["pytest.ini", "pyproject.toml", "requirements.txt"]

    for marker in markers:
        direct_marker = repo / marker
        if direct_marker.exists():
            return repo

        for path in sorted(repo.rglob(marker)):
            if any(part in ignored_dirs for part in path.parts):
                continue
            return path.parent

    return None


def _decode_output(output: bytes | str | None) -> str:
    if output is None:
        return ""
    if isinstance(output, str):
        return output

    encodings = ["utf-8", locale.getpreferredencoding(False), "cp936", "gbk"]
    for encoding in dict.fromkeys(encodings):
        try:
            return output.decode(encoding)
        except UnicodeDecodeError:
            continue

    return output.decode("utf-8", errors="replace")


def _find_repo_root(path: Path) -> Path:
    current = path.resolve()
    candidates = [current, *current.parents]
    for candidate in candidates:
        if (candidate / ".git").exists():
            return candidate
    return current


def _sanitize_test_output(output: str, repo_root: Path) -> str:
    if not output:
        return output

    root = str(repo_root.resolve())
    root_posix = repo_root.resolve().as_posix()
    sanitized = output
    for variant in sorted({root, root_posix}, key=len, reverse=True):
        sanitized = sanitized.replace(variant, "<repo>")

    return sanitized.replace("\\", "/")


def _extract_summary(output: str, project_type: str) -> str:
    """Pull a short human-readable result line from test runner output."""
    if project_type == "python":
        keywords = ("passed", "failed", "error", "no tests ran")
        for line in reversed(output.splitlines()):
            stripped = line.strip("= \t")
            if stripped and any(kw in stripped for kw in keywords):
                return stripped
    return ""
