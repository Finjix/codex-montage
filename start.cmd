@echo off
setlocal
set "SUITE_DIR=%~dp0"
if not exist "%SUITE_DIR%dependencies\python\python.exe" (
  echo Bundled Python is missing: "%SUITE_DIR%dependencies\python\python.exe"
  pause
  exit /b 2
)
"%SUITE_DIR%dependencies\python\python.exe" -X utf8 -c "import pathlib,sys; body=pathlib.Path(sys.argv[1]).read_text(encoding='utf-8-sig').split(chr(10)+'# BEGIN PYTHON DEPLOY'+chr(10),1)[1]; exec(compile(body,sys.argv[1],'exec'))" "%~f0" %*
set "EXIT_CODE=%errorlevel%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo Deployment failed. Keep this window open and send the error text.
  pause
)
exit /b %EXIT_CODE%
# BEGIN PYTHON DEPLOY
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path


VERSION = "20.2.5"
MODEL = (
    "models/models--mobiuslabsgmbh--faster-whisper-large-v3-turbo/"
    "snapshots/0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf/model.bin"
)
SKILLS = (
    "ffmpeg-montage-controller",
    "semantic-analysis-training-backup-v20",
    "montage-three-part-orchestrator-ff",
)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def run_checked(label: str, command: list[str], show_output: bool = False) -> None:
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise RuntimeError(
            f"{label} failed ({result.returncode})\n"
            f"{result.stdout[-4000:]}\n{result.stderr[-4000:]}"
        )
    if show_output and result.stdout.strip():
        print(result.stdout.strip())


def create_junction(link: Path, target: Path, backup_root: Path, backup_name: str) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(link):
        backup_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        link.rename(backup_root / f"{backup_name}.bak-{stamp}")
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        raise RuntimeError(f"Cannot register Codex skill {link}: {result.stdout} {result.stderr}")


