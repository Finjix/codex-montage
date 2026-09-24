#!/usr/bin/env python3
"""Copy only release-authorized outputs into a new or matching delivery directory."""

from __future__ import annotations

import argparse
import importlib.util
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("v9_gate_runtime", ROOT / "v9_gate_runtime.py")
gate = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(gate)


def safe_name(value: str) -> str:
    result = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value)
    if not result:
        raise ValueError("empty plan id")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-authorization", type=Path, required=True)
    parser.add_argument("--delivery-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    authorization = gate.load_json(args.release_authorization)
    if authorization.get("schema") != "semantic-release-authorization/v9" or authorization.get("decision") != "pass" or authorization.get("failures"):
        raise ValueError("release authorization is not passing")
    rows = authorization.get("authorized_outputs", [])
    if not rows:
        raise ValueError("no authorized outputs")
    args.delivery_dir.mkdir(parents=True, exist_ok=True)
    delivered = []
    for row in rows:
        source = Path(row["export_path"])
        if not source.is_file() or gate.sha_file(source) != row["export_sha256"]:
            raise ValueError(f"authorized export hash mismatch: {row.get('plan_id')}")
        target = args.delivery_dir / f"{safe_name(row['plan_id'])}{source.suffix.lower()}"
        if target.exists():
            if gate.sha_file(target) != row["export_sha256"]:
                raise FileExistsError(f"delivery collision: {target}")
        else:
            partial = target.with_name(target.name + ".partial")
            if partial.exists():
                partial.unlink()
            shutil.copy2(source, partial)
            if gate.sha_file(partial) != row["export_sha256"]:
                partial.unlink(missing_ok=True)
                raise ValueError(f"delivery copy hash mismatch: {target}")
            partial.replace(target)
        delivered.append({"plan_id": row["plan_id"], "path": str(target.resolve()), "sha256": gate.sha_file(target)})
    manifest = {"schema": "semantic-delivery-manifest/v20", "package_id": gate.PACKAGE_ID, "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"), "release_authorization_path": str(args.release_authorization.resolve()), "release_authorization_sha256": gate.sha_file(args.release_authorization), "delivery_dir": str(args.delivery_dir.resolve()), "count": len(delivered), "outputs": delivered}
    gate.atomic_json(args.manifest, manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
