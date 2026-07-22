"""Safe PowerShell argument construction for WSL-to-Windows calls.

Ownership: encode fixed, owner-authored PowerShell source without relying on
the Windows PowerShell 5.1 source-file encoding default.
Non-goals: shell execution, script policy, command allowlisting, or transport.
"""

from __future__ import annotations

import base64


def encoded_command_arguments(script: str) -> list[str]:
    """Return noninteractive PowerShell arguments using UTF-16LE transport."""
    if not isinstance(script, str) or not script:
        raise ValueError("PowerShell script must be a non-empty string")
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return ["-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]


def decode_encoded_command(encoded: str) -> str:
    """Decode an encoded command for tests and diagnostics only."""
    return base64.b64decode(encoded.encode("ascii"), validate=True).decode("utf-16le")
