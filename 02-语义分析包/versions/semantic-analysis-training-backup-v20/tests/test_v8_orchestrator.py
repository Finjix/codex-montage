from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


orch = load_module("v11_orchestrator", ROOT / "scripts" / "v11_orchestrator.py")
source_asr = load_module("v9_source_asr", ROOT / "scripts" / "v9_source_asr.py")


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class V8OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.task = Path(self.temp.name)
        self.config_path = self.task / "pipeline.json"
        self.state_path = self.task / "state.json"

    def tearDown(self):
        self.temp.cleanup()

    def local_writer(self, name: str, content: str = "ok") -> list[str]:
        return ["${python}", "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(sys.argv[2],encoding='utf-8')", "${task_root}/" + name, content]

    def make_config(self, phases: list[dict]) -> dict:
        config = {"schema": orch.CONFIG_SCHEMA, "mode": "test", "task_id": "task-v8", "task_root": str(self.task), "default_max_retries": 1, "phases": phases}
        write_json(self.config_path, config)
        return config

    def poll(self, limit: int = 100):
        for _ in range(limit):
            code, state = orch.resume(self.config_path, self.state_path)
            if code not in (21, 22):
                return code, state
            time.sleep(0.03)
        self.fail("orchestrator did not settle")

    def test_local_model_local_pipeline_completes(self):
        phases = [
            {"name": "SOURCE_ASR", "kind": "local", "command": self.local_writer("asr.json"), "outputs": [{"name": "source_asr_index", "path": "${task_root}/asr.json"}]},
            {"name": "SEMANTIC_REVIEW", "kind": "model", "model_role": "gpt-5.6-terra", "instructions": "review", "response_path": "${task_root}/model_response.json", "outputs": [{"name": "semantic_review", "path": "${task_root}/semantic.json"}]},
            {"name": "PRELOCK_GATE", "kind": "local", "command": self.local_writer("gate.json"), "outputs": [{"name": "gate_report", "path": "${task_root}/gate.json"}]},
        ]
        self.make_config(phases)
        orch.init_orchestrator(self.config_path, self.state_path)
        code, state = self.poll()
        self.assertEqual(20, code)
        self.assertEqual("WAITING_MODEL", state["status"])
        semantic = self.task / "semantic.json"
        semantic.write_text("reviewed", encoding="utf-8")
        response = {"schema": orch.MODEL_RESPONSE_SCHEMA, "task_id": "task-v8", "phase": "SEMANTIC_REVIEW", "decision": "pass", "outputs": [{"name": "semantic_review", "path": str(semantic), "sha256": orch.sha_file(semantic)}]}
        response_path = self.task / "model_response.json"
        write_json(response_path, response)
        code, _ = orch.supply_model(self.config_path, self.state_path, response_path)
        if code in (21, 22):
            code, state = self.poll()
        else:
            state = orch.load_json(self.state_path)
        self.assertEqual(0, code)
        self.assertEqual("COMPLETE", state["status"])
        self.assertEqual(3, len(state["history"]))

    def test_failed_local_phase_uses_one_fallback(self):
        phase = {"name": "SOURCE_ASR", "kind": "local", "command": ["${python}", "-c", "import sys;sys.exit(7)"], "fallback_commands": [self.local_writer("asr.json", "fallback")], "max_retries": 1, "outputs": [{"name": "source_asr_index", "path": "${task_root}/asr.json"}]}
        self.make_config([phase])
        orch.init_orchestrator(self.config_path, self.state_path)
        code, state = self.poll(150)
        self.assertEqual(0, code)
        self.assertEqual("COMPLETE", state["status"])
        self.assertEqual("fallback", (self.task / "asr.json").read_text(encoding="utf-8"))
        self.assertEqual(1, len(state["failures"]))

    def test_config_hash_drift_stops_resume(self):
        self.make_config([{"name": "ONE", "kind": "local", "command": self.local_writer("one.json"), "outputs": [{"name": "one", "path": "${task_root}/one.json"}]}])
        orch.init_orchestrator(self.config_path, self.state_path)
        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        config["default_max_retries"] = 9
        write_json(self.config_path, config)
        with self.assertRaisesRegex(ValueError, "CONFIG_HASH_MISMATCH"):
            orch.resume(self.config_path, self.state_path)

    def test_model_rejection_marks_failure(self):
        self.make_config([{"name": "SOL_QC", "kind": "model", "model_role": "gpt-5.6-sol", "response_path": "${task_root}/response.json", "outputs": []}])
        orch.init_orchestrator(self.config_path, self.state_path)
        code, _ = orch.resume(self.config_path, self.state_path)
        self.assertEqual(20, code)
        response = {"schema": orch.MODEL_RESPONSE_SCHEMA, "task_id": "task-v8", "phase": "SOL_QC", "decision": "reject", "failures": ["bad cut"], "outputs": []}
        response_path = self.task / "response.json"
        write_json(response_path, response)
        code, state = orch.supply_model(self.config_path, self.state_path, response_path)
        self.assertEqual(2, code)
        self.assertEqual("FAILED", state["status"])

    def test_required_heartbeat_must_be_registered(self):
        config = self.make_config([{"name": "ONE", "kind": "local", "command": self.local_writer("one.json"), "outputs": [{"name": "one", "path": "${task_root}/one.json"}]}])
        config["mode"] = "production"
        config["checkpoint_command"] = ["${python}", "-c", "print('checkpoint')"]
        config["heartbeat"] = {"required": True, "registration_path": str(self.task / "heartbeat.json")}
        write_json(self.config_path, config)
        with self.assertRaises(FileNotFoundError):
            orch.init_orchestrator(self.config_path, self.state_path)
        registration = {"schema": "semantic-heartbeat-registration/v11", "task_id": "task-v8", "status": "active", "automation_id": "automation-test"}
        write_json(self.task / "heartbeat.json", registration)
        state = orch.init_orchestrator(self.config_path, self.state_path)
        self.assertEqual("READY", state["status"])

    def test_model_repair_rewinds_to_allowed_phase(self):
        phases = [
            {"name": "GLOBAL_SOLVE", "kind": "model", "response_path": "${task_root}/round_${repair_round}/solve_response.json", "outputs": [{"name": "plan", "path": "${task_root}/round_${repair_round}/plan.json"}]},
            {"name": "SOL_FINAL_QC", "kind": "model", "response_path": "${task_root}/round_${repair_round}/sol_response.json", "outputs": [{"name": "qc", "path": "${task_root}/round_${repair_round}/qc.json"}], "on_repair": {"allowed_phases": ["GLOBAL_SOLVE"], "default_phase": "GLOBAL_SOLVE", "max_rounds": 2}},
        ]
        self.make_config(phases)
        orch.init_orchestrator(self.config_path, self.state_path)
        code, _ = orch.resume(self.config_path, self.state_path)
        self.assertEqual(20, code)
        round0 = self.task / "round_0"
        round0.mkdir()
        plan = round0 / "plan.json"
        plan.write_text("plan0", encoding="utf-8")
        solve_response = {"schema": orch.MODEL_RESPONSE_SCHEMA, "task_id": "task-v8", "phase": "GLOBAL_SOLVE", "decision": "pass", "outputs": [{"name": "plan", "path": str(plan), "sha256": orch.sha_file(plan)}]}
        write_json(round0 / "solve_response.json", solve_response)
        code, _ = orch.supply_model(self.config_path, self.state_path, round0 / "solve_response.json")
        self.assertEqual(20, code)
        qc = round0 / "qc.json"
        qc.write_text("rejected", encoding="utf-8")
        sol_response = {"schema": orch.MODEL_RESPONSE_SCHEMA, "task_id": "task-v8", "phase": "SOL_FINAL_QC", "decision": "repair_required", "repair_phase": "GLOBAL_SOLVE", "outputs": [{"name": "qc", "path": str(qc), "sha256": orch.sha_file(qc)}]}
        write_json(round0 / "sol_response.json", sol_response)
        code, state = orch.supply_model(self.config_path, self.state_path, round0 / "sol_response.json")
        self.assertEqual(20, code)
        self.assertEqual(1, state["repair_round"])
        self.assertEqual("GLOBAL_SOLVE", state["current_phase"])
        self.assertIn("round_1", state["pending_model_action"]["response_path"])

    def test_source_asr_reuses_complete_records_without_model_import(self):
        manifest = {"batch_id": "b", "sources": []}
        output_dir = self.task / "asr"
        output_dir.mkdir()
        for index in range(2):
            source = {"source_id": f"s{index}", "source_path": str(self.task / f"s{index}.mp4"), "source_sha256": orch.sha_file(self.config_path) if self.config_path.exists() else hashlib_value(str(index)), "processing_stage": "clean"}
            manifest["sources"].append(source)
            write_json(output_dir / f"s{index}.json", {"schema": "semantic-source-asr/v9", **source, "asr": {"segments": [{"start": 0, "end": 1, "text": "ok"}]}})
        manifest_path = self.task / "manifest.json"
        write_json(manifest_path, manifest)
        code = source_asr.main(["--manifest", str(manifest_path), "--output-dir", str(output_dir), "--index", str(self.task / "index.json"), "--progress", str(self.task / "progress.json"), "--model-root", str(self.task / "models")])
        self.assertEqual(0, code)
        self.assertEqual("pass", json.loads((self.task / "index.json").read_text(encoding="utf-8"))["decision"])

    def test_chunked_local_phase_continues_without_background_worker(self):
        command = ["${python}", "-c", "import pathlib,sys;p=pathlib.Path(sys.argv[1]);n=int(p.read_text())+1 if p.exists() else 1;p.write_text(str(n));out=pathlib.Path(sys.argv[2]);out.write_text('done') if n>=3 else None;sys.exit(0 if n>=3 else 75)", "${task_root}/counter.txt", "${task_root}/done.json"]
        phase = {"name": "SOURCE_ASR", "kind": "chunked_local", "command": command, "continue_exit_code": 75, "outputs": [{"name": "done", "path": "${task_root}/done.json"}]}
        self.make_config([phase])
        orch.init_orchestrator(self.config_path, self.state_path)
        code, state = self.poll()
        self.assertEqual(0, code)
        self.assertEqual("COMPLETE", state["status"])
        self.assertEqual("3", (self.task / "counter.txt").read_text())
        self.assertIsNone(state.get("active_worker"))

    def test_run_continuous_executes_chunks_without_scheduler_delay(self):
        command = ["${python}", "-c", "import pathlib,sys;p=pathlib.Path(sys.argv[1]);n=int(p.read_text())+1 if p.exists() else 1;p.write_text(str(n));out=pathlib.Path(sys.argv[2]);out.write_text('done') if n>=4 else None;sys.exit(0 if n>=4 else 75)", "${task_root}/continuous_counter.txt", "${task_root}/done.json"]
        self.make_config([{"name": "SOURCE_ASR", "kind": "chunked_local", "command": command, "continue_exit_code": 75, "outputs": [{"name": "done", "path": "${task_root}/done.json"}]}])
        orch.init_orchestrator(self.config_path, self.state_path)
        code, state = orch.run_continuous(self.config_path, self.state_path, 10)
        self.assertEqual(0, code)
        self.assertEqual("COMPLETE", state["status"])
        self.assertEqual("4", (self.task / "continuous_counter.txt").read_text())

    def test_abrupt_chunk_failure_with_progress_does_not_consume_retry(self):
        progress_dir = self.task / "progress"
        command = ["${python}", "-c", "import pathlib,sys;d=pathlib.Path(sys.argv[1]);d.mkdir(exist_ok=True);n=len(list(d.glob('*.json')))+1;(d/f'{n}.json').write_text('{}');out=pathlib.Path(sys.argv[2]);out.write_text('done') if n>=2 else None;sys.exit(0 if n>=2 else 9)", "${task_root}/progress", "${task_root}/done.json"]
        phase = {"name": "SOURCE_ASR", "kind": "chunked_local", "command": command, "recover_progress_on_failure": True, "progress": {"directory": "${task_root}/progress", "glob": "*.json", "total": 2}, "outputs": [{"name": "done", "path": "${task_root}/done.json"}]}
        self.make_config([phase])
        orch.init_orchestrator(self.config_path, self.state_path)
        code, state = self.poll()
        self.assertEqual(0, code)
        self.assertEqual([], state["failures"])


def hashlib_value(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()


if __name__ == "__main__":
    unittest.main()
