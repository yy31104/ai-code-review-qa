from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


@dataclass
class GitDiffResult:
    diff: str
    changed_files: list[str]
    error: str | None = None


def read_git_diff(repo_path: str | Path) -> GitDiffResult:
    """Read git diff output and changed files for a repository path."""
    repo = Path(repo_path).resolve()
    if not repo.exists() or not repo.is_dir():
        return GitDiffResult("", [], f"Repository path does not exist: {repo}")

    if not _is_git_repo(repo):
        files = _list_project_files(repo)
        return GitDiffResult(
            diff="",
            changed_files=files,
            error="Path is not inside a git repository; using project file list instead.",
        )

    git_prefix = _git_prefix(repo)
    diff_result = _run_git(repo, ["diff", "--", "."])
    names_result = _run_git(repo, ["diff", "--name-only", "--", "."])
    status_result = _run_git(repo, ["status", "--porcelain", "--untracked-files=all", "--", "."])

    changed_files = set(_normalize_paths(_split_lines(names_result.stdout), repo, git_prefix))
    changed_files.update(_parse_status_files(status_result.stdout, repo, git_prefix))

    error_parts = []
    for result in (diff_result, names_result, status_result):
        if result.returncode != 0 and result.stderr:
            error_parts.append(result.stderr.strip())

    return GitDiffResult(
        diff=diff_result.stdout.strip(),
        changed_files=sorted(changed_files),
        error="; ".join(error_parts) or None,
    )


def _is_git_repo(repo: Path) -> bool:
    result = _run_git(repo, ["rev-parse", "--is-inside-work-tree"])
    return result.returncode == 0 and result.stdout.strip() == "true"


def _git_prefix(repo: Path) -> str:
    result = _run_git(repo, ["rev-parse", "--show-prefix"])
    if result.returncode != 0:
        return ""
    return result.stdout.strip().strip("/")


def _run_git(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


def _split_lines(value: str) -> list[str]:
    return [line.strip().replace("\\", "/") for line in value.splitlines() if line.strip()]


def _parse_status_files(status_output: str, repo: Path, git_prefix: str) -> list[str]:
    files: list[str] = []
    for line in status_output.splitlines():
        if len(line) < 4:
            continue

        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        path = path.strip('"').replace("\\", "/")

        if path in {".", "./"}:
            files.extend(_list_project_files(repo))
        elif path:
            files.append(_normalize_path(path, repo, git_prefix))

    return [file for file in files if _should_include(file)]


def _normalize_paths(paths: list[str], repo: Path, git_prefix: str) -> list[str]:
    normalized_paths = [_normalize_path(path, repo, git_prefix) for path in paths]
    return [path for path in normalized_paths if _should_include(path)]


def _normalize_path(path: str, repo: Path, git_prefix: str) -> str:
    path = path.replace("\\", "/")
    if path.startswith("./"):
        path = path[2:]
    if not path:
        return path

    if git_prefix and path.startswith(f"{git_prefix}/"):
        return path[len(git_prefix) + 1 :]

    candidate = (repo / path).resolve()
    try:
        return candidate.relative_to(repo).as_posix()
    except ValueError:
        return path


def _should_include(path: str) -> bool:
    if not path or path in {".", "./"}:
        return False

    ignored_dirs = {".git", ".pytest_cache", "__pycache__", "node_modules", "bin", "obj"}
    parts = set(PurePosixPath(path).parts)
    if parts.intersection(ignored_dirs):
        return False

    return not path.endswith((".pyc", ".pyo"))


def _list_project_files(repo: Path) -> list[str]:
    ignored_dirs = {".git", ".pytest_cache", "__pycache__", "node_modules", "bin", "obj"}
    files: list[str] = []

    for path in repo.rglob("*"):
        if path.is_dir():
            continue
        if any(part in ignored_dirs for part in path.parts):
            continue
        if path.suffix in {".pyc", ".pyo"}:
            continue
        files.append(path.relative_to(repo).as_posix())

    return sorted(files)
