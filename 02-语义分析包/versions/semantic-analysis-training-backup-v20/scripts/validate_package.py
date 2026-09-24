#!/usr/bin/env python3
"""Build and verify the V20.2.5 fail-closed frame-native package manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


PACKAGE_ID = "semantic-analysis-training-backup-v20"
VERSION = "20.2.5"
MANIFEST_NAME = "PACKAGE_MANIFEST.json"
IGNORED_PARTS = {"__pycache__", ".git"}
BASELINE_MANIFEST_SHA256 = "V20-LOCAL-BASELINE-REBUILT"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def is_member(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    return (
        path.is_file()
        and relative.name != MANIFEST_NAME
        and relative.suffix != ".pyc"
        and not any(part in IGNORED_PARTS for part in relative.parts)
    )


def actual_members(root: Path) -> list[dict[str, object]]:
    result = []
    for path in sorted((item for item in root.rglob("*") if is_member(item, root)), key=lambda item: item.as_posix()):
        result.append({
            "path": path.relative_to(root).as_posix(),
            "sha256": sha256(path),
            "bytes": path.stat().st_size,
        })
    return result


def build(root: Path) -> dict[str, object]:
    manifest = {
        "schema": "semantic-analysis-package-manifest/v1",
        "package_id": PACKAGE_ID,
        "version": VERSION,
        "created_at": "2026-09-14",
        "updated_at": "2026-09-22",
        "inherits": {
            "package_id": "semantic-analysis-training-backup-v19-2",
            "package_manifest_sha256": BASELINE_MANIFEST_SHA256,
        },
        "members": actual_members(root),
    }
    (root / MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def verify(root: Path) -> dict[str, object]:
    manifest = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
    failures: list[str] = []
    if manifest.get("package_id") != PACKAGE_ID:
        failures.append("package_id")
    if manifest.get("version") != VERSION:
        failures.append("version")
    expected = manifest.get("members")
    if not isinstance(expected, list):
        failures.append("members")
        expected = []
    expected_by_path = {entry.get("path"): entry for entry in expected if isinstance(entry, dict)}
    actual_by_path = {entry["path"]: entry for entry in actual_members(root)}
    if len(expected_by_path) != len(expected):
        failures.append("duplicate_member")
    if set(expected_by_path) != set(actual_by_path):
        failures.append("member_set")
    for path, actual in actual_by_path.items():
        listed = expected_by_path.get(path, {})
        if listed.get("sha256") != actual["sha256"] or listed.get("bytes") != actual["bytes"]:
            failures.append(f"hash:{path}")
    return {
        "ok": not failures,
        "package_id": manifest.get("package_id"),
        "version": manifest.get("version"),
        "member_count": len(actual_by_path),
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("build", "verify"))
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.mode == "build":
        result = build(root)
        print(json.dumps({"ok": True, "member_count": len(result["members"])}))
        return 0
    result = verify(root)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
