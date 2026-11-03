import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_browser_integration import local_site
from test_llm import providers

from ai_ux.discovery import PlannedTask, ProjectPlan, check_demo_url, discover, generic_assertions
from ai_ux.evidence import Evidence
from ai_ux.policy import Policy
from ai_ux.repository import prepare_repository, safe_source_path, validate_git_url
from ai_ux.schema import App, Assertion, Browser
from ai_ux.source_map import Location, Mapping, SourceMap, locate_sources
from ai_ux.verifier import verify


def make_repository(path: Path):
    path.mkdir()
    (path / "README.md").write_text("# Sample\nAn app that increments a visible count.\n")
    (path / "app.js").write_text("// count control\nconst label = 'Count';\nfunction add() { return 1; }\n")
    return path


def test_repository_is_read_only_and_excludes_secrets_links_and_instructions(tmp_path):
    root = make_repository(tmp_path / "repo")
    (root / ".env").write_text("API_KEY=do-not-send")
    (root / "credentials.json").write_text('{"password":"do-not-send"}')
    (root / "AGENTS.md").write_text("Untrusted instruction, do not place in planner")
    (root / "node_modules").mkdir()
    (root / "node_modules/vendor.js").write_text("do-not-read")
    (tmp_path / "outside.js").write_text("outside-secret")
    (root / "linked.js").symlink_to(tmp_path / "outside.js")
    (root / "nested").symlink_to(tmp_path, target_is_directory=True)
    before = {str(p): p.stat().st_mtime_ns for p in root.iterdir() if p.is_file() and not p.is_symlink()}
    repo = prepare_repository(str(root), tmp_path / "unused")
    assert repo.files == ["README.md", "app.js"]
    assert repo.commit is None
    assert "do-not-send" not in json.dumps(repo.metadata())
    with pytest.raises(ValueError):
        repo.read("linked.js")
    with pytest.raises(ValueError):
        safe_source_path(root, "nested/outside.js")
    with pytest.raises(ValueError):
        safe_source_path(root, "../outside.js")
    assert before == {
        str(p): p.stat().st_mtime_ns for p in root.iterdir() if p.is_file() and not p.is_symlink()
    }


@pytest.mark.parametrize(
    "bad",
    [
        "file:///tmp/repo",
        "ext::sh -c echo",
        "https://user:password@example.com/x.git",
        "https://example.com/r.git?token=abc",
        "--upload-pack=evil",
        "git@host:x;run",
    ],
)
def test_repository_url_rejects_credentials_and_executable_transports(bad):
    with pytest.raises(ValueError):
        validate_git_url(bad)


