from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
SIMPLE_FST = FIXTURES / "simple.fst"
SIMPLE_VCD = FIXTURES / "simple.vcd"


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            shutil.which("python") or "python",
            str(ROOT / "scripts" / "hw_debug_cli.py"),
            *args,
        ],
        capture_output=True,
        text=True,
    )


class DirectFstQueryTests(unittest.TestCase):
    def test_build_wave_meta_command_creates_metadata_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir) / "wave_meta"
            proc = _run_cli(
                "build-wave-meta",
                "--waveform",
                str(SIMPLE_FST),
                "--out-dir",
                str(out_dir),
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue((out_dir / "manifest.json").exists())

    def test_query_signal_value_can_read_fst_directly_without_manifest(self) -> None:
        proc = _run_cli(
            "query-signal-value",
            "--waveform",
            str(SIMPLE_FST),
            "--signal",
            "TOP.clk",
            "--time",
            "5",
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["value_at_time"]["value"], "1")
        self.assertEqual(payload["signal"]["full_wave_path"], "TOP.clk")

    def test_query_packet_can_read_fst_directly_without_wave_db_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "packet.json"
            proc = _run_cli(
                "query-packet",
                "--waveform",
                str(SIMPLE_FST),
                "--focus-scope",
                "TOP",
                "--t-start",
                "0",
                "--t-end",
                "10",
                "--out",
                str(out_path),
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["query"]["focus_scope"], "TOP")
            self.assertEqual(payload["query"]["t_start"], 0)
            self.assertEqual(payload["query"]["t_end"], 10)
            self.assertEqual(payload["window_summary"]["active_signal_count"], 2)
            self.assertEqual(payload["window_summary"]["change_count"], 4)
            signals = {signal["full_wave_path"]: signal for signal in payload["focus_signals"]}
            self.assertEqual(sorted(signals), ["TOP.clk", "TOP.sig"])
            self.assertEqual([change["t"] for change in signals["TOP.clk"]["changes"]], [0, 5])
            self.assertEqual([change["t"] for change in signals["TOP.sig"]["changes"]], [0, 10])

    def test_query_signal_value_can_read_vcd_directly_without_manifest(self) -> None:
        proc = _run_cli(
            "query-signal-value",
            "--waveform",
            str(SIMPLE_VCD),
            "--signal",
            "TOP.clk",
            "--time",
            "5",
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["value_at_time"]["value"], "1")
        self.assertEqual(payload["signal"]["full_wave_path"], "TOP.clk")

    def test_query_packet_can_read_vcd_directly_without_wave_db_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "packet.json"
            proc = _run_cli(
                "query-packet",
                "--waveform",
                str(SIMPLE_VCD),
                "--focus-scope",
                "TOP",
                "--t-start",
                "0",
                "--t-end",
                "10",
                "--out",
                str(out_path),
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["query"]["focus_scope"], "TOP")
            self.assertEqual(payload["query"]["t_start"], 0)
            self.assertEqual(payload["query"]["t_end"], 10)
            self.assertEqual(payload["window_summary"]["active_signal_count"], 2)
            self.assertEqual(payload["window_summary"]["change_count"], 4)
            signals = {signal["full_wave_path"]: signal for signal in payload["focus_signals"]}
            self.assertEqual(sorted(signals), ["TOP.clk", "TOP.sig"])
            self.assertEqual([change["t"] for change in signals["TOP.clk"]["changes"]], [0, 5])
            self.assertEqual([change["t"] for change in signals["TOP.sig"]["changes"]], [0, 10])


if __name__ == "__main__":
    unittest.main()
