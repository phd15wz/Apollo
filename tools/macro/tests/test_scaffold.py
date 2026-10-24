"""The FRED key stays out of error text, and the CLI refuses a repo cache."""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tools.macro.fred import MissingFredApiKey, fred_api_key, fred_query_params
from tools.macro.refresh import main, refresh_once

NY = ZoneInfo("America/New_York")
REPO = Path(__file__).resolve().parents[3]
PACKAGE = Path(__file__).resolve().parents[1]
FAKE_KEY = "test-fred-key"
KEY_LITERAL = re.compile(r"""["']api_key["']\s*:\s*["'][^"']+["']""")


def test_missing_key_raises_without_a_placeholder_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    with pytest.raises(MissingFredApiKey, match="secret store") as exc:
        fred_api_key()
    assert FAKE_KEY not in str(exc.value)
    with pytest.raises(MissingFredApiKey):
        fred_api_key(environ={})
    with pytest.raises(MissingFredApiKey):
        fred_query_params("DGS10", "   ")


def test_query_params_name_the_locked_series_and_not_dxy() -> None:
    params = fred_query_params(
        "DGS10",
        FAKE_KEY,
        observation_start=date(2024, 1, 2),
        observation_end=date(2024, 1, 31),
    )
    assert params["series_id"] == "DGS10"
    assert params["file_type"] == "json"
    assert params["api_key"] == FAKE_KEY
    assert params["observation_start"] == "2024-01-02"
    assert params["observation_end"] == "2024-01-31"
    with pytest.raises(ValueError, match="DX-Y.NYB"):
        fred_query_params("DX-Y.NYB", FAKE_KEY)
    with pytest.raises(ValueError, match="not a locked FRED series"):
        fred_query_params("UNRATE", FAKE_KEY)


def test_refresh_once_rejects_a_naive_clock(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        refresh_once(now=datetime(2025, 11, 26, 17, 0), cache_dir=tmp_path)


def test_cli_missing_key(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    assert main([]) == 2
    captured = capsys.readouterr()
    assert "FRED_API_KEY" in captured.err
    assert captured.out == ""


def test_cli_rejects_a_repo_cache_and_does_not_print_the_key(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FRED_API_KEY", FAKE_KEY)
    assert main(["--cache-dir", str(REPO / "data" / "macro")]) == 2
    captured = capsys.readouterr()
    assert FAKE_KEY not in captured.err
    assert FAKE_KEY not in captured.out

def test_module_entrypoint_does_not_echo_the_key() -> None:
    env = os.environ.copy()
    env["FRED_API_KEY"] = FAKE_KEY
    result = subprocess.run(
        [sys.executable, "-m", "tools.macro.refresh", "--cache-dir", str(REPO / "data" / "macro")],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert FAKE_KEY not in result.stdout
    assert FAKE_KEY not in result.stderr
    assert "outside the repo" in result.stderr


def test_production_modules_do_not_hardcode_an_api_key() -> None:
    for path in PACKAGE.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert FAKE_KEY not in text
        assert KEY_LITERAL.search(text) is None
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "FRED_API_KEY":
                        raise AssertionError(f"hardcoded FRED_API_KEY assignment in {path}")
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] != "yfinance", path
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] != "yfinance", path
