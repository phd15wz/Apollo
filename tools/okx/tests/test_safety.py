from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {"tests"}

FORBIDDEN_SUBSTRINGS = (
    "OK-ACCESS-KEY",
    "OK-ACCESS-SIGN",
    "OK-ACCESS-PASSPHRASE",
    "OK-ACCESS-TIMESTAMP",
    "api_secret",
    "secret_key",
    "passphrase",
)

FORBIDDEN_IMPORTS = {"hmac"}
FORBIDDEN_NAMES = {"api_key", "apiKey", "secretKey", "passphrase"}


def _iter_py() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*.py"):
        if any(part in SKIP_PARTS for part in path.parts):
            continue
        files.append(path)
    return files


def test_no_signing_or_key_placeholders_in_library() -> None:
    for path in _iter_py():
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_SUBSTRINGS:
            assert token not in text, f"{token} found in {path}"
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in FORBIDDEN_IMPORTS, path
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in FORBIDDEN_IMPORTS, path
            if isinstance(node, ast.FunctionDef):
                for arg in node.args.args + node.args.kwonlyargs:
                    assert arg.arg not in FORBIDDEN_NAMES, f"{arg.arg} in {path}::{node.name}"


def test_package_does_not_vendor_agent_trade_kit() -> None:
    text = "\n".join(p.read_text(encoding="utf-8") for p in _iter_py())
    assert "agent-trade-kit" not in text
    assert "agent_trade_kit" not in text
