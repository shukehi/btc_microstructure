"""L01: parsing an opt-in never grants REAL authority."""

from pathlib import Path

from pytest import CaptureFixture

from btc_microstructure.cli import main


def test_real_entry_always_closed(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    output = tmp_path / "absent.json"
    assert main(["collect-bounded", str(output), "--allow-network"]) == 2
    assert "BLOCKED" in capsys.readouterr().out
    assert not output.exists()
