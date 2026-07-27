#!/usr/bin/env python3
"""Regressions for the workflow-evolution information entry."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import system_state_index as index


class SystemStateIndexObservationTests(unittest.TestCase):
    def test_observation_iteration_is_exposed_through_existing_state_index(self) -> None:
        signal = next(
            item
            for item in index.SIGNAL_COMMANDS
            if item.source == "workflow_observation_iteration.plan"
        )
        self.assertEqual("workflow_evolution", signal.area)
        self.assertEqual("workflow_observation_iteration", signal.owner)
        self.assertEqual((sys.executable, "_bridge/workflow_observation_iteration.py", "plan"), signal.command)

    def test_all_signal_commands_use_the_current_available_python(self) -> None:
        self.assertTrue(Path(index.PYTHON_EXECUTABLE).is_file())
        self.assertTrue(all(item.command[0] == index.PYTHON_EXECUTABLE for item in index.SIGNAL_COMMANDS))


if __name__ == "__main__":
    unittest.main()
