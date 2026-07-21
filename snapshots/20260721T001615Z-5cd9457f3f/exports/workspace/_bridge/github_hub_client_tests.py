from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

import github_hub_client as client


class GithubKeyringBridgeTests(unittest.TestCase):
    def test_keyring_token_is_read_in_process_only(self) -> None:
        completed = subprocess.CompletedProcess(
            ["gh", "auth", "token"],
            0,
            stdout="ghp_test_token\n",
            stderr="",
        )
        with patch.object(client.shutil, "which", side_effect=lambda name: "/usr/bin/gh" if name == "gh" else None), patch.object(
            client.subprocess, "run", return_value=completed
        ) as run:
            token = client._gh_keyring_token()
        self.assertEqual(token, "ghp_test_token")
        self.assertEqual(run.call_args.args[0], ["/usr/bin/gh", "auth", "token"])

    def test_auth_candidates_use_keyring_after_vault(self) -> None:
        with patch.object(client, "github_app_create_installation_token", side_effect=RuntimeError("not configured")), patch.object(
            client, "secret_vault_get_secret", side_effect=RuntimeError("backend unavailable")
        ), patch.object(client, "_gh_keyring_token", return_value="ghp_test_token"):
            candidates = client.github_auth_candidates()
        self.assertEqual(candidates, [("ghp_test_token", "gh_keyring")])

    def test_missing_keyring_token_remains_bounded(self) -> None:
        with patch.object(client.shutil, "which", return_value="/usr/bin/gh"), patch.object(
            client.subprocess, "run", side_effect=FileNotFoundError("gh")
        ):
            self.assertEqual(client._gh_keyring_token(), "")


if __name__ == "__main__":
    unittest.main()
