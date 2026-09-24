from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("v12_orchestrator", ROOT / "scripts" / "v12_orchestrator.py")
orch = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(orch)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class V12ForegroundWatchdogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.task = Path(self.temp.name)
        self.profile = self.task / "profile.json"
        self.manifest = self.task / "sources.json"
        self.registration = self.task / "heartbeat_registration.json"
        self.config_path = self.task / "pipeline.json"
        self.state_path = self.task / "state.json"
        self.worker = self.task / "chunk_worker.py"
        write_json(self.profile, {"schema": "semantic-style-policy-profile/v12", "profile": "celebrity_live_action_opening/v12", "policy": {"inventory_mode": "witness_sufficient"}})
        write_json(self.manifest, {"schema": "semantic-source-manifest/v12", "source_count": 1, "sources": [{"source_id": "s1", "source_path": "source.mp4"}]})
        write_json(self.registration, {"schema": "semantic-heartbeat-registration/v12", "task_id": "task-v12", "status": "active", "role": "watchdog_only", "automation_id": "watchdog-test"})
        self.worker.write_text(
            "import pathlib,sys\n"
            "counter=pathlib.Path(sys.argv[1]); progress=pathlib.Path(sys.argv[2]); output=pathlib.Path(sys.argv[3])\n"
            "n=int(counter.read_text() if counter.exists() else '0')+1; counter.write_text(str(n))\n"
            "progress.mkdir(parents=True,exist_ok=True); (progress/f'{n:03d}.json').write_text('{}')\n"
            "if n < 3: raise SystemExit(75)\n"
            "output.write_text('done'); raise SystemExit(0)\n",
            encoding="utf-8",
        )
        self.config = {
            "schema": orch.CONFIG_SCHEMA,
            "mode": "production",
            "task_id": "task-v12",
            "task_root": str(self.task),
            "default_max_retries": 1,
            "execution": {"foreground_required": True, "heartbeat_recovery_only": True, "heartbeat_stale_after_seconds": 180, "recovery_budget_seconds": 240},
            "job_contract": {"requested_outputs": 1, "style_profile_id": "celebrity_live_action_opening/v12", "inventory_mode": "witness_sufficient", "source_scope_mode": "explicit_witness_manifest", "source_scope_total": 1, "source_scope_justification": "test witness"},
            "heartbeat": {"required": True, "registration_path": str(self.registration)},
            "variables": {"source_manifest": str(self.manifest), "source_scope_total": "1", "requested_outputs": "1", "style_profile": str(self.profile)},
            "checkpoint_command": [sys.executable, "-c", "pass"],
            "slice_timeout_seconds": 10,
            "phases": [{
                "name": "CHUNK",
                "kind": "chunked_local",
                "command": [sys.executable, str(self.worker), str(self.task / "counter.txt"), str(self.task / "progress"), str(self.task / "result.txt")],
                "continue_exit_code": 75,
                "recover_progress_on_failure": True,
                "progress": {"directory": str(self.task / "progress"), "glob": "*.json", "total": "${source_scope_total}"},
                "outputs": [{"name": "result", "path": str(self.task / "result.txt")}],
            }],
        }
        write_json(self.config_path, self.config)

    def tearDown(self):
        self.temp.cleanup()

    def init(self):
        return orch.init_orchestrator(self.config_path, self.state_path)

    def test_unbounded_foreground_runs_all_chunks_without_scheduler_gap(self):
        self.init()
        code, state = orch.run_continuous(self.config_path, self.state_path, 0, recovery=False)
        self.assertEqual(0, code)
        self.assertEqual("COMPLETE", state["status"])
        self.assertEqual("3", (self.task / "counter.txt").read_text())
        lease = orch.load_json(orch.foreground_lease_path(self.config))
        self.assertEqual("complete", lease["status"])

    def test_bounded_normal_foreground_is_rejected(self):
        self.init()
        with self.assertRaisesRegex(ValueError, "V12_BOUNDED_FOREGROUND_FORBIDDEN"):
            orch.run_continuous(self.config_path, self.state_path, 55, recovery=False)

    def test_watchdog_does_nothing_for_live_foreground_lease(self):
        state = self.init()
        orch.write_foreground_lease(self.config, state, "foreground", "running")
        code, result = orch.watchdog(self.config_path, self.state_path)
        self.assertEqual(0, code)
        self.assertEqual("foreground_active", result["action"])

    @unittest.skipUnless(os.name == "nt", "Windows-specific liveness probe")
    def test_windows_liveness_probe_never_uses_os_kill(self):
        with mock.patch.object(orch.os, "kill", side_effect=AssertionError("os.kill must not probe Windows PIDs")):
            self.assertTrue(orch.pid_alive(os.getpid()))

    def test_watchdog_requests_recovery_only_after_stale_state(self):
        self.init()
        state = orch.load_json(self.state_path)
        state["updated_at"] = "2000-01-01T00:00:00+00:00"
        orch.atomic_json(self.state_path, state)
        code, result = orch.watchdog(self.config_path, self.state_path)
        self.assertEqual(22, code)
        self.assertEqual("recovery_required", result["action"])

    def test_profile_mismatch_is_rejected_at_init(self):
        self.config["job_contract"]["style_profile_id"] = "historical_low_repeat_rolling/v10"
        write_json(self.config_path, self.config)
        with self.assertRaisesRegex(ValueError, "V12_STYLE_PROFILE_BINDING_MISMATCH"):
            self.init()

    def test_source_scope_count_mismatch_is_rejected_at_init(self):
        self.config["job_contract"]["source_scope_total"] = 2
        self.config["variables"]["source_scope_total"] = "2"
        write_json(self.config_path, self.config)
        with self.assertRaisesRegex(ValueError, "V12_SOURCE_SCOPE_COUNT_MISMATCH"):
            self.init()

    def test_progress_total_token_is_expanded(self):
        self.init()
        phase = self.config["phases"][0]
        snapshot = orch.progress_snapshot(phase, self.config, self.state_path)
        self.assertEqual(1, snapshot["total"])


if __name__ == "__main__":
    unittest.main()
