from __future__ import annotations

import importlib.util
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
EXECUTOR = ROOT / "components/montage-three-part-orchestrator-ff/scripts/three_suite_ff.py"


def load_executor():
    spec = importlib.util.spec_from_file_location("three_suite_ff_test", EXECUTOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_deployer():
    source = (ROOT / "start.cmd").read_text(encoding="utf-8-sig").split("# BEGIN PYTHON DEPLOY\n", 1)[1]
    source = source.rsplit("\nSOURCE =", 1)[0]
    module = types.ModuleType("start_deploy_test")
    exec(compile(source, str(ROOT / "start.cmd"), "exec"), module.__dict__)
    return module


class ExecutorIntegrityTests(unittest.TestCase):
    def test_reference_requires_same_path_and_current_hash(self):
        executor = load_executor()
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temporary:
            directory = Path(temporary)
            first, second = directory / "first.json", directory / "second.json"
            first.write_text("{}", encoding="utf-8")
            second.write_text("{}", encoding="utf-8")
            reference = {"path": str(first), "sha256": executor.sha(first)}
            self.assertEqual(first, executor.require_argument(first, reference, "delivery"))
            with self.assertRaisesRegex(RuntimeError, "previous stage"):
                executor.require_argument(second, reference, "delivery")
            first.write_text('{"changed":true}', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "changed"):
                executor.require_reference(reference, "delivery")

    def test_full_suite_verifier_failure_is_fatal(self):
        executor = load_executor()
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temporary:
            suite = Path(temporary)
            (suite / "tools").mkdir()
            (suite / "tools/build_ff_suite.py").write_text('print("{\\"ok\\": false}")', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "suite hash verification failed"):
                executor.verify(suite)


class DeploymentRollbackTests(unittest.TestCase):
    def test_activation_failure_restores_all_skill_links_and_active_config(self):
        deployer = load_deployer()
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temporary:
            base = Path(temporary)
            target = base / "new"
            legacy = base / "codex/skills"
            official = base / "profile/.agents/skills"
            for name in deployer.SKILLS:
                (target / "components" / name).mkdir(parents=True)
                for skill_root in (legacy, official):
                    old = skill_root / name
                    old.mkdir(parents=True)
                    (old / "old.txt").write_text("previous", encoding="utf-8")
            active_path = base / "codex/three-part-suite-ff/active.json"
            active_path.parent.mkdir(parents=True)
            active_path.write_text('{"suite_root":"old"}\n', encoding="utf-8")
            old_active = active_path.read_bytes()
            report_path = target / "artifacts/deployment-report.json"
            original_write = deployer.write_json

            def fail_activation(path, value):
                if path == active_path:
                    raise OSError("injected activation failure")
                return original_write(path, value)

            with patch.dict(deployer.__dict__, {"write_json": fail_activation}):
                with self.assertRaisesRegex(OSError, "injected activation failure"):
                    deployer.register_skills(target, legacy, official, base / "backups",
                                             active_path, {"suite_root": str(target)}, report_path, {})
            self.assertEqual(old_active, active_path.read_bytes())
            self.assertFalse(report_path.exists())
            for name in deployer.SKILLS:
                for skill_root in (legacy, official):
                    link = skill_root / name
                    self.assertFalse(link.is_junction())
                    self.assertEqual("previous", (link / "old.txt").read_text(encoding="utf-8"))
                    self.assertEqual([], list(skill_root.glob(f".{name}.pending-*")))


if __name__ == "__main__":
    unittest.main()
