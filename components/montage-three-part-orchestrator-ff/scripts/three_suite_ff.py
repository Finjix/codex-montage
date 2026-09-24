from __future__ import annotations

import argparse, hashlib, json, os, subprocess, sys
from datetime import datetime
from pathlib import Path


def now(): return datetime.now().astimezone().isoformat(timespec="seconds")
def read(path): return json.loads(Path(path).read_text(encoding="utf-8-sig"))
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def atomic(path,value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True); temp=path.with_suffix(path.suffix+".tmp"); temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); os.replace(temp,path)
def run(command,allowed={0}):
    result=subprocess.run(command,capture_output=True,text=True,encoding="utf-8",errors="replace")
    if result.returncode not in allowed: raise RuntimeError(f"command failed {result.returncode}: {command}\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}")
    return result
def root(explicit):
    if explicit: return Path(explicit).resolve()
    environment=os.environ.get("MONTAGE_THREE_SUITE_FF_ROOT")
    if environment: return Path(environment).resolve()
    portable=Path(__file__).resolve().parents[3]
    if (portable/"suite_registry.json").is_file(): return portable
    codex=Path(os.environ.get("CODEX_HOME",Path.home()/".codex")); active=codex/"three-part-suite-ff"/"active.json"
    if active.is_file(): return Path(read(active)["suite_root"]).resolve()
    raise RuntimeError("cannot locate V20 FF suite")
def components(suite):
    registry=read(suite/"suite_registry.json"); return {name:{**value,"root":suite/value["relative_path"],"manifest":suite/value["manifest_relative_path"]} for name,value in registry["components"].items()}
def verify(suite):
    c=components(suite); registry=read(suite/"suite_registry.json")
    if registry.get("render_mode")!="source_frame_ranges/v1" or registry.get("seconds_only_fallback") is not False: raise RuntimeError("suite is not frame-native")
    required=[c["semantic"]["root"]/"SKILL.md",c["semantic"]["root"]/c["semantic"]["interface"]["gate_runtime"],c["semantic"]["root"]/c["semantic"]["interface"]["frame_plan_gate"],c["semantic"]["root"]/c["semantic"]["interface"]["portable_renderer"],c["semantic"]["root"]/c["semantic"]["interface"]["frame_range_repair"],c["semantic"]["root"]/c["semantic"]["interface"]["winky_ledger"],c["controller"]["root"]/"SKILL.md",c["controller"]["root"]/c["controller"]["interface"]["controller"],c["executor"]["root"]/"SKILL.md"]
    missing=[str(x) for x in required if not x.is_file()]
    if missing: raise RuntimeError(f"missing components: {missing}")
    for item in c.values():
        if not item["manifest"].is_file() or sha(item["manifest"])!=item["manifest_sha256"]: raise RuntimeError(f"stale manifest: {item['root']}")
    return c
def state(job): return read(job/"three_suite_ff_state.json")
def save(job,value,phase,summary): value["phase"]=phase; value.setdefault("events",[]).append({"at":now(),"phase":phase,"summary":summary}); atomic(job/"three_suite_ff_state.json",value)


