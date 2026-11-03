"""Bounded read-only repository inspection. Never install or execute target code."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .policy import scrub

EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "artifacts",
    "reports",
    "coverage",
    ".next",
    "__pycache__",
    ".secrets",
}
SUFFIXES = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".vue",
    ".svelte",
    ".html",
    ".css",
    ".scss",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".rb",
    ".php",
    ".md",
    ".mdx",
    ".json",
    ".toml",
    ".yml",
    ".yaml",
}
DENY_FILE = re.compile(
    r"(^\.|secret|credential|password|token|cookie|lock\.|\.lock$|\.min\.|config\.json$)", re.I
)
MAX_FILE_BYTES = 256_000
MAX_FILES = 2500
MAX_SCAN_BYTES = 8_000_000


def git(root: Path, *args: str) -> str | None:
    try:
        r = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=10, check=True
        )
        return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def validate_git_url(value: str) -> str:
    if re.fullmatch(r"git@[A-Za-z0-9.-]+:[A-Za-z0-9_./-]+", value):
        return value
    p = urlsplit(value)
    if p.scheme not in {"https", "ssh"} or not p.hostname or p.password or p.query or p.fragment:
        raise ValueError("repo must be an existing local directory or a credential-free HTTPS/SSH Git URL")
    if p.username and not (p.scheme == "ssh" and p.username == "git"):
        raise ValueError("use existing Git credentials; do not embed credentials in repository URL")
    if not re.fullmatch(r"/[A-Za-z0-9_./%-]+", p.path):
        raise ValueError("invalid Git repository path")
    return value


def safe_source_path(root: Path, relative: str) -> Path:
    raw = root / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("source path outside repository")
    current = root
    for part in Path(relative).parts:
        current /= part
        if current.is_symlink():
            raise ValueError("source symlinks are excluded")
    resolved = raw.resolve()
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        raise ValueError("source file missing or outside repository")
    return resolved


def eligible(relative: str) -> bool:
    parts = Path(relative).parts
    if any(p in EXCLUDE_DIRS or p.startswith(".") for p in parts[:-1]):
        return False
    name = parts[-1]
    return (
        Path(name).suffix.lower() in SUFFIXES
        and not DENY_FILE.search(name)
        and name.lower() not in {"agents.md", "skill.md", "package-lock.json", "pnpm-lock.yaml"}
    )


def enumerate_files(root: Path) -> tuple[list[str], bool]:
    """Respect gitignore through rg; cap inventory, even for huge source trees."""
    if shutil.which("rg"):
        cmd = ["rg", "--files", "-g", "!.git/**"]
        for directory in sorted(EXCLUDE_DIRS):
            cmd.extend(["-g", f"!**/{directory}/**"])
        r = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=20)
        candidates = r.stdout.splitlines()
    else:
        candidates = []
        for base, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [
                d
                for d in dirs
                if d not in EXCLUDE_DIRS and not d.startswith(".") and not (Path(base) / d).is_symlink()
            ]
            candidates.extend(str((Path(base) / f).relative_to(root)) for f in files)
            if len(candidates) > MAX_FILES * 4:
                break
    result = []
    for relative in sorted(candidates):
        if not eligible(relative):
            continue
        try:
            f = safe_source_path(root, relative)
            if f.stat().st_size <= MAX_FILE_BYTES:
                result.append(relative)
        except (ValueError, OSError):
            continue
        if len(result) > MAX_FILES:
            return result[:MAX_FILES], True
    return result, False


@dataclass
class Repository:
    root: Path
    origin: str
    cloned: bool
    files: list[str]
    commit: str | None
    dirty: bool | None
    truncated: bool
    documents: list[dict] = field(default_factory=list)

    def read(self, relative: str) -> str:
        if relative not in self.files:
            raise ValueError("file outside bounded source inventory")
        f = safe_source_path(self.root, relative)
        if f.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("source file grew beyond limit")
        text = f.read_text(encoding="utf-8")
        if "\0" in text:
            raise ValueError("binary source file excluded")
        return scrub(text)

    def metadata(self):
        return {
            "origin": self.origin,
            "local_path": str(self.root),
            "cloned": self.cloned,
            "commit": self.commit,
            "dirty": self.dirty,
            "files": self.files,
            "inventory_truncated": self.truncated,
            "documents": self.documents,
            "deployment_match": "unknown; source version is not proof of deployed version",
        }

    def snippets(self, issues: list[dict], observations: list[dict], limit=10):
        """Find relevant literals without handing the repository to Explorer or Judge."""
        terms = set()
        for o in observations:
            for n in o.get("nodes", []):
                if n.get("dom_id") and len(n["dom_id"]) > 2:
                    terms.add(n["dom_id"])
                name = n.get("name", "").strip()
                if 3 <= len(name) <= 35:
                    terms.add(name)
        prose = json.dumps(issues, ensure_ascii=False)
        terms.update(re.findall(r"[A-Za-z_][A-Za-z0-9_-]{3,40}", prose))
        terms.update(re.findall(r"[一-鿿]{2,16}", prose))
        scored = []
        scanned = 0
        for relative in self.files:
            if Path(relative).suffix in {".md", ".mdx"}:
                continue
            try:
                text = self.read(relative)
            except (OSError, UnicodeError, ValueError):
                continue
            scanned += len(text.encode())
            if scanned > MAX_SCAN_BYTES:
                break
            lines = text.splitlines()
            matches = [
                (i, sum(min(3, len(t) / 8) for t in terms if t.lower() in line.lower()))
                for i, line in enumerate(lines)
                if len(line) < 1800
            ]
            matches = [(i, score) for i, score in matches if score]
            if not matches:
                continue
            best, score = max(matches, key=lambda item: item[1])
            start, end = max(0, best - 6), min(len(lines), best + 9)
            excerpt = "\n".join(f"{i + 1}: {lines[i]}" for i in range(start, end))
            scored.append(
                (
                    score,
                    {
                        "path": relative,
                        "start_line": start + 1,
                        "end_line": end,
                        "excerpt": excerpt[:5000],
                        "sha256": hashlib.sha256(text.encode()).hexdigest(),
                    },
                )
            )
        return [s for _, s in sorted(scored, key=lambda pair: pair[0], reverse=True)[:limit]]


def prepare_repository(value: str, destination: Path, ref: str | None = None) -> Repository:
    local = Path(value).expanduser()
    cloned = not local.is_dir()
    if not cloned:
        if ref:
            raise ValueError("--ref only applies to remote clones; local worktrees are never checked out")
        root = local.resolve()
        origin = str(root)
    else:
        origin = validate_git_url(value)
        if ref and (ref.startswith("-") or not re.fullmatch(r"[A-Za-z0-9_./-]+", ref)):
            raise ValueError("ref must be a branch or tag name")
        root = destination.resolve()
        root.parent.mkdir(parents=True, exist_ok=True)
        if root.exists():
            raise ValueError("clone destination already exists")
        cmd = [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "protocol.file.allow=never",
            "-c",
            "filter.lfs.smudge=",
            "-c",
            "filter.lfs.required=false",
            "clone",
            "--depth",
            "1",
            "--no-recurse-submodules",
        ]
        if ref:
            cmd.extend(["--branch", ref])
        cmd.extend(["--", origin, str(root)])
        env = {
            **os.environ,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_LFS_SKIP_SMUDGE": "1",
            "GIT_SSH_COMMAND": "ssh -oBatchMode=yes",
        }
        try:
            subprocess.run(cmd, env=env, capture_output=True, timeout=120, check=True)
        except (OSError, subprocess.SubprocessError):
            raise ValueError("Git clone failed; check URL, existing Git credentials and network") from None
    files, truncated = enumerate_files(root)
    status = git(root, "status", "--porcelain")
    repo = Repository(
        root,
        origin,
        cloned,
        files,
        git(root, "rev-parse", "HEAD"),
        None if status is None else bool(status),
        truncated,
    )
    budget = 18000
    docs = [f for f in files if Path(f).name.lower().startswith("readme")]
    docs += [
        f
        for f in files
        if re.search(r"(product|design|getting.started|overview)", f, re.I)
        and Path(f).suffix == ".md"
        and f not in docs
    ]
    for relative in docs[:4]:
        try:
            text = repo.read(relative)[: min(6000, budget)]
        except (OSError, UnicodeError, ValueError):
            continue
        repo.documents.append({"path": relative, "text": text})
        budget -= len(text)
        if budget <= 0:
            break
    return repo