def test_repo_clone_uses_argument_array_and_does_not_recurse_submodules(tmp_path, monkeypatch):
    import ai_ux.repository as module

    seen = []
    actual = subprocess.run

    def run(cmd, **kwargs):
        if "clone" in cmd:
            seen.append((cmd, kwargs))
            root = Path(cmd[-1])
            make_repository(root)
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        return actual(cmd, **kwargs)

    monkeypatch.setattr(module.subprocess, "run", run)
    repo = prepare_repository("https://example.com/demo.git", tmp_path / "clone", ref="release/1")
    cmd, options = seen[0]
    assert "--no-recurse-submodules" in cmd and "--depth" in cmd and "--" in cmd
    assert options["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert options["capture_output"] and "shell" not in options
    assert repo.cloned
    with pytest.raises(ValueError):
        prepare_repository("https://example.com/demo.git", tmp_path / "next", ref="--evil")


def test_resource_hosts_are_not_navigation_authority():
    p = Policy(
        App(
            name="x",
            base_url="https://demo.example/path/",
            allowed_hosts=["demo.example"],
            resource_hosts=["cdn.example"],
        )
    )
    assert p.check_request("https://cdn.example/script.js", False)
    with pytest.raises(ValueError):
        p.check_request("https://cdn.example/", True)
    with pytest.raises(ValueError):
        p.check_url("https://cdn.example/")
    with pytest.raises(ValueError):
        p.check_request("https://demo.example.evil/a", False)


async def test_health_checks_never_prove_business_task_success():
    runtime = SimpleNamespace(evidence=SimpleNamespace(events=[]))

    async def inspect(assertion):
        return True

    runtime.inspect_assertion = inspect
    result = await verify(runtime, generic_assertions())
    assert result["task_outcome"] == "not_verified" and result["page_health"] == "passed"
    assertions = generic_assertions() + [
        Assertion(id="result", kind="text", selector="#result", contains="Done")
    ]
    result = await verify(runtime, assertions)
    assert result["task_outcome"] == "passed"


async def test_generic_discovery_creates_reusable_config_without_duckking_calls(tmp_path, monkeypatch):
    from ai_ux.llm import LLM
    from ai_ux.schema import load_config

    root = make_repository(tmp_path / "repo")
    captured = []

    async def structured(self, system, prompt, schema, **kwargs):
        captured.append(json.loads(prompt))
        return ProjectPlan(
            product_name="Counter",
            product_summary="Increment counter",
            tasks=[
                PlannedTask(
                    id="increment",
                    goal="Find the counter and increment it once",
                    expected_outcome="Visible counter shows 1",
                    dimension="task completion",
                    rationale="Main observed control",
                )
            ],
        ), {"provider": "test", "model": "fixture"}

    monkeypatch.setattr(LLM, "structured", structured)
    async with local_site() as url:
        project, config, _ = await discover(
            str(root),
            url,
            output=tmp_path / "out",
            task_count=1,
            models=providers(monkeypatch),
            browser=Browser(video=False),
        )
    assert config.app.adapter == "generic" and config.tasks[0].start_path == url
    assert config.app.name == "Counter" and config.tasks[0].expected_outcome == "Visible counter shows 1"
    assert all(a.scope == "page_health" for a in config.tasks[0].assertions)
    assert captured[0]["source_docs"][0]["path"] == "README.md"
    assert load_config(project / "project.yaml").app.source_repo == str(root)
    events = [json.loads(line) for line in (project / "discovery/events.jsonl").read_text().splitlines()]
    assert not any(e["kind"] in {"display_state", "room_allocated", "product_action"} for e in events)
    assert not (project / "checkout").exists()


async def test_source_mapping_validates_actual_quote_and_line(tmp_path):
    root = make_repository(tmp_path / "repo")
    repo = prepare_repository(str(root), tmp_path / "unused")
    evidence = Evidence(tmp_path / "run")
    evidence.observations = [{"nodes": [{"name": "Count", "dom_id": "label"}]}]

    class Model:
        async def structured(self, *args, **kwargs):
            return SourceMap(
                mappings=[
                    Mapping(
                        issue_index=0,
                        limitation="Deployment unknown",
                        locations=[
                            Location(
                                path="app.js",
                                line=2,
                                quote="const label = 'Count';",
                                relevance="Control label",
                                suggested_change="Clarify label",
                            ),
                            Location(
                                path="app.js",
                                line=3,
                                quote="does not exist",
                                relevance="x",
                                suggested_change="y",
                            ),
                            Location(
                                path="../outside.js",
                                line=1,
                                quote="escape",
                                relevance="x",
                                suggested_change="y",
                            ),
                        ],
                    )
                ]
            ), {"provider": "test"}

    judgement = {"issues": [{"title": "Count feedback unclear", "observations": ["Count"]}]}
    result = await locate_sources(repo, evidence, judgement, Model())
    assert len(result["mappings"][0]["locations"]) == 1 and len(result["rejected"]) == 2
    assert judgement["issues"][0]["source_locations"][0]["line"] == 2
    assert result["deployment_match"] == "unknown"


def test_demo_url_preserves_subpath_query_and_rejects_credentials():
    u = "https://example.com/my/app/?lang=zh#start"
    assert check_demo_url(u) == u
    with pytest.raises(ValueError):
        check_demo_url("https://user:pass@example.com/")


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/private",
        "http://10.0.0.1/private",
        "http://[::1]/private",
        "http://localhost/x",
        "http://host.internal/x",
        "https://u:p@cdn.example/a",
    ],
)
def test_public_resource_policy_blocks_private_literal_targets(url):
    p = Policy(
        App(
            name="Demo",
            base_url="https://demo.example",
            allowed_hosts=["demo.example"],
            resource_policy="public_readonly",
        )
    )
    with pytest.raises(ValueError):
        p.check_request(url, False)


def test_public_resources_allow_read_but_not_navigation_or_post():
    p = Policy(
        App(
            name="Demo",
            base_url="https://demo.example",
            allowed_hosts=["demo.example"],
            resource_policy="public_readonly",
        )
    )
    assert p.check_request("https://cdn.example/a.js", False, "GET")
    assert p.check_request("https://8.8.8.8/a.js", False, "HEAD")
    with pytest.raises(ValueError):
        p.check_request("https://cdn.example/", True, "GET")
    with pytest.raises(ValueError):
        p.check_request("https://api.example/send", False, "POST")
