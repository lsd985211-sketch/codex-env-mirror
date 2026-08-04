#!/usr/bin/env python3

import unittest

from workflow_validation_plan_session import ValidationPlanSession


class ValidationPlanSessionTests(unittest.TestCase):
    def _session(self, calls: list[tuple[str, str]], *, skill="skill-v1", source="source-v1") -> ValidationPlanSession:
        def build(message: str, *, detail: str, skill_routing_context: object) -> dict:
            calls.append((message, detail))
            return {"message": message, "detail": detail, "nested": {"context": skill_routing_context}}

        return ValidationPlanSession(
            build,
            skill_routing_context={"stable": True},
            skill_context_signature=skill,
            workflow_source_signature=source,
        )

    def test_reuses_only_exact_canonical_input_and_returns_defensive_copies(self) -> None:
        calls: list[tuple[str, str]] = []
        session = self._session(calls)
        first = session.plan_for("检查 当前 工作流", detail="full")
        first["nested"]["context"]["stable"] = False
        second = session.plan_for("检查 当前 工作流", detail="full")

        self.assertEqual([("检查 当前 工作流", "full")], calls)
        self.assertTrue(second["nested"]["context"]["stable"])
        self.assertIsNot(first, second)
        self.assertEqual(
            {
                "plan_request_count": 2,
                "executed_plan_build_count": 1,
                "intra_validation_reuse_count": 1,
                "fail_closed_build_count": 0,
                "cache_entry_count": 1,
            },
            {key: session.report()[key] for key in (
                "plan_request_count", "executed_plan_build_count", "intra_validation_reuse_count",
                "fail_closed_build_count", "cache_entry_count",
            )},
        )

    def test_detail_or_signature_change_uses_an_independent_session_entry(self) -> None:
        calls: list[tuple[str, str]] = []
        session = self._session(calls)
        session.plan_for("检查工具", detail="full")
        session.plan_for("检查工具", detail="micro")
        self.assertEqual(2, session.report()["executed_plan_build_count"])

        other_calls: list[tuple[str, str]] = []
        other = self._session(other_calls, source="source-v2")
        other.plan_for("检查工具", detail="full")
        self.assertEqual(1, other.report()["executed_plan_build_count"])
        self.assertEqual([], other_calls[1:])

    def test_missing_or_unknown_identity_fails_closed_without_reuse(self) -> None:
        calls: list[tuple[str, str]] = []
        session = self._session(calls, skill="")
        session.plan_for("检查工具", detail="full")
        session.plan_for("检查工具", detail="full")
        self.assertEqual(2, len(calls))
        self.assertEqual(2, session.report()["fail_closed_build_count"])

        calls.clear()
        unknown = self._session(calls)
        unknown.plan_for("检查工具", detail="unknown-detail")
        unknown.plan_for("检查工具", detail="unknown-detail")
        unknown.plan_for("检查工具", detail="FULL")
        unknown.plan_for("检查工具", detail="FULL")
        self.assertEqual(4, len(calls))
        self.assertEqual(0, unknown.report()["intra_validation_reuse_count"])

        aliases: list[tuple[str, str]] = []
        alias_session = self._session(aliases)
        alias_session.plan_for("  检查   工具  ", detail="full")
        alias_session.plan_for("检查 工具", detail="full")
        self.assertEqual(2, len(aliases))
        self.assertEqual(0, alias_session.report()["intra_validation_reuse_count"])

    def test_corrupt_cached_entry_rebuilds_instead_of_returning_it(self) -> None:
        calls: list[tuple[str, str]] = []
        session = self._session(calls)
        session.plan_for("检查工具", detail="full")
        identity = next(iter(session._entries))
        session._entries[identity] = {"canonical_input": {"wrong": "input"}, "plan": {}}
        rebuilt = session.plan_for("检查工具", detail="full")
        self.assertEqual(2, len(calls))
        self.assertEqual("检查工具", rebuilt["message"])

    def test_cli_projection_preserves_bounded_session_metrics(self) -> None:
        from workflow_orchestrator import cli_projection

        session = {
            "schema": "workflow_validation_plan_session.v1",
            "plan_request_count": 78,
            "executed_plan_build_count": 65,
            "intra_validation_reuse_count": 13,
        }
        projected = cli_projection(
            {"ok": True, "checks": [], "validation_plan_session": session},
            "validate",
        )
        self.assertEqual(session, projected["validation_plan_session"])


if __name__ == "__main__":
    unittest.main()
