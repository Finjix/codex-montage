#!/usr/bin/env python3
"""Resumable V8 phase orchestrator for local workers and Codex model handoffs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "semantic-production-orchestrator-state/v11"
CONFIG_SCHEMA = "semantic-production-pipeline-config/v11"
MODEL_RESPONSE_SCHEMA = "semantic-model-phase-response/v11"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8-sig") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"object required: {path}")
    return value


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
    os.replace(temp, path)


def save_state(path: Path, state: dict, backup: bool = True) -> None:
    if backup and path.is_file():
        backup_dir = path.parent / ".v11" / "state_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        shutil.copy2(path, backup_dir / f"{path.name}.bak-{stamp}")
    state["updated_at"] = now_iso()
    state["revision"] = int(state.get("revision", 0)) + 1
    atomic_json(path, state)


def validate_config(config: dict) -> None:
    if config.get("schema") != CONFIG_SCHEMA:
        raise ValueError("pipeline config schema mismatch")
    if not isinstance(config.get("task_id"), str) or not config["task_id"]:
        raise ValueError("task_id required")
    if not isinstance(config.get("task_root"), str):
        raise ValueError("task_root required")
    phases = config.get("phases")
    if not isinstance(phases, list) or not phases:
        raise ValueError("non-empty phases required")
    names = []
    for phase in phases:
        if not isinstance(phase, dict) or phase.get("kind") not in {"local", "chunked_local", "model"} or not phase.get("name"):
            raise ValueError("each phase needs name and local/chunked_local/model kind")
        if phase["kind"] in {"local", "chunked_local"} and not isinstance(phase.get("command"), list):
            raise ValueError(f"local phase command required: {phase.get('name')}")
        names.append(phase["name"])
    if len(names) != len(set(names)):
        raise ValueError("phase names must be unique")
    if config.get("mode", "production") == "production":
        if config.get("heartbeat", {}).get("required") is not True:
            raise ValueError("production requires heartbeat")
        if not isinstance(config.get("checkpoint_command"), list) or not config["checkpoint_command"]:
            raise ValueError("production requires checkpoint_command")


def verify_heartbeat_registration(config: dict) -> None:
    heartbeat = config.get("heartbeat", {})
    if not isinstance(heartbeat, dict) or heartbeat.get("required") is not True:
        return
    raw_path = str(heartbeat.get("registration_path", "")).replace("${task_root}", str(Path(config["task_root"]).resolve())).replace("${package_root}", str(Path(__file__).resolve().parent.parent))
    path = Path(raw_path)
    expected_hash = heartbeat.get("registration_sha256")
    ref = verify_ref(str(path), expected_hash)
    registration = load_json(Path(ref["path"]))
    if registration.get("schema") != "semantic-heartbeat-registration/v11" or registration.get("task_id") != config.get("task_id") or registration.get("status") != "active" or not registration.get("automation_id"):
        raise ValueError("HEARTBEAT_REGISTRATION_INVALID")


def expand(value: str, config: dict, state_path: Path) -> str:
    package_root = Path(__file__).resolve().parent.parent
    tokens = {
        "${task_root}": str(Path(config["task_root"]).resolve()),
        "${package_root}": str(package_root),
        "${python}": sys.executable,
        "${state_path}": str(state_path.resolve()),
    }
    repair_round = 0
    if state_path.is_file():
        try:
            repair_round = int(load_json(state_path).get("repair_round", 0))
        except Exception:
            repair_round = 0
    tokens["${repair_round}"] = str(repair_round)
    for key, replacement in tokens.items():
        value = value.replace(key, replacement)
    for key, replacement in config.get("variables", {}).items():
        value = value.replace("${" + str(key) + "}", str(replacement))
    return value


def expand_command(command: list, config: dict, state_path: Path) -> list[str]:
    return [expand(str(item), config, state_path) for item in command]


def verify_ref(path_value: str, hash_value: str | None = None) -> dict:
    path = Path(path_value).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    actual = sha_file(path)
    if hash_value and actual.lower() != hash_value.lower():
        raise ValueError(f"hash mismatch: {path}")
    return {"path": str(path), "sha256": actual}


def collect_outputs(phase: dict, config: dict, state_path: Path) -> dict:
    outputs = {}
    for item in phase.get("outputs", []):
        name = item.get("name")
        path = expand(str(item.get("path", "")), config, state_path)
        if not name:
            raise ValueError(f"unnamed output in phase {phase['name']}")
        outputs[name] = verify_ref(path)
    return outputs


def progress_snapshot(phase: dict, config: dict, state_path: Path) -> dict | None:
    spec = phase.get("progress")
    if not isinstance(spec, dict):
        return None
    directory = Path(expand(str(spec.get("directory", "")), config, state_path))
    pattern = str(spec.get("glob", "*"))
    count = len(list(directory.glob(pattern))) if directory.is_dir() else 0
    return {"completed": count, "total": spec.get("total"), "directory": str(directory), "glob": pattern}


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ProcessLookupError, ValueError):
        return False


def emit_checkpoint(config: dict, state_path: Path, state: dict, summary: str) -> None:
    template = config.get("checkpoint_command")
    if not isinstance(template, list) or not template:
        return
    command = [
        expand(str(item), config, state_path)
        .replace("${phase}", str(state.get("current_phase")))
        .replace("${summary}", summary)
        .replace("${task_id}", str(state.get("task_id")))
        for item in template
    ]
    result = subprocess.run(command, cwd=config["task_root"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        state.setdefault("warnings", []).append({"at": now_iso(), "code": "CHECKPOINT_COMMAND_FAILED", "detail": result.stderr[-1000:]})


def phase_result_path(config: dict, phase_index: int, attempt: int) -> Path:
    phase = config["phases"][phase_index]
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in phase["name"])
    return Path(config["task_root"]) / ".v8" / "phase_results" / f"{phase_index:02d}-{safe}-attempt{attempt}.json"


def phase_command(phase: dict, attempt: int) -> list:
    if attempt == 0:
        return phase["command"]
    fallbacks = phase.get("fallback_commands", [])
    index = attempt - 1
    if index >= len(fallbacks):
        raise IndexError("no fallback command")
    return fallbacks[index]


def launch_worker(config: dict, state_path: Path, state: dict) -> None:
    index = int(state["phase_index"])
    phase = config["phases"][index]
    attempt = int(state.get("phase_attempt", 0))
    command = expand_command(phase_command(phase, attempt), config, state_path)
    result_path = phase_result_path(config, index, attempt)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    log_dir = Path(config["task_root"]) / ".v8" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / f"{index:02d}-{phase['name']}-attempt{attempt}.stdout.log"
    stderr_path = log_dir / f"{index:02d}-{phase['name']}-attempt{attempt}.stderr.log"
    worker = [sys.executable, str(Path(__file__).resolve()), "_worker", "--command-json", json.dumps(command, ensure_ascii=False), "--cwd", str(Path(config["task_root"]).resolve()), "--result", str(result_path), "--stdout", str(stdout_path), "--stderr", str(stderr_path)]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(worker, creationflags=flags, close_fds=True)
    worker_pid = process.pid
    if os.name == "nt":
        process._handle.Close()
        process.returncode = 0
    state["status"] = "BACKGROUND_RUNNING"
    state["active_worker"] = {"pid": worker_pid, "phase": phase["name"], "attempt": attempt, "result_path": str(result_path), "stdout_path": str(stdout_path), "stderr_path": str(stderr_path), "started_at": now_iso(), "command": command}
    state["last_progress"] = progress_snapshot(phase, config, state_path)
    save_state(state_path, state)
    emit_checkpoint(config, state_path, state, f"launched local phase {phase['name']} attempt {attempt}")


def complete_phase(config: dict, state_path: Path, state: dict, outputs: dict, detail: str) -> None:
    phase = config["phases"][int(state["phase_index"])]
    state.setdefault("artifacts", {}).update(outputs)
    state.setdefault("history", []).append({"phase": phase["name"], "completed_at": now_iso(), "attempt": int(state.get("phase_attempt", 0)), "detail": detail, "outputs": outputs})
    state["phase_index"] = int(state["phase_index"]) + 1
    state["phase_attempt"] = 0
    state["chunk_run"] = 0
    state["active_worker"] = None
    state["pending_model_action"] = None
    if state["phase_index"] >= len(config["phases"]):
        state["status"] = "COMPLETE"
        state["current_phase"] = "COMPLETE"
    else:
        state["status"] = "READY"
        state["current_phase"] = config["phases"][state["phase_index"]]["name"]
    save_state(state_path, state)
    emit_checkpoint(config, state_path, state, f"completed phase {phase['name']}")


def fail_or_retry(config: dict, state_path: Path, state: dict, detail: str) -> None:
    phase = config["phases"][int(state["phase_index"])]
    attempt = int(state.get("phase_attempt", 0))
    max_retries = int(phase.get("max_retries", config.get("default_max_retries", 1)))
    fallback_count = len(phase.get("fallback_commands", []))
    state.setdefault("failures", []).append({"phase": phase["name"], "attempt": attempt, "at": now_iso(), "detail": detail})
    if attempt < max_retries and attempt < fallback_count:
        state["phase_attempt"] = attempt + 1
        state["chunk_run"] = 0
        state["active_worker"] = None
        state["status"] = "READY"
        save_state(state_path, state)
        emit_checkpoint(config, state_path, state, f"retrying phase {phase['name']} after: {detail[:300]}")
    else:
        state["status"] = "FAILED"
        state["active_worker"] = None
        state["failure"] = {"phase": phase["name"], "detail": detail, "at": now_iso()}
        save_state(state_path, state)
        emit_checkpoint(config, state_path, state, f"phase failed {phase['name']}: {detail[:300]}")


def init_orchestrator(config_path: Path, state_path: Path) -> dict:
    if state_path.exists():
        raise FileExistsError(state_path)
    config = load_json(config_path)
    validate_config(config)
    verify_heartbeat_registration(config)
    first = config["phases"][0]["name"]
    state = {"schema": SCHEMA, "task_id": config["task_id"], "config_path": str(config_path.resolve()), "config_sha256": sha_file(config_path), "phase_index": 0, "current_phase": first, "phase_attempt": 0, "chunk_run": 0, "repair_round": 0, "status": "READY", "revision": 0, "updated_at": now_iso(), "active_worker": None, "pending_model_action": None, "artifacts": {}, "history": [], "failures": [], "warnings": []}
    save_state(state_path, state, backup=False)
    emit_checkpoint(config, state_path, state, "orchestrator initialized")
    return state


def load_bound(config_path: Path, state_path: Path) -> tuple[dict, dict]:
    config = load_json(config_path)
    validate_config(config)
    verify_heartbeat_registration(config)
    state = load_json(state_path)
    if state.get("schema") != SCHEMA or state.get("task_id") != config.get("task_id"):
        raise ValueError("state/config identity mismatch")
    if state.get("config_sha256") != sha_file(config_path):
        raise ValueError("CONFIG_HASH_MISMATCH")
    for name, ref in state.get("artifacts", {}).items():
        verify_ref(ref["path"], ref["sha256"])
    return config, state


def progress_count(snapshot: dict | None) -> int:
    return int(snapshot.get("completed", 0)) if isinstance(snapshot, dict) else 0


def run_local_heartbeat_slice(config: dict, state_path: Path, state: dict, phase: dict) -> tuple[int, dict]:
    attempt = int(state.get("phase_attempt", 0))
    chunk = int(state.get("chunk_run", 0))
    command = expand_command(phase_command(phase, attempt), config, state_path)
    log_dir = Path(config["task_root"]) / ".v11" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / f"{state['phase_index']:02d}-{phase['name']}-attempt{attempt}-chunk{chunk}.stdout.log"
    stderr_path = log_dir / f"{state['phase_index']:02d}-{phase['name']}-attempt{attempt}-chunk{chunk}.stderr.log"
    before = progress_snapshot(phase, config, state_path)
    timed_out = False
    with stdout_path.open("w", encoding="utf-8", errors="replace") as out, stderr_path.open("w", encoding="utf-8", errors="replace") as err:
        try:
            result = subprocess.run(command, cwd=config["task_root"], stdout=out, stderr=err, shell=False, timeout=float(phase.get("slice_timeout_seconds", config.get("slice_timeout_seconds", 240))))
            code = int(result.returncode)
        except subprocess.TimeoutExpired:
            timed_out = True
            code = 124
    after = progress_snapshot(phase, config, state_path)
    gained = progress_count(after) > progress_count(before)
    state["last_progress"] = after
    state["active_worker"] = None
    state["chunk_run"] = chunk + 1
    if code == 0:
        try:
            outputs = collect_outputs(phase, config, state_path)
        except Exception as exc:
            fail_or_retry(config, state_path, state, f"output verification failed: {type(exc).__name__}: {exc}")
            return 2, load_json(state_path)
        complete_phase(config, state_path, state, outputs, "bounded local slice completed and outputs verified")
        updated = load_json(state_path)
        return (0 if updated["status"] == "COMPLETE" else 22), updated
    if phase["kind"] == "chunked_local" and (code == int(phase.get("continue_exit_code", 75)) or (gained and phase.get("recover_progress_on_failure", True))):
        state["status"] = "CHUNK_PENDING"
        state.setdefault("history", []).append({"phase": phase["name"], "at": now_iso(), "detail": "chunk_progress", "returncode": code, "timed_out": timed_out, "progress": after})
        save_state(state_path, state)
        emit_checkpoint(config, state_path, state, f"chunk {chunk} completed/progressed for {phase['name']}")
        return 22, state
    stderr_tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-4000:]
    fail_or_retry(config, state_path, state, f"bounded local exit={code} timeout={timed_out} stderr={stderr_tail}")
    updated = load_json(state_path)
    return (22 if updated["status"] == "READY" else 2), updated


def resume(config_path: Path, state_path: Path) -> tuple[int, dict]:
    config, state = load_bound(config_path, state_path)
    if state["status"] == "COMPLETE":
        return 0, state
    if state["status"] == "FAILED":
        return 2, state
    if state["status"] == "WAITING_MODEL":
        return 20, state
    if state["status"] == "CHUNK_PENDING":
        state["status"] = "READY"
        save_state(state_path, state)
    if int(state["phase_index"]) >= len(config["phases"]):
        state["status"] = "COMPLETE"
        save_state(state_path, state)
        return 0, state
    phase = config["phases"][int(state["phase_index"])]
    state["current_phase"] = phase["name"]
    if phase["kind"] == "model":
        request = {"schema": "semantic-model-phase-request/v11", "task_id": state["task_id"], "phase": phase["name"], "created_at": now_iso(), "instructions": expand(str(phase.get("instructions", "")), config, state_path), "model_role": phase.get("model_role", "gpt-5.6-terra"), "inputs": state.get("artifacts", {}), "required_outputs": phase.get("outputs", []), "response_path": expand(str(phase["response_path"]), config, state_path)}
        pending_path = Path(config["task_root"]) / ".v11" / "pending_model_action.json"
        atomic_json(pending_path, request)
        state["status"] = "WAITING_MODEL"
        state["pending_model_action"] = {"path": str(pending_path.resolve()), "sha256": sha_file(pending_path), "phase": phase["name"], "response_path": request["response_path"]}
        save_state(state_path, state)
        emit_checkpoint(config, state_path, state, f"waiting for Codex model phase {phase['name']}")
        return 20, state
    return run_local_heartbeat_slice(config, state_path, state, phase)


def supply_model(config_path: Path, state_path: Path, response_path: Path) -> tuple[int, dict]:
    config, state = load_bound(config_path, state_path)
    if state.get("status") != "WAITING_MODEL":
        raise ValueError("not waiting for model phase")
    response = load_json(response_path)
    phase = config["phases"][int(state["phase_index"])]
    if response.get("schema") != MODEL_RESPONSE_SCHEMA or response.get("task_id") != state["task_id"] or response.get("phase") != phase["name"]:
        raise ValueError("model response binding mismatch")
    if response.get("decision") == "repair_required":
        outputs = {}
        expected_paths = {item["name"]: Path(expand(str(item["path"]), config, state_path)).resolve() for item in phase.get("outputs", [])}
        for item in response.get("outputs", []):
            if item.get("name") not in expected_paths or Path(item["path"]).resolve() != expected_paths[item["name"]]:
                raise ValueError(f"model repair output path mismatch: {item.get('name')}")
            outputs[item["name"]] = verify_ref(item["path"], item["sha256"])
        required = {item["name"] for item in phase.get("outputs", [])}
        if required - set(outputs):
            raise ValueError("model repair response missing outputs: " + ",".join(sorted(required - set(outputs))))
        repair = phase.get("on_repair", {})
        allowed = repair.get("allowed_phases", [])
        target = response.get("repair_phase") or repair.get("default_phase")
        if target not in allowed:
            raise ValueError(f"repair phase not allowed: {target}")
        new_round = int(state.get("repair_round", 0)) + 1
        if new_round > int(repair.get("max_rounds", 0)):
            fail_or_retry(config, state_path, state, "repair round limit exhausted")
            return 2, load_json(state_path)
        target_index = next((index for index, item in enumerate(config["phases"]) if item["name"] == target), None)
        if target_index is None or target_index >= int(state["phase_index"]):
            raise ValueError("repair target must be an earlier phase")
        state.setdefault("artifacts", {}).update(outputs)
        state.setdefault("history", []).append({"phase": phase["name"], "completed_at": now_iso(), "detail": "repair_required", "repair_round": new_round, "outputs": outputs})
        state["repair_round"] = new_round
        state["phase_index"] = target_index
        state["current_phase"] = target
        state["phase_attempt"] = 0
        state["chunk_run"] = 0
        state["status"] = "READY"
        state["active_worker"] = None
        state["pending_model_action"] = None
        save_state(state_path, state)
        emit_checkpoint(config, state_path, state, f"repair round {new_round} rewound to {target}")
        return resume(config_path, state_path)
    if response.get("decision") != "pass":
        fail_or_retry(config, state_path, state, f"model phase rejected: {response.get('failures')}")
        return 2, load_json(state_path)
    outputs = {}
    expected_paths = {item["name"]: Path(expand(str(item["path"]), config, state_path)).resolve() for item in phase.get("outputs", [])}
    for item in response.get("outputs", []):
        if item.get("name") not in expected_paths or Path(item["path"]).resolve() != expected_paths[item["name"]]:
            raise ValueError(f"model output path mismatch: {item.get('name')}")
        outputs[item["name"]] = verify_ref(item["path"], item["sha256"])
    required = {item["name"] for item in phase.get("outputs", [])}
    if required - set(outputs):
        raise ValueError("model response missing outputs: " + ",".join(sorted(required - set(outputs))))
    complete_phase(config, state_path, state, outputs, f"model response {sha_file(response_path)} verified")
    return resume(config_path, state_path)


def status(config_path: Path, state_path: Path) -> dict:
    config, state = load_bound(config_path, state_path)
    if state.get("active_worker") and int(state.get("phase_index", 0)) < len(config["phases"]):
        phase = config["phases"][int(state["phase_index"])]
        state["worker_running"] = pid_alive(state["active_worker"].get("pid"))
        state["progress"] = progress_snapshot(phase, config, state_path)
    return state


def run_continuous(config_path: Path, state_path: Path, budget_seconds: float) -> tuple[int, dict]:
    deadline = time.monotonic() + max(1.0, budget_seconds)
    last = load_json(state_path)
    while time.monotonic() < deadline:
        code, last = resume(config_path, state_path)
        if code != 22:
            return code, last
    last["status"] = "CONTINUATION_REQUIRED"
    return 22, last


def worker(command: list[str], cwd: Path, result_path: Path, stdout_path: Path, stderr_path: Path) -> int:
    started = now_iso()
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    with stdout_path.open("w", encoding="utf-8", errors="replace") as out, stderr_path.open("w", encoding="utf-8", errors="replace") as err:
        try:
            result = subprocess.run(command, cwd=cwd, stdout=out, stderr=err, shell=False)
            code = int(result.returncode)
        except Exception as exc:
            err.write(f"{type(exc).__name__}: {exc}\n")
            code = 127
    stderr_tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-4000:]
    atomic_json(result_path, {"schema": "semantic-local-phase-result/v8", "command": command, "started_at": started, "completed_at": now_iso(), "returncode": code, "stdout_path": str(stdout_path.resolve()), "stderr_path": str(stderr_path.resolve()), "stderr_tail": stderr_tail})
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    init_p = sub.add_parser("init")
    init_p.add_argument("--config", type=Path, required=True)
    init_p.add_argument("--state", type=Path, required=True)
    resume_p = sub.add_parser("resume")
    resume_p.add_argument("--config", type=Path, required=True)
    resume_p.add_argument("--state", type=Path, required=True)
    continuous_p = sub.add_parser("run-continuous")
    continuous_p.add_argument("--config", type=Path, required=True)
    continuous_p.add_argument("--state", type=Path, required=True)
    continuous_p.add_argument("--budget-seconds", type=float, default=1800)
    status_p = sub.add_parser("status")
    status_p.add_argument("--config", type=Path, required=True)
    status_p.add_argument("--state", type=Path, required=True)
    supply_p = sub.add_parser("supply-model")
    supply_p.add_argument("--config", type=Path, required=True)
    supply_p.add_argument("--state", type=Path, required=True)
    supply_p.add_argument("--response", type=Path, required=True)
    worker_p = sub.add_parser("_worker")
    worker_p.add_argument("--command-json", required=True)
    worker_p.add_argument("--cwd", type=Path, required=True)
    worker_p.add_argument("--result", type=Path, required=True)
    worker_p.add_argument("--stdout", type=Path, required=True)
    worker_p.add_argument("--stderr", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            result = init_orchestrator(args.config, args.state)
            print(json.dumps(result, ensure_ascii=False))
            return 0
        if args.command == "resume":
            code, result = resume(args.config, args.state)
            print(json.dumps({"status": result.get("status"), "phase": result.get("current_phase"), "progress": result.get("last_progress"), "pending_model_action": result.get("pending_model_action"), "failure": result.get("failure")}, ensure_ascii=False))
            return code
        if args.command == "run-continuous":
            code, result = run_continuous(args.config, args.state, args.budget_seconds)
            print(json.dumps({"status": result.get("status"), "phase": result.get("current_phase"), "progress": result.get("last_progress"), "pending_model_action": result.get("pending_model_action"), "failure": result.get("failure")}, ensure_ascii=False))
            return code
        if args.command == "status":
            print(json.dumps(status(args.config, args.state), ensure_ascii=False))
            return 0
        if args.command == "supply-model":
            code, result = supply_model(args.config, args.state, args.response)
            print(json.dumps({"status": result.get("status"), "phase": result.get("current_phase"), "pending_model_action": result.get("pending_model_action")}, ensure_ascii=False))
            return code
        if args.command == "_worker":
            return worker(json.loads(args.command_json), args.cwd, args.result, args.stdout, args.stderr)
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False), file=sys.stderr)
        return 10
    return 10


if __name__ == "__main__":
    raise SystemExit(main())
