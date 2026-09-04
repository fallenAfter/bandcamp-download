from pathlib import Path

from bcdl.config import DEFAULT_FORMAT, FORMAT_PREFERENCE, config_dir


def test_flac_is_preferred() -> None:
    assert DEFAULT_FORMAT == "flac"
    assert FORMAT_PREFERENCE[0] == "flac"


def test_config_dir_respects_bcdl_home(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BCDL_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert config_dir() == (tmp_path / "state").resolve()