def copy_filter(directory: str, names: list[str]) -> set[str]:
    ignored = {name for name in names if name in {".git", "artifacts", "__pycache__", ".pytest_cache"}
               or name.endswith(".pyc") or name == "deployment-report.json"}
    if Path(directory).resolve() == SOURCE:
        ignored.update(name for name in names if name == "Microsoft")
    return ignored


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy the portable Codex montage suite.")
    default_root = Path(os.environ.get("USERPROFILE") or Path.home()) / "CodexMontageFF" / VERSION
    parser.add_argument("-InstallRoot", "--InstallRoot", type=Path, default=default_root)
    parser.add_argument("-PreflightOnly", "--PreflightOnly", action="store_true")
    args = parser.parse_args(sys.argv[2:])

    python = SOURCE / "dependencies/python/python.exe"
    if sys.version_info[:3] != (3, 13, 15) or sys.maxsize <= 2**32 or Path(sys.executable).resolve() != python.resolve():
        raise RuntimeError("The bundled 64-bit Python 3.13.15 is required.")

    dependency_paths = {
        "python": python,
        "ffmpeg": SOURCE / "dependencies/ffmpeg/bin/ffmpeg.exe",
        "ffprobe": SOURCE / "dependencies/ffmpeg/bin/ffprobe.exe",
        "model": SOURCE / "dependencies" / MODEL,
    }
    semantic = SOURCE / "components/semantic-analysis-training-backup-v20"
    required = [
        *dependency_paths.values(),
        semantic / "scripts/portable_frame_renderer.py",
        semantic / "scripts/frame_range_repair.py",
        semantic / "scripts/v20_frame_plan_gate.py",
        semantic / "scripts/winky_ledger.py",
        SOURCE / "runtime-lock.json",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(f"Portable files are missing: {missing}")

    run_checked("Dependency and component hashes", [str(python), "-X", "utf8", str(SOURCE / "tools/build_ff_suite.py"), "verify"])
    run_checked("Three-part suite", [str(python), "-X", "utf8", str(SOURCE / "tools/verify_suite.py")])
    import faster_whisper, onnxruntime, cv2, PIL, yaml  # noqa: F401

    if args.PreflightOnly:
        print(json.dumps({
            "schema": "v20-ff-deployment-preflight/v1",
            "version": VERSION,
            "decision": "pass",
            **{name: str(path) for name, path in dependency_paths.items()},
        }, ensure_ascii=False, indent=2))
        return

    target = args.InstallRoot.resolve()
    if len(str(target)) > 90:
        raise RuntimeError(f"InstallRoot is too long for PyAV DLL loading: {target}")
    if os.path.lexists(target):
        raise RuntimeError(f"InstallRoot already exists: {target}")
    if target.is_relative_to(SOURCE) and not target.is_relative_to(SOURCE / "artifacts"):
        raise RuntimeError("InstallRoot inside the source tree must be under artifacts/.")

    shutil.copytree(SOURCE, target, ignore=copy_filter)
    installed_python = target / "dependencies/python/python.exe"
    run_checked("Installed registry rebuild", [str(installed_python), "-X", "utf8", str(target / "tools/build_ff_suite.py"), "build"])
    run_checked("Release validation", [str(installed_python), "-X", "utf8", str(target / "tools/validate_v20_release.py")], show_output=True)

    user_profile = Path(os.environ.get("USERPROFILE") or Path.home()).resolve()
    codex_home = Path(os.environ.get("CODEX_HOME") or user_profile / ".codex").resolve()
    skill_root = codex_home / "skills"
    official_root = user_profile / ".agents/skills"
    backups = codex_home / "skill-backups"
    installed_skills = []
    for name in SKILLS:
        component = target / "components" / name
        for root, prefix in ((skill_root, name), (official_root, f"official-{name}")):
            link = root / name
            create_junction(link, component, backups, prefix)
            installed_skills.append(str(link))

    write_json(codex_home / "three-part-suite-ff/active.json", {
        "schema": "three-part-suite-ff-active/v1",
        "suite_root": str(target),
        "version": VERSION,
        "render_mode": "source_frame_ranges/v1",
        "adjacent_same_source": "coalesce_monotonic_touching_or_overlapping_frame_ranges",
        "seconds_only_fallback": False,
    })
    installed_semantic = target / "components/semantic-analysis-training-backup-v20"
    report = {
        "schema": "v20-ff-deployment-report/v1",
        "version": VERSION,
        "installed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "install_root": str(target),
        "python": str(installed_python),
        "bundled_ffmpeg": str(target / "dependencies/ffmpeg/bin/ffmpeg.exe"),
        "bundled_ffprobe": str(target / "dependencies/ffmpeg/bin/ffprobe.exe"),
        "bundled_model": str(target / "dependencies" / MODEL),
        "portable_renderer": str(installed_semantic / "scripts/portable_frame_renderer.py"),
        "frame_range_repair": str(installed_semantic / "scripts/frame_range_repair.py"),
        "boundary_signal_scan": str(installed_semantic / "scripts/v20_boundary_signal_scan.py"),
        "opening_visual_family_gate": str(installed_semantic / "scripts/v20_opening_visual_family_gate.py"),
        "winky_ledger": str(installed_semantic / "scripts/winky_ledger.py"),
        "legacy_skill_root": str(skill_root),
        "official_skill_root": str(official_root),
        "installed_skill_paths": installed_skills,
        "render_mode": "source_frame_ranges/v1",
        "adjacent_same_source": "coalesce_monotonic_touching_or_overlapping_frame_ranges",
        "seconds_only_fallback": False,
        "preflight_passed": True,
        "restart_codex_required": True,
    }
    write_json(target / "artifacts/deployment-report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("Deployment complete. Restart Codex to load the skills.")


SOURCE = Path(sys.argv[1]).resolve().parent
try:
    main()
except Exception:
    traceback.print_exc()
    raise SystemExit(1)
