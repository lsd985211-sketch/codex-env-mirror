#!/usr/bin/env python3
"""Update the local Reasonix DeepSeek API key with backup and atomic replace."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import sys
import tempfile

BRIDGE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from shared.backup_router import create_backup as create_routed_backup


DEFAULT_REASONIX_DIR = Path(r"C:\Users\45543\AppData\Roaming\reasonix")
DEFAULT_CREDENTIALS_FILE = DEFAULT_REASONIX_DIR / "credentials"
KEY_NAME = "DEEPSEEK_API_KEY"
BACKUP_SUFFIX = "reasonix-key-tool"


class ReasonixKeyStoreError(Exception):
    """Raised when the credentials file cannot be safely updated."""


@dataclass(frozen=True)
class UpdateResult:
    """Structured response for the GUI layer."""

    credentials_path: Path
    backup_path: Path
    updated_existing_line: bool


@dataclass(frozen=True)
class CredentialsCheckResult:
    """Read-only health summary for the target credentials file."""

    credentials_path: Path
    exists: bool
    contains_target_key: bool
    parent_exists: bool
    writable: bool


def validate_key(raw_value: str) -> str:
    """Validate and normalize the submitted key."""
    value = raw_value.strip()
    if not value:
        raise ReasonixKeyStoreError("密钥不能为空。")
    if not value.startswith("sk-"):
        raise ReasonixKeyStoreError("密钥格式不正确，当前工具只接受以 sk- 开头的值。")
    return value


def get_credentials_file() -> Path:
    """Return the credentials file, allowing test overrides via environment."""
    override = os.environ.get("REASONIX_KEY_TOOL_CREDENTIALS")
    if override:
        return Path(override).expanduser().resolve()
    return DEFAULT_CREDENTIALS_FILE


def check_credentials_target() -> CredentialsCheckResult:
    """Inspect the target credentials file without mutating it."""
    credentials_file = get_credentials_file()
    parent_exists = credentials_file.parent.exists()
    exists = credentials_file.exists()
    contains_target_key = False
    writable = False

    if exists:
        try:
            text = credentials_file.read_text(encoding="utf-8")
            contains_target_key = any(line.startswith(f"{KEY_NAME}=") for line in text.splitlines())
        except OSError:
            contains_target_key = False
        writable = os.access(credentials_file, os.W_OK)
    elif parent_exists:
        writable = os.access(credentials_file.parent, os.W_OK)

    return CredentialsCheckResult(
        credentials_path=credentials_file,
        exists=exists,
        contains_target_key=contains_target_key,
        parent_exists=parent_exists,
        writable=writable,
    )


def update_deepseek_api_key(raw_value: str) -> UpdateResult:
    """Update the DeepSeek key in the Reasonix credentials file."""
    value = validate_key(raw_value)
    credentials_file = get_credentials_file()
    reasonix_dir = credentials_file.parent

    if not reasonix_dir.exists():
        raise ReasonixKeyStoreError(f"Reasonix 配置目录不存在：{reasonix_dir}")
    if not credentials_file.exists():
        raise ReasonixKeyStoreError(f"Reasonix 凭据文件不存在：{credentials_file}")

    try:
        original_text = credentials_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReasonixKeyStoreError("无法读取 Reasonix 凭据文件。") from exc

    backup_path = create_backup(credentials_file)
    new_text, updated_existing_line = build_updated_credentials_text(original_text, value)
    atomic_replace_text(credentials_file, new_text)

    return UpdateResult(
        credentials_path=credentials_file,
        backup_path=backup_path,
        updated_existing_line=updated_existing_line,
    )


def create_backup(target: Path) -> Path:
    """Create a routed backup with manifest metadata before updating the target file."""
    result = create_routed_backup(
        [str(target)],
        remark=BACKUP_SUFFIX,
        purpose="backup Reasonix credentials before API key update",
        category="reasonix-key-tool",
        trigger="reasonix_key_store.create_backup",
    )
    if not result.get("ok"):
        raise ReasonixKeyStoreError("创建凭据备份失败，已停止更新。")
    items = result.get("items") if isinstance(result.get("items"), list) else []
    backup_path = Path(str(items[0].get("backup_path") or "")) if items else Path()
    if not backup_path.exists():
        raise ReasonixKeyStoreError("创建凭据备份失败，已停止更新。")
    return backup_path


def build_updated_credentials_text(original_text: str, key_value: str) -> tuple[str, bool]:
    """Replace the existing key line or append one if missing."""
    replacement = f"{KEY_NAME}={key_value}"
    lines = original_text.splitlines()
    updated = False
    output_lines: list[str] = []

    for line in lines:
        if line.startswith(f"{KEY_NAME}="):
            output_lines.append(replacement)
            updated = True
        else:
            output_lines.append(line)

    if not updated:
        output_lines.append(replacement)

    new_text = "\n".join(output_lines)
    if original_text.endswith("\n") or not original_text:
        new_text += "\n"
    return new_text, updated


def atomic_replace_text(target: Path, content: str) -> None:
    """Write to a temporary file in the same directory and atomically replace."""
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=str(target.parent),
            prefix=f"{target.name}.tmp-",
            suffix=".txt",
        ) as handle:
            handle.write(content)
            temp_path = Path(handle.name)
        os.replace(temp_path, target)
    except OSError as exc:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise ReasonixKeyStoreError("写入凭据文件失败，原文件未被替换。") from exc
