import tomllib
from pathlib import Path

from lyra.contracts.config import LyraConfig
from tests.config_serialization import render_config_toml


def test_example_config_matches_config_contract() -> None:
    example_path = Path(__file__).resolve().parents[1] / "config.example.toml"
    raw_config = tomllib.loads(example_path.read_text(encoding="utf-8"))

    config = LyraConfig.model_validate(raw_config)

    assert config.jobs.queue == "lyra"
    assert "admin" not in raw_config
    assert "agent" not in raw_config
    assert config.api.forwarded_allow_ips == ["127.0.0.1"]
    assert config.logging.file is None
    assert config.worker.concurrency == 6

    rendered = render_config_toml(config)

    assert "[admin]" not in rendered
    assert "[agent]" not in rendered
    assert "password_file" not in rendered
    assert "service_account_file" not in rendered
    assert "api_key_file" not in rendered
    assert "catalog_dir" not in rendered
    assert "runner_base_dir" not in rendered
    assert 'forwarded_allow_ips = [\n  "127.0.0.1",\n]' in rendered
