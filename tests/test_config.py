from pathlib import Path

import pytest

from maestro.config import ConfigError, load_config


def test_load_config_expands_environment_variables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINEAR_API_KEY", "linear-secret")
    config_path = tmp_path / "maestro.yaml"
    config_path.write_text(
        """
linear:
  api_key: $LINEAR_API_KEY
workspace:
  root: ./workspaces
acp:
  command: agent acp
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.linear.api_key == "linear-secret"
    assert config.workspace.root == (tmp_path / "workspaces").resolve()


def test_load_config_requires_expected_sections(tmp_path: Path) -> None:
    config_path = tmp_path / "maestro.yaml"
    config_path.write_text("{}", encoding="utf-8")

    with pytest.raises(ConfigError):
        load_config(config_path)
