from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {"tests"}

# Stage-2 signing lives only in these modules. Public market modules stay keyless.
PRIVATE_MODULES = {"auth.py", "private.py", "guards.py", "private_cli.py"}

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


def _public_py() -> list[Path]:
    return [path for path in _iter_py() if path.name not in PRIVATE_MODULES]


def test_no_signing_or_key_placeholders_in_public_library() -> None:
    """Public market modules stay keyless. Signing is confined to stage-2 files."""
    scanned = _public_py()
    assert scanned, "expected public modules to scan"
    assert any(path.name == "client.py" for path in scanned)
    for path in scanned:
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
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for arg in node.args.args + node.args.kwonlyargs:
                    assert arg.arg not in FORBIDDEN_NAMES, f"{arg.arg} in {path}::{node.name}"


def test_hmac_import_is_only_in_auth() -> None:
    for path in _iter_py():
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.split(".")[0] == "hmac":
                    assert path.name == "auth.py", path


def test_library_source_has_no_withdraw_routes() -> None:
    forbidden = (
        "/api/v5/asset/withdrawal",
        "/api/v5/asset/cancel-withdrawal",
        "/api/v5/asset/transfer",
        "/api/v5/asset/deposit-address",
        "/api/v5/asset/deposit-history",
        "/api/v5/finance/savings",
    )
    for path in _iter_py():
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{token} found in {path}"
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert "withdraw" not in node.name.lower(), f"{node.name} in {path}"


def test_package_does_not_vendor_agent_trade_kit() -> None:
    text = "\n".join(p.read_text(encoding="utf-8") for p in _iter_py())
    assert "agent-trade-kit" not in text
    assert "agent_trade_kit" not in text
