import json

import pytest
import yaml
from test_llm import providers

from ai_ux import cli
from ai_ux.discovery import generic_assertions
from ai_ux.schema import App, Config, Task


def cli_config(tmp_path, monkeypatch):
    c = Config(
        app=App(name="Test", base_url="http://localhost", allowed_hosts=["localhost"], environment="local"),
        models=providers(monkeypatch),
        tasks=[Task(id="test", goal="Use the app", assertions=generic_assertions())],
    )
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(c.model_dump()))
    return p, c


def test_cli_config_and_doctor_do_not_show_keys(tmp_path, monkeypatch, capsys):
    p, _ = cli_config(tmp_path, monkeypatch)
    for command in ["validate-config", "doctor"]:
        monkeypatch.setattr("sys.argv", ["uxpilot", "--config", str(p), command])
        assert cli.main() == 0
        output = capsys.readouterr().out
        assert "primary-secret" not in output
        assert json.loads(output)


@pytest.mark.parametrize(
    "command,result,expected",
    [
        ("eval", {"runs": [{"stop_reason": "explorer_done", "judge_status": "completed"}]}, 0),
        ("eval", {"runs": [{"stop_reason": "interrupted", "judge_status": "not_run"}]}, 130),
        ("eval", {"runs": [{"stop_reason": "runtime_error", "judge_status": "failed"}]}, 2),
        ("judge", {"judge_status": "completed"}, 0),
        ("judge", {"judge_status": "failed"}, 2),
        ("probe-video", {"providers": [{"passed": True}]}, 0),
        ("probe-video", {"providers": [{"passed": False}]}, 2),
    ],
)
def test_cli_commands_return_truthful_exit_codes(tmp_path, monkeypatch, command, result, expected):
    p, _ = cli_config(tmp_path, monkeypatch)

    async def fake(*args, **kwargs):
        return (tmp_path, result) if command == "eval" else result

    monkeypatch.setattr(cli, {"eval": "evaluate", "judge": "rejudge", "probe-video": "probe"}[command], fake)
    argv = ["uxpilot", "--config", str(p), command]
    if command == "judge":
        argv.append(str(tmp_path / "run"))
    monkeypatch.setattr("sys.argv", argv)
    assert cli.main() == expected


def test_generic_review_does_not_read_duckking_default(tmp_path, monkeypatch):
    _, c = cli_config(tmp_path, monkeypatch)
    seen = []

    async def fake(repo, url, **kw):
        seen.append((repo, url, kw))
        return tmp_path, c, None

    monkeypatch.setattr(cli, "discover", fake)

    def no_config(*args):
        raise AssertionError("default profile must not be read")

    monkeypatch.setattr(cli, "load_config", no_config)
    monkeypatch.setattr(
        "sys.argv",
        ["uxpilot", "review", "--repo", "/path/project", "--url", "https://example.com/app", "--plan-only"],
    )
    assert cli.main() == 0 and seen[0][1] == "https://example.com/app"


def test_generic_review_eval_and_invalid_timeout(tmp_path, monkeypatch):
    _, c = cli_config(tmp_path, monkeypatch)

    async def discover(*args, **kwargs):
        return tmp_path, c, None

    async def evaluate(*args, **kwargs):
        return tmp_path, {"runs": []}

    monkeypatch.setattr(cli, "discover", discover)
    monkeypatch.setattr(cli, "evaluate", evaluate)
    argv = ["uxpilot", "review", "--repo", "/repo", "--url", "https://example.com"]
    monkeypatch.setattr("sys.argv", argv)
    assert cli.main() == 0
    monkeypatch.setattr("sys.argv", argv + ["--timeout", "1"])
    assert cli.main() == 2


def test_missing_config_and_interrupt(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["uxpilot", "--config", str(tmp_path / "missing"), "doctor"])
    assert cli.main() == 2 and "FileNotFoundError" in capsys.readouterr().err
    monkeypatch.setattr(cli, "load_config", lambda *args: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert cli.main() == 130
