"""Unit tests for maestro.learning.recorder."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from maestro.learning.recorder import RunRecord, RunRecorder


def _make_record(
    *,
    issue: str = "NOV-1",
    turn: int = 1,
    success: bool = True,
    error: str | None = None,
    duration_ms: int = 5000,
    tools: list[str] | None = None,
    output: str = "",
) -> RunRecord:
    return RunRecord(
        issue_identifier=issue,
        timestamp_utc="2026-01-01T00:00:00+00:00",
        turn=turn,
        attempt=None,
        success=success,
        error=error,
        duration_ms=duration_ms,
        tools_used=tools or [],
        output_summary=output,
    )


class TestRecordAndLoadRoundTrip:
    def test_single_record(self, tmp_path: Path) -> None:
        recorder = RunRecorder(tmp_path)
        rec = _make_record()
        recorder.record(rec)

        loaded = recorder.load_recent()
        assert len(loaded) == 1
        assert loaded[0] == rec

    def test_multiple_records_preserve_order(self, tmp_path: Path) -> None:
        recorder = RunRecorder(tmp_path)
        for i in range(5):
            recorder.record(_make_record(turn=i + 1))

        loaded = recorder.load_recent()
        assert len(loaded) == 5
        assert [r.turn for r in loaded] == [1, 2, 3, 4, 5]

    def test_load_recent_respects_limit(self, tmp_path: Path) -> None:
        recorder = RunRecorder(tmp_path)
        for i in range(10):
            recorder.record(_make_record(turn=i + 1))

        loaded = recorder.load_recent(limit=3)
        assert len(loaded) == 3
        assert [r.turn for r in loaded] == [8, 9, 10]

    def test_fields_survive_serialisation(self, tmp_path: Path) -> None:
        recorder = RunRecorder(tmp_path)
        rec = RunRecord(
            issue_identifier="NOV-99",
            timestamp_utc="2026-03-12T10:00:00+00:00",
            turn=2,
            attempt=3,
            success=False,
            error="stall_timeout",
            duration_ms=42000,
            tools_used=["readToolCall", "writeToolCall"],
            output_summary="Something went wrong here",
        )
        recorder.record(rec)
        loaded = recorder.load_recent()
        assert loaded[0] == rec


class TestBuildLearningContext:
    def test_empty_history_returns_empty_string(self, tmp_path: Path) -> None:
        recorder = RunRecorder(tmp_path)
        assert recorder.build_learning_context() == ""

    def test_all_successes(self, tmp_path: Path) -> None:
        recorder = RunRecorder(tmp_path)
        for _ in range(5):
            recorder.record(_make_record(success=True))

        ctx = recorder.build_learning_context()
        assert "100% success rate" in ctx
        assert "failure" not in ctx.lower() or "0 failed" in ctx

    def test_with_failures_groups_error_patterns(self, tmp_path: Path) -> None:
        recorder = RunRecorder(tmp_path)
        recorder.record(_make_record(success=True))
        recorder.record(
            _make_record(success=False, error="stall_timeout"),
        )
        recorder.record(
            _make_record(success=False, error="stall_timeout"),
        )
        recorder.record(
            _make_record(success=False, error="turn_timeout"),
        )

        ctx = recorder.build_learning_context()
        assert "stall_timeout" in ctx
        assert "2 occurrence" in ctx
        assert "turn_timeout" in ctx

    def test_tools_listed(self, tmp_path: Path) -> None:
        recorder = RunRecorder(tmp_path)
        recorder.record(_make_record(tools=["readToolCall", "writeToolCall"]))
        recorder.record(_make_record(tools=["readToolCall"]))

        ctx = recorder.build_learning_context()
        assert "readToolCall" in ctx

    def test_last_failure_excerpt_included(self, tmp_path: Path) -> None:
        recorder = RunRecorder(tmp_path)
        recorder.record(
            _make_record(success=False, error="process_exit(1)", output="Error: module not found"),
        )

        ctx = recorder.build_learning_context()
        assert "module not found" in ctx


class TestStoreDirCreation:
    def test_creates_store_dir_if_missing(self, tmp_path: Path) -> None:
        deep = tmp_path / "a" / "b" / "c"
        assert not deep.exists()

        recorder = RunRecorder(deep)
        recorder.record(_make_record())

        assert deep.exists()
        assert (deep / "run_history.jsonl").exists()


class TestConcurrentWrites:
    def test_concurrent_writes_no_corruption(self, tmp_path: Path) -> None:
        recorder = RunRecorder(tmp_path)
        n_threads = 8
        n_records = 20
        barrier = threading.Barrier(n_threads)

        def _writer(thread_id: int) -> None:
            barrier.wait()
            for i in range(n_records):
                recorder.record(
                    _make_record(issue=f"T-{thread_id}", turn=i + 1),
                )

        threads = [
            threading.Thread(target=_writer, args=(tid,))
            for tid in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        history_path = tmp_path / "run_history.jsonl"
        lines = history_path.read_text().strip().split("\n")
        assert len(lines) == n_threads * n_records

        for line in lines:
            obj = json.loads(line)
            assert "issue_identifier" in obj
            assert "success" in obj


class TestMalformedLines:
    def test_skips_malformed_json(self, tmp_path: Path) -> None:
        history_path = tmp_path / "run_history.jsonl"
        good = json.dumps({
            "issue_identifier": "NOV-1",
            "timestamp_utc": "2026-01-01T00:00:00+00:00",
            "turn": 1,
            "attempt": None,
            "success": True,
            "error": None,
            "duration_ms": 1000,
            "tools_used": [],
            "output_summary": "",
        })
        history_path.write_text(f"NOT VALID JSON\n{good}\n")

        recorder = RunRecorder(tmp_path)
        loaded = recorder.load_recent()
        assert len(loaded) == 1
        assert loaded[0].issue_identifier == "NOV-1"
