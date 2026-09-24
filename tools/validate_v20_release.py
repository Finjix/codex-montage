from __future__ import annotations

import json, subprocess
from datetime import datetime
from pathlib import Path


ROOT=Path(__file__).resolve().parent.parent
ARTIFACTS=ROOT/"artifacts"
ARTIFACTS.mkdir(exist_ok=True)
PYTHON=ROOT/"dependencies"/"python"/"python.exe"
V20=ROOT/"components"/"semantic-analysis-training-backup-v20"
CONTROLLER=ROOT/"components"/"ffmpeg-montage-controller"
EXECUTOR=ROOT/"components"/"montage-three-part-orchestrator-ff"


def run(name,command,expected={0}):
 result=subprocess.run(command,capture_output=True,text=True,encoding="utf-8",errors="replace")
 return {"name":name,"command":[str(x) for x in command],"returncode":result.returncode,"passed":result.returncode in expected,"stdout":result.stdout[-12000:],"stderr":result.stderr[-12000:]}


checks=[]
checks.append(run("semantic_regressions",[PYTHON,"-m","unittest","discover","-s",str(V20/"tests"),"-p","test_*.py","-v"]))
checks.append(run("ff_controller_regressions",[PYTHON,"-m","unittest","discover","-s",str(CONTROLLER/"tests"),"-p","test_*.py","-v"]))
checks.append(run("release_integrity_regressions",[PYTHON,"-m","unittest","discover","-s",str(ROOT/"tools"),"-p","test_release_integrity.py","-v"]))
validator=ROOT/"tools"/"validate_skill.py"
for name,path in (("semantic_skill",V20),("controller_skill",CONTROLLER),("executor_skill",EXECUTOR)):
 checks.append(run(name,[PYTHON,"-X","utf8",str(validator),str(path)]))
checks.append(run("semantic_package",[PYTHON,str(V20/"scripts"/"validate_package.py"),"verify","--root",str(V20)]))
checks.append(run("suite_tree",[PYTHON,str(ROOT/"tools"/"build_ff_suite.py"),"verify"]))
checks.append(run("suite_preflight",[PYTHON,str(EXECUTOR/"scripts"/"three_suite_ff.py"),"--suite-root",str(ROOT),"preflight"]))
checks.append(run("ff_controller_preflight",[PYTHON,str(CONTROLLER/"scripts"/"ffmpeg_controller.py"),"preflight","--output",str(ARTIFACTS/"ff-controller-preflight.json")]))
checks.append(run("bundled_runtime_paths",[PYTHON,str(CONTROLLER/"scripts"/"runtime_paths.py")]))
checks.append(run("frame_native_scripts_compile",[PYTHON,"-m","py_compile",str(V20/"scripts"/"v20_frame_plan_gate.py"),str(V20/"scripts"/"v20_dense_boundary_evidence.py"),str(V20/"scripts"/"v20_boundary_signal_scan.py"),str(V20/"scripts"/"v20_review_boundary.py"),str(V20/"scripts"/"v20_fail_closed.py"),str(V20/"scripts"/"portable_frame_renderer.py"),str(V20/"scripts"/"frame_range_repair.py"),str(V20/"scripts"/"winky_ledger.py"),str(CONTROLLER/"scripts"/"post_encode_evidence.py"),str(CONTROLLER/"scripts"/"boundary_signal_scan.py"),str(CONTROLLER/"scripts"/"review_post_encode.py")]))
template=(V20/"references"/"pipeline-config.template.json").read_text(encoding="utf-8")
unresolved=any(token in template for token in ("REPLACE_ABSOLUTE_RENDER_LOCKED_PLAN_PY","REPLACE_ABSOLUTE_GOVERNANCE_LEDGER_PY"))
checks.append({"name":"portable_runtime_config_closed","command":[],"returncode":2 if unresolved else 0,"passed":not unresolved,"stdout":"bundled renderer and ledger paths" if not unresolved else "unresolved runtime placeholder","stderr":""})
runtime_lock=ROOT/"runtime-lock.json"
checks.append({"name":"runtime_lock_present","command":[],"returncode":0 if runtime_lock.is_file() else 2,"passed":runtime_lock.is_file(),"stdout":str(runtime_lock),"stderr":""})
checks.append(run("bundled_python_modules",[PYTHON,"-c","import sys; assert sys.version_info[:3] == (3, 13, 15); import faster_whisper, numpy, PIL, av, cv2, onnxruntime, yaml, pywinauto"]))
model_bin=ROOT/"dependencies"/"models"/"models--mobiuslabsgmbh--faster-whisper-large-v3-turbo"/"snapshots"/"0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"/"model.bin"
checks.append({"name":"complete_model_size","command":[],"returncode":0 if model_bin.is_file() and model_bin.stat().st_size>1_500_000_000 else 2,"passed":model_bin.is_file() and model_bin.stat().st_size>1_500_000_000,"stdout":str(model_bin),"stderr":""})
old_job=Path(r"C:\Users\dd\Documents\Codex\2026-09-10\montage-three-part-orchestrator-c-users\work\wuzimu-beauty-v19-40x23-26")
if (old_job/"round_1"/"batch_lock_request.json").is_file():
 output=ARTIFACTS/"v19-bad-batch-v20-audit.json"
 check=run("known_bad_v19_batch_must_reject",[PYTHON,str(V20/"scripts"/"v20_fail_closed.py"),"audit-request","--request",str(old_job/"round_1"/"batch_lock_request.json"),"--registry",str(V20/"references"/"wuzimu-v20-invalid-intervals.json"),"--output",str(output)],{2})
 if output.is_file():
  value=json.loads(output.read_text(encoding="utf-8")); check["passed"]=check["passed"] and value.get("decision")=="reject" and any(x.get("code")=="V20_USER_INVALID_INTERVAL" for x in value.get("failures",[]))
 checks.append(check)
report={"schema":"v20.2-ff-suite-release-validation/v1","version":"20.2.5","render_mode":"source_frame_ranges/v1","adjacent_same_source":"coalesce_monotonic_touching_or_overlapping_frame_ranges","seconds_only_fallback":False,"created_at":datetime.now().astimezone().isoformat(timespec="seconds"),"decision":"pass" if all(x["passed"] for x in checks) else "reject","checks":checks}
(ARTIFACTS/"v20-validation-report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps({"decision":report["decision"],"checks":len(checks),"passed":sum(x["passed"] for x in checks)},ensure_ascii=False))
raise SystemExit(0 if report["decision"]=="pass" else 2)
