#!/usr/bin/env python3

from __future__ import annotations

import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

BRIDGE = Path(__file__).resolve().parents[1]
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

try:
    from shared.process_liveness import find_unsafe_zero_signal_probes, normalize_pid, process_creation_identity, process_is_alive
except ModuleNotFoundError:
    from _bridge.shared.process_liveness import find_unsafe_zero_signal_probes, normalize_pid, process_creation_identity, process_is_alive


ROOT = Path(__file__).resolve().parents[2]


class ProcessLivenessTests(unittest.TestCase):
    def test_pid_normalization_is_strict(self) -> None:
        self.assertEqual(normalize_pid(" 123 "), 123)
        for value in (True, False, 0, -1, 1.5, "1.0", "-2", ""):
            self.assertIsNone(normalize_pid(value))

    def test_current_and_missing_processes(self) -> None:
        self.assertTrue(process_is_alive(os.getpid()))
        self.assertTrue(process_creation_identity(os.getpid()))
        self.assertFalse(process_is_alive(0))
        self.assertIsNone(process_creation_identity(0))
        self.assertFalse(process_is_alive(2**31 - 1))
        self.assertIsNone(process_creation_identity(2**31 - 1))

    @unittest.skipUnless(os.name == "nt", "Windows regression")
    def test_repeated_probe_does_not_interrupt_target(self) -> None:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        try:
            child_identity = process_creation_identity(child.pid)
            self.assertTrue(child_identity)
            self.assertNotEqual(child_identity, process_creation_identity(os.getpid()))
            for _ in range(50):
                self.assertTrue(process_is_alive(child.pid))
                self.assertEqual(process_creation_identity(child.pid), child_identity)
            time.sleep(0.1)
            self.assertIsNone(child.poll())
        finally:
            child.terminate()
            child.wait(timeout=5)

    def test_first_party_sources_do_not_reintroduce_zero_signal_probe(self) -> None:
        self.assertEqual(find_unsafe_zero_signal_probes(ROOT / "_bridge"), [])


if __name__ == "__main__":
    unittest.main()
