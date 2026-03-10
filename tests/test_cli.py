from typer.testing import CliRunner

from maestro.cli import app
from maestro.linear.models import Issue


runner = CliRunner()


def test_workspace_show_command(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "maestro.yaml"
    config_path.write_text(
        """
linear:
  api_key: test
workspace:
  root: ./workspaces
acp:
  command: agent acp
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["workspace", "show", "ABC-123", "--config", str(config_path)])

    assert result.exit_code == 0
    assert str((tmp_path / "workspaces" / "ABC-123").resolve()) in result.stdout


def test_list_command_renders_issues(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "maestro.yaml"
    config_path.write_text(
        """
linear:
  api_key: test
workspace:
  root: ./workspaces
acp:
  command: agent acp
""".strip(),
        encoding="utf-8",
    )

    class FakeLinearClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def fetch_issues(self, **_kwargs):
            return [
                Issue(
                    id="issue-1",
                    identifier="ABC-123",
                    title="First issue",
                    description=None,
                    state="In Progress",
                )
            ]

    monkeypatch.setattr("maestro.cli.LinearClient", FakeLinearClient)

    result = runner.invoke(app, ["list", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "ABC-123" in result.stdout
    assert "First issue" in result.stdout
