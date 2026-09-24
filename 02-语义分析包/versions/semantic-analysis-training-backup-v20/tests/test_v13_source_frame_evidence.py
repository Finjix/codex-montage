from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("v13_source_frame_evidence", ROOT / "scripts" / "v13_source_frame_evidence.py")
frames = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(frames)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class SourceFrameEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.mp4"
        self.source.write_bytes(b"native-source")
        self.asr = self.root / "asr-s1.json"
        source_sha = frames.sha_file(self.source)
        write_json(self.asr, {"schema": "semantic-source-asr/v9", "source_id": "s1", "source_path": str(self.source), "source_sha256": source_sha, "asr": {"segments": [{"start": 1.0, "end": 3.0, "text": "完整口播"}]}})
        self.asr_index = self.root / "source_asr_index.json"
        write_json(self.asr_index, {"schema": "semantic-source-asr-index/v9", "results": [{"source_id": "s1", "status": "completed", "path": str(self.asr), "sha256": frames.sha_file(self.asr)}]})
        self.proposals = self.root / "candidate_proposals.json"
        self.proposal_rows = [
            {"candidate_id": "p1", "source_id": "s1", "source_path": str(self.source), "source_sha256": source_sha, "source_in": 1.0, "source_out": 3.0},
            {"candidate_id": "p2", "source_id": "s1", "source_path": str(self.source), "source_sha256": source_sha, "source_in": 4.0, "source_out": 6.0},
        ]
        write_json(self.proposals, {"schema": frames.PROPOSAL_SCHEMA, "proposals": self.proposal_rows})
        self.evidence_dir = self.root / "evidence"
        self.index = self.root / "frame_index.json"
        self.progress = self.root / "frame_progress.json"

    def tearDown(self):
        self.temp.cleanup()

    def fake_decode(self, _ffmpeg, _source, requested, output, _tolerance):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(f"frame@{requested:.3f}".encode())
        return {"requested_time": round(requested, 6), "decoded_pts_time": requested, "path": str(output.resolve()), "sha256": frames.sha_file(output), "extractor": "ffmpeg_accurate_decode"}

    def invoke(self, limit=1):
        return frames.main(["--source-asr-index", str(self.asr_index), "--proposal-index", str(self.proposals), "--output-dir", str(self.evidence_dir), "--index", str(self.index), "--progress", str(self.progress), "--max-items-per-run", str(limit)])

    def test_resumable_evidence_requires_ffmpeg_records_before_index_passes(self):
        with mock.patch.object(frames, "decode_frame", side_effect=self.fake_decode):
            self.assertEqual(75, self.invoke(limit=1))
            progress = frames.load_json(self.progress)
            self.assertEqual(1, progress["completed"])
            self.assertFalse(self.index.exists())
            self.assertEqual(0, self.invoke(limit=1))
        index = frames.load_json(self.index)
        self.assertEqual(frames.INDEX_SCHEMA, index["schema"])
        self.assertEqual(2, len(index["results"]))
        first = frames.load_json(self.evidence_dir / "p1" / "frame_evidence.json")
        self.assertFalse(first["wpf_evidence_accepted"])
        self.assertEqual("ffmpeg_accurate_decode", first["extractor"])
        self.assertEqual({"in", "mid", "out"}, {row["position"] for row in first["frames"]})

    def test_decode_uses_decoded_pts_and_accurate_filter_path(self):
        output = self.root / "frame.jpg"

        def fake_run(command, **_kwargs):
            output.write_bytes(b"frame")
            self.assertLess(command.index("-i"), command.index("-vf"))
            self.assertIn("showinfo", command[command.index("-vf") + 1])
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="[Parsed_showinfo] pts_time:1.125")

        with mock.patch.object(frames.subprocess, "run", side_effect=fake_run):
            result = frames.decode_frame("ffmpeg", str(self.source), 1.1, output, 0.25)
        self.assertEqual(1.125, result["decoded_pts_time"])
        self.assertEqual(hashlib.sha256(b"frame").hexdigest(), result["sha256"])

    def test_proposal_without_completed_asr_reference_is_rejected(self):
        write_json(self.asr_index, {"schema": "semantic-source-asr-index/v9", "results": []})
        with self.assertRaisesRegex(ValueError, "PROPOSAL_SOURCE_NOT_IN_COMPLETED_ASR_INDEX"):
            self.invoke()


if __name__ == "__main__":
    unittest.main()