def main():
    p=argparse.ArgumentParser(); p.add_argument("--suite-root"); sub=p.add_subparsers(dest="command",required=True)
    sub.add_parser("preflight")
    i=sub.add_parser("init"); i.add_argument("--job-dir",type=Path,required=True); i.add_argument("--authorization",required=True); i.add_argument("--source-root",type=Path,required=True); i.add_argument("--output-root",type=Path,required=True); i.add_argument("--indexes",required=True)
    s=sub.add_parser("semantic-run"); s.add_argument("--job-dir",type=Path,required=True); s.add_argument("--config",type=Path,required=True); s.add_argument("--semantic-state",type=Path,required=True)
    d=sub.add_parser("semantic-complete"); d.add_argument("--job-dir",type=Path,required=True); d.add_argument("--manifest",type=Path,required=True)
    cp=sub.add_parser("controller-preflight"); cp.add_argument("--job-dir",type=Path,required=True); cp.add_argument("--report",type=Path,required=True)
    cf=sub.add_parser("controller-finalize"); cf.add_argument("--job-dir",type=Path,required=True); cf.add_argument("--premaster-manifest",type=Path,required=True); cf.add_argument("--semantic-release",type=Path,required=True); cf.add_argument("--output-dir",type=Path,required=True); cf.add_argument("--manifest",type=Path,required=True)
    cv=sub.add_parser("controller-validate"); cv.add_argument("--job-dir",type=Path,required=True); cv.add_argument("--manifest",type=Path,required=True); cv.add_argument("--semantic-release",type=Path,required=True); cv.add_argument("--post-qc",type=Path,required=True); cv.add_argument("--opening-family-report",type=Path,required=True); cv.add_argument("--report",type=Path,required=True)
    co=sub.add_parser("complete"); co.add_argument("--job-dir",type=Path,required=True)
    st=sub.add_parser("status"); st.add_argument("--job-dir",type=Path,required=True)
    a=p.parse_args(); suite=root(a.suite_root)
    portable_python=suite/"dependencies"/"python"/"python.exe"
    if not portable_python.is_file(): raise RuntimeError(f"bundled Python missing: {portable_python}")
    if Path(sys.executable).resolve()!=portable_python.resolve():
        raise SystemExit(subprocess.call([str(portable_python),str(Path(__file__).resolve()),*sys.argv[1:]]))
    c=verify(suite)
    if a.command=="preflight": print(json.dumps({"schema":"ff-three-suite-preflight/v20.2","decision":"pass","render_mode":"source_frame_ranges/v1","seconds_only_fallback":False,"components":{k:v["tree_sha256"] for k,v in c.items()}},ensure_ascii=False)); return
    if a.command=="init":
        job=a.job_dir.resolve();
        if job.exists() and any(job.iterdir()): raise RuntimeError("refusing non-empty job")
        job.mkdir(parents=True,exist_ok=True); indexes=[]
        for part in a.indexes.split(","):
            if "-" in part: x,y=map(int,part.split("-")); indexes.extend(range(x,y+1))
            else: indexes.append(int(part))
        value={"schema":"mandatory-three-suite-ff-state/v20","created_at":now(),"authorization":a.authorization,"source_root":str(a.source_root.resolve()),"output_root":str(a.output_root.resolve()),"expected_indexes":sorted(set(indexes)),"components":{k:{**{x:y for x,y in v.items() if x not in ("root","manifest")},"root":str(v["root"]),"manifest":str(v["manifest"])} for k,v in c.items()},"semantic_invocations":[],"controller_invocations":[],"events":[]}; save(job,value,"initialized","bound V20 semantic, FF controller and executor"); print(job/"three_suite_ff_state.json"); return
    job=a.job_dir.resolve(); value=state(job)
    if a.command=="semantic-run":
        semantic=c["semantic"]; command=[sys.executable,str(semantic["root"]/semantic["interface"]["orchestrator"]),semantic["interface"]["run_command"],"--config",str(a.config.resolve()),"--state",str(a.semantic_state.resolve())]; result=run(command,{0,20,22}); value["semantic_invocations"].append({"at":now(),"command":command,"returncode":result.returncode}); save(job,value,"semantic_invoked",f"semantic returned {result.returncode}"); print(result.stdout); raise SystemExit(result.returncode)
    if a.command=="semantic-complete":
        manifest=read(a.manifest); expected=c["semantic"]["interface"]["completion_schema"]
        if manifest.get("schema")!=expected: raise RuntimeError("semantic completion schema mismatch")
        authorized=manifest.get("authorized_outputs") if isinstance(manifest.get("authorized_outputs"),list) else []
        if manifest.get("decision")!="pass" or manifest.get("package_version")!="20.2.5" or int(manifest.get("requested_outputs",0) or 0)!=len(value.get("expected_indexes",[])) or len(authorized)!=len(value.get("expected_indexes",[])) or len(set(authorized))!=len(authorized): raise RuntimeError("complete semantic authorization set required")
        evidence=manifest.get("evidence") if isinstance(manifest.get("evidence"),dict) else {}
        required=("v20_prelock_audit.json","candidate_inventory.json","batch_lock_request.json","work_order.json")
        bound={}
        for name in required:
            item=evidence.get(name) if isinstance(evidence.get(name),dict) else {}; path=Path(str(item.get("path","")))
            if not path.is_file() or sha(path)!=str(item.get("sha256","")).lower(): raise RuntimeError(f"semantic completion missing bound {name}")
            bound[name]=path
        audit=read(bound["v20_prelock_audit.json"]); packaged_registry=c["semantic"]["root"]/"references"/"wuzimu-v20-invalid-intervals.json"
        if audit.get("schema")!="v20-fail-closed-prelock-report/v3" or audit.get("decision")!="pass" or audit.get("registry_sha256")!=sha(packaged_registry) or audit.get("request_sha256")!=sha(bound["batch_lock_request.json"]) or int(audit.get("declared_plan_count",0) or 0)!=len(authorized) or int(audit.get("requested_outputs",0) or 0)!=len(authorized): raise RuntimeError("current packaged exhaustive prelock audit required")
        request_value=read(bound["batch_lock_request.json"]); request_ids=[str(plan.get("plan_id") or "") for plan in request_value.get("plans",[])]; work_value=read(bound["work_order.json"])
        if set(request_ids)!=set(authorized) or len(request_ids)!=len(authorized) or int(work_value.get("requested_outputs",0) or 0)!=len(authorized): raise RuntimeError("semantic authorized outputs must equal complete request scope")
        recheck=job/"v20_prelock_recheck.json"; fail_closed_script=c["semantic"]["root"]/c["semantic"]["interface"]["fail_closed"]
        run([sys.executable,str(fail_closed_script),"audit-request","--request",str(bound["batch_lock_request.json"]),"--registry",str(packaged_registry),"--output",str(recheck)])
        recheck_value=read(recheck)
        if recheck_value.get("schema")!="v20-fail-closed-prelock-report/v3" or recheck_value.get("decision")!="pass": raise RuntimeError("independent packaged prelock recheck failed")
        value["semantic_prelock_recheck"]={"path":str(recheck.resolve()),"sha256":sha(recheck)}
        value["semantic_completion"]={"path":str(a.manifest.resolve()),"sha256":sha(a.manifest)}; save(job,value,"semantic_completed","V20 semantic receipt verified"); return
    controller=c["controller"]; script=controller["root"]/controller["interface"]["controller"]
    if a.command=="controller-preflight":
        if "semantic_completion" not in value: raise RuntimeError("semantic completion required")
        run([sys.executable,str(script),"preflight","--output",str(a.report.resolve())]); value["controller_preflight"]={"path":str(a.report.resolve()),"sha256":sha(a.report)}; save(job,value,"controller_preflight","FF controller preflight passed"); return
    if a.command=="controller-finalize":
        if "controller_preflight" not in value: raise RuntimeError("controller preflight required")
        command=[sys.executable,str(script),"finalize","--premaster-manifest",str(a.premaster_manifest.resolve()),"--semantic-release",str(a.semantic_release.resolve()),"--output-dir",str(a.output_dir.resolve()),"--manifest",str(a.manifest.resolve())]; run(command); value["controller_invocations"].append({"at":now(),"command":command}); value["delivery_manifest"]={"path":str(a.manifest.resolve()),"sha256":sha(a.manifest)}; save(job,value,"controller_outputs","FF exact-60 outputs completed"); return
    if a.command=="controller-validate":
        command=[sys.executable,str(script),"validate","--manifest",str(a.manifest.resolve()),"--semantic-release",str(a.semantic_release.resolve()),"--post-qc",str(a.post_qc.resolve()),"--opening-family-report",str(a.opening_family_report.resolve()),"--report",str(a.report.resolve())]; run(command); report=read(a.report)
        if report.get("decision")!="pass": raise RuntimeError("controller validation rejected")
        value["controller_validation"]={"path":str(a.report.resolve()),"sha256":sha(a.report),"post_qc":str(a.post_qc.resolve()),"post_qc_sha256":sha(a.post_qc),"opening_family_report":str(a.opening_family_report.resolve()),"opening_family_report_sha256":sha(a.opening_family_report)}; save(job,value,"controller_validated","post-encode, opening-family and technical QC passed"); return
    if a.command=="complete":
        if not all(k in value for k in ("semantic_completion","controller_preflight","delivery_manifest","controller_validation")) or not value.get("controller_invocations"): raise RuntimeError("mandatory receipts incomplete")
        receipt={"schema":"mandatory-three-suite-ff-completion/v20","completed_at":now(),"semantic":value["semantic_completion"],"controller_preflight":value["controller_preflight"],"delivery_manifest":value["delivery_manifest"],"controller_validation":value["controller_validation"],"components":value["components"]}; atomic(job/"three_suite_ff_completion.json",receipt); value["completion"]={"path":str(job/"three_suite_ff_completion.json"),"sha256":sha(job/"three_suite_ff_completion.json")}; save(job,value,"complete","V20 semantic plus FF final receipts complete"); print(value["completion"]["path"]); return
    print(json.dumps(value,ensure_ascii=False,indent=2))


if __name__=="__main__": main()
