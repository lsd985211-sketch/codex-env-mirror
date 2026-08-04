#!/usr/bin/env python3
"""Focused tests for Python package resource execution modes."""

from __future__ import annotations

import unittest
from unittest import mock

import resource_package_owner as owner


GATEWAY = {
    "ok": True,
    "plan": {
        "route_mode": "probe_selected_direct",
        "target_kind": "package",
        "env": {},
        "unset_env": [],
    },
}


def result(**payload):
    payload.setdefault("writes_files", False)
    payload.setdefault("writes_remote_state", False)
    return payload


def metadata_result(version: str = "4.15.0") -> dict:
    return result(
        ok=True,
        status="completed",
        source="package_manager",
        result_kind="package_index_metadata",
        content=f"beautifulsoup4 ({version})",
        metadata={"latest": version, "command_kind": "pypi_json_metadata"},
    )


class ResourcePackageOwnerTests(unittest.TestCase):
    def request(self, mode: str, *, approved: bool = False, allow_write: bool = False) -> dict:
        return {
            "target": "beautifulsoup4",
            "allow_filesystem_write": allow_write,
            "metadata": {
                "package_ecosystem": "python",
                "package_action": "install" if mode == "verified_auto_install" else "plan",
                "package_execution_mode": mode,
                "package_spec": "beautifulsoup4==4.15.0",
                "install_approved": approved,
                "validation_profile": "full",
            },
        }

    def test_inspect_returns_plan_without_installing(self) -> None:
        with mock.patch.object(owner, "_lookup_python_metadata", return_value=metadata_result()), mock.patch.object(
            owner, "execute_python_package_install"
        ) as install:
            actual = owner.execute_package_metadata(
                self.request("inspect_then_install"), GATEWAY, 30, result
            )
        self.assertTrue(actual["ok"])
        self.assertEqual(actual["result_kind"], "python_package_install_plan")
        plan = actual["metadata"]["package_install_plan"]
        self.assertEqual(plan["locked_spec"], "beautifulsoup4==4.15.0")
        self.assertEqual(
            plan["missing_permissions"],
            [
                "install_approved",
                "allow_filesystem_write",
                "authorization.grant_ref",
                "authorization.operation_id",
                "authorization.thread_id",
            ],
        )
        self.assertEqual(len(plan["requirements"]), 3)
        self.assertTrue(plan["lock_signature"])
        install.assert_not_called()

    def test_auto_mode_requires_both_permissions_before_metadata_or_install(self) -> None:
        with mock.patch.object(owner, "_lookup_python_metadata") as lookup, mock.patch.object(
            owner, "execute_python_package_install"
        ) as install:
            actual = owner.execute_package_metadata(
                self.request("verified_auto_install", approved=True, allow_write=False),
                GATEWAY,
                30,
                result,
            )
        self.assertEqual(actual["error_class"], "verified_auto_install_requires_approved_write")
        self.assertEqual(
            actual["metadata"]["package_install_plan"]["missing_permissions"],
            [
                "allow_filesystem_write",
                "authorization.grant_ref",
                "authorization.operation_id",
                "authorization.thread_id",
            ],
        )
        lookup.assert_not_called()
        install.assert_not_called()

    def test_auto_mode_fails_closed_on_metadata_lock_drift(self) -> None:
        request = self.request("verified_auto_install", approved=True, allow_write=True)
        request["metadata"]["authorization"] = {
            "grant_ref": "scoped-authorization:grant:test",
            "operation_id": "install-test",
            "thread_id": "turn-test",
        }
        with mock.patch.object(owner, "_lookup_python_metadata", return_value=metadata_result("4.15.1")), mock.patch.object(
            owner, "execute_python_package_install"
        ) as install:
            actual = owner.execute_package_metadata(
                request,
                GATEWAY,
                30,
                result,
            )
        self.assertEqual(actual["error_class"], "package_metadata_lock_mismatch")
        install.assert_not_called()

    def test_auto_mode_runs_existing_installer_after_checks(self) -> None:
        request = self.request("verified_auto_install", approved=True, allow_write=True)
        request["metadata"]["authorization"] = {
            "grant_ref": "scoped-authorization:grant:test",
            "operation_id": "install-test",
            "thread_id": "turn-test",
        }
        installed = result(
            ok=True,
            status="completed",
            source="package_manager",
            result_kind="python_package_install",
            metadata={"installed_version": "4.15.0", "import_probe": {"ok": True}},
        )
        with mock.patch.object(owner, "_lookup_python_metadata", return_value=metadata_result()), mock.patch.object(
            owner, "execute_python_package_install", return_value=installed
        ) as install:
            actual = owner.execute_package_metadata(
                request,
                GATEWAY,
                30,
                result,
            )
        self.assertTrue(actual["ok"])
        self.assertEqual(actual["metadata"]["package_execution_mode"], "verified_auto_install")
        self.assertTrue(actual["metadata"]["metadata_check"]["ok"])
        install.assert_called_once()


if __name__ == "__main__":
    unittest.main()
