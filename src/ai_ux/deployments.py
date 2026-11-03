"""Immutable process release identity and explicit deployment acceptance records."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from pydantic import Field, model_validator

from . import __version__
from .schema import StrictModel


class Release(StrictModel):
    version: str = Field(min_length=1, max_length=120)
    commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    built_at: str
    target: str = Field(min_length=1, max_length=100)


class DeploymentRecord(Release):
    status: str
    checks: dict[str, bool]
    coverage: dict | None = None

    @model_validator(mode="after")
    def acceptance(self):
        if self.status not in {"staged", "success", "failed"}:
            raise ValueError("invalid deployment status")
        if self.status == "success" and not all(
            self.checks.get(k) is True for k in ["health", "pages", "assets", "version"]
        ):
            raise ValueError("success requires passing health/pages/assets/version checks")
        if self.coverage is not None:
            if self.coverage.get("commit") != self.commit:
                raise ValueError("coverage must identify the deployed commit")
            for k in ["line_percent", "branch_percent"]:
                value = self.coverage.get(k)
                if not isinstance(value, (int, float)) or not 0 <= value <= 100:
                    raise ValueError("invalid coverage percentage")
        return self


def process_identity(release_file: Path | None):
    if release_file:
        try:
            return {
                "state": "release",
                "release": Release.model_validate_json(release_file.read_text()).model_dump(),
            }
        except (OSError, ValueError):
            return {"state": "unknown", "release": None, "warning": "发布元信息无法读取或格式错误"}
    commit = None
    dirty = None
    try:
        root = Path(__file__).resolve().parents[2]
        commit = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "-C", str(root), "status", "--porcelain"],
                capture_output=True,
                text=True,
                timeout=3,
                check=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError):
        pass
    return {
        "state": "development",
        "release": None,
        "package_version": __version__,
        "source_commit": commit,
        "source_dirty": dirty,
    }


def snapshot(identity, records):
    release = identity.get("release")
    state = identity["state"]
    result = []
    previous = {}
    for r in reversed(records):
        row = dict(r)
        base = previous.get(row["target"])
        row["compare_url"] = (
            f"https://github.com/zhoutingunl/uxpilot/compare/{base}...{row['commit_sha']}"
            if base and base != row["commit_sha"]
            else None
        )
        row["commit_url"] = "https://github.com/zhoutingunl/uxpilot/commit/" + row["commit_sha"]
        result.append(row)
        if row["status"] == "success":
            previous[row["target"]] = row["commit_sha"]
    if release:
        match = next(
            (
                r
                for r in records
                if r["target"] == release["target"]
                and r["version"] == release["version"]
                and r["commit_sha"] == release["commit"]
            ),
            None,
        )
        state = "unrecorded" if not match else match["status"]
        successful = next(
            (r for r in records if r["target"] == release["target"] and r["status"] == "success"), None
        )
        if not match and successful:
            state = "mismatch"
    return {
        "current": identity,
        "state": state,
        "records": list(reversed(result)),
        "note": "开发启动不生成成功发布记录；成功需明确验收，覆盖率必须绑定同一提交。",
    }


def record_file(store, path):
    record = DeploymentRecord.model_validate_json(Path(path).read_text()).model_dump()
    store.record_deployment({**record, "at": time.time()})
    return record
