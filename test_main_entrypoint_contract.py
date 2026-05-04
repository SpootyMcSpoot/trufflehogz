"""
Contract tests for main() and _secure_entrypoint() in trufflehog_scanner.

Pins the workflow-orchestration shape used by the GHA self-scan:
  * --validate path returns 0 without calling get_auth_token
  * Diagnostics-only run (no summary, no ndjson) returns 0 quietly
  * Auth failure writes summary (when requested) then returns 0
  * No-findings path writes summary then returns 0
  * Happy path dispatches process_repo via ThreadPoolExecutor and writes
    summary with created/skipped/failed counts
  * Failed-repo path returns exit code 2
  * _secure_entrypoint maps KeyboardInterrupt -> 130, Exception -> 1

Heavy mocking: parse_args is patched to return a Namespace; downstream
functions (load_findings, process_repo, write_markdown_summary,
get_auth_token) are stubbed at the module level.
"""
from __future__ import annotations

import argparse
from unittest import mock

import pytest

import trufflehog_scanner as ts


def _ns(**overrides) -> argparse.Namespace:
    """Build a Namespace with parse_args defaults, allowing per-test override."""
    defaults = dict(
        ndjson="findings.ndjson",
        run_url="",
        scanner_repo="",
        max_workers=0,
        labels="",
        dry_run=False,
        title_prefix="[TruffleHog]",
        log_level="INFO",
        print_sanitized=0,
        write_summary=False,
        org="testorg",
        diag_dir="",
        diag_max_files=25,
        diag_max_lines=3,
        validate=False,
        exclude_patterns_file=None,
        summary_detailed=False,
        summary_detailed_per_repo=10,
        summary_detailed_max=300,
        errors_ndjson=None,
        include_unverified=False,
        target_repo=None,
        force_create=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


@pytest.fixture
def patched_setup_logging(monkeypatch):
    monkeypatch.setattr(ts, "setup_logging", lambda *a, **kw: None)


@pytest.fixture
def patched_load_excludes(monkeypatch):
    monkeypatch.setattr(ts, "load_exclude_regexes", lambda *a, **kw: [])


class TestValidateShortCircuits:
    def test_validate_returns_zero_without_auth(
        self, monkeypatch, patched_setup_logging, patched_load_excludes
    ):
        monkeypatch.setattr(ts, "parse_args", lambda: _ns(validate=True))
        monkeypatch.setattr(
            ts, "ndjson_stats", lambda *a, **kw: (5, 0, 1, 1)
        )
        # Pin: --validate must NOT touch auth path
        auth_spy = mock.MagicMock(side_effect=AssertionError("auth called"))
        monkeypatch.setattr(ts, "get_auth_token", auth_spy)

        rc = ts.main()
        assert rc == 0
        auth_spy.assert_not_called()

    def test_validate_swallows_stats_error(
        self, monkeypatch, patched_setup_logging, patched_load_excludes
    ):
        monkeypatch.setattr(ts, "parse_args", lambda: _ns(validate=True))
        monkeypatch.setattr(
            ts, "ndjson_stats",
            mock.MagicMock(side_effect=RuntimeError("boom")),
        )
        # Pin: bad stats must NOT crash main(); workflow degrades gracefully
        rc = ts.main()
        assert rc == 0


class TestDiagnosticsOnlyShortCircuit:
    def test_diag_dir_no_summary_no_ndjson_returns_zero(
        self, monkeypatch, tmp_path, patched_setup_logging, patched_load_excludes
    ):
        monkeypatch.setattr(
            ts, "parse_args",
            lambda: _ns(
                diag_dir=str(tmp_path / "diag"),
                write_summary=False,
                ndjson=str(tmp_path / "missing.ndjson"),
            ),
        )
        monkeypatch.setattr(ts, "diagnose_findings_dir", lambda *a, **kw: None)
        auth_spy = mock.MagicMock(side_effect=AssertionError("auth called"))
        monkeypatch.setattr(ts, "get_auth_token", auth_spy)

        rc = ts.main()
        assert rc == 0
        auth_spy.assert_not_called()

    def test_diag_dir_failure_logged_and_continues(
        self, monkeypatch, tmp_path, patched_setup_logging, patched_load_excludes, caplog
    ):
        monkeypatch.setattr(
            ts, "parse_args",
            lambda: _ns(
                diag_dir=str(tmp_path / "diag"),
                write_summary=False,
                ndjson=str(tmp_path / "missing.ndjson"),
            ),
        )
        monkeypatch.setattr(
            ts, "diagnose_findings_dir",
            mock.MagicMock(side_effect=RuntimeError("disk full")),
        )
        # Pin: diagnose failure must NOT crash main(); short-circuit branch wins
        rc = ts.main()
        assert rc == 0


class TestAuthFailureWritesSummary:
    def test_auth_value_error_writes_summary_then_returns_zero(
        self, monkeypatch, tmp_path, patched_setup_logging, patched_load_excludes
    ):
        ndjson = tmp_path / "findings.ndjson"
        ndjson.write_text("{}\n")
        monkeypatch.setattr(
            ts, "parse_args",
            lambda: _ns(ndjson=str(ndjson), write_summary=True),
        )
        monkeypatch.setattr(
            ts, "get_auth_token",
            mock.MagicMock(side_effect=ValueError("no token")),
        )
        summary = mock.MagicMock()
        monkeypatch.setattr(ts, "write_markdown_summary", summary)
        # Pin: process_repo MUST NOT run after auth failure
        proc = mock.MagicMock(side_effect=AssertionError("process called"))
        monkeypatch.setattr(ts, "process_repo", proc)

        rc = ts.main()
        assert rc == 0
        summary.assert_called_once()

    def test_auth_failure_summary_skipped_when_no_ndjson(
        self, monkeypatch, tmp_path, patched_setup_logging, patched_load_excludes
    ):
        monkeypatch.setattr(
            ts, "parse_args",
            lambda: _ns(ndjson=str(tmp_path / "missing.ndjson"), write_summary=True),
        )
        monkeypatch.setattr(
            ts, "get_auth_token",
            mock.MagicMock(side_effect=ValueError("no token")),
        )
        summary = mock.MagicMock()
        monkeypatch.setattr(ts, "write_markdown_summary", summary)

        rc = ts.main()
        assert rc == 0
        summary.assert_not_called()

    def test_auth_failure_summary_swallows_secondary_error(
        self, monkeypatch, tmp_path, patched_setup_logging, patched_load_excludes
    ):
        ndjson = tmp_path / "findings.ndjson"
        ndjson.write_text("{}\n")
        monkeypatch.setattr(
            ts, "parse_args",
            lambda: _ns(ndjson=str(ndjson), write_summary=True),
        )
        monkeypatch.setattr(
            ts, "get_auth_token",
            mock.MagicMock(side_effect=ValueError("no token")),
        )
        monkeypatch.setattr(
            ts, "write_markdown_summary",
            mock.MagicMock(side_effect=OSError("write failed")),
        )
        # Pin: secondary failure during summary must NOT poison the rc
        rc = ts.main()
        assert rc == 0


class TestNoFindingsWritesSummary:
    def test_no_findings_writes_summary_returns_zero(
        self, monkeypatch, tmp_path, patched_setup_logging, patched_load_excludes
    ):
        ndjson = tmp_path / "findings.ndjson"
        ndjson.write_text("")
        monkeypatch.setattr(
            ts, "parse_args",
            lambda: _ns(ndjson=str(ndjson), write_summary=True),
        )
        monkeypatch.setattr(ts, "get_auth_token", lambda: "tok")
        monkeypatch.setattr(ts, "load_findings", lambda *a, **kw: {})
        summary = mock.MagicMock()
        monkeypatch.setattr(ts, "write_markdown_summary", summary)

        rc = ts.main()
        assert rc == 0
        summary.assert_called_once()


class TestHappyPathProcessesRepos:
    def test_process_repo_dispatched_per_repo_and_summary_includes_counts(
        self, monkeypatch, tmp_path, patched_setup_logging, patched_load_excludes
    ):
        ndjson = tmp_path / "findings.ndjson"
        ndjson.write_text("{}\n")
        monkeypatch.setattr(
            ts, "parse_args",
            lambda: _ns(ndjson=str(ndjson), write_summary=True),
        )
        monkeypatch.setattr(ts, "get_auth_token", lambda: "tok")
        # Two repos: one created (True), one skipped (None)
        monkeypatch.setattr(
            ts, "load_findings",
            lambda *a, **kw: {"o/r1": [{"a": 1}], "o/r2": [{"b": 2}]},
        )
        results = {"o/r1": True, "o/r2": None}
        monkeypatch.setattr(
            ts, "process_repo",
            lambda repo, *a, **kw: (repo, results[repo]),
        )
        summary = mock.MagicMock()
        monkeypatch.setattr(ts, "write_markdown_summary", summary)

        rc = ts.main()
        assert rc == 0
        # Pin: created + skipped counts thread into summary call
        kwargs = summary.call_args.kwargs
        assert kwargs.get("created_count") == 1
        assert kwargs.get("skipped_count") == 1
        assert kwargs.get("failed_repos") == []

    def test_dry_run_passes_none_counts_to_summary(
        self, monkeypatch, tmp_path, patched_setup_logging, patched_load_excludes
    ):
        ndjson = tmp_path / "findings.ndjson"
        ndjson.write_text("{}\n")
        monkeypatch.setattr(
            ts, "parse_args",
            lambda: _ns(ndjson=str(ndjson), write_summary=True, dry_run=True),
        )
        monkeypatch.setattr(ts, "get_auth_token", lambda: "tok")
        monkeypatch.setattr(
            ts, "load_findings", lambda *a, **kw: {"o/r1": [{}]}
        )
        monkeypatch.setattr(
            ts, "process_repo", lambda repo, *a, **kw: (repo, True)
        )
        summary = mock.MagicMock()
        monkeypatch.setattr(ts, "write_markdown_summary", summary)

        rc = ts.main()
        assert rc == 0
        # Pin: dry-run paints counts as None so summary doesn't claim creation
        kwargs = summary.call_args.kwargs
        assert kwargs.get("created_count") is None
        assert kwargs.get("skipped_count") is None
        assert kwargs.get("failed_repos") is None


class TestFailedRepoReturnsTwo:
    def test_failed_repo_returns_exit_two(
        self, monkeypatch, tmp_path, patched_setup_logging, patched_load_excludes
    ):
        ndjson = tmp_path / "findings.ndjson"
        ndjson.write_text("{}\n")
        monkeypatch.setattr(
            ts, "parse_args",
            lambda: _ns(ndjson=str(ndjson), write_summary=False),
        )
        monkeypatch.setattr(ts, "get_auth_token", lambda: "tok")
        monkeypatch.setattr(
            ts, "load_findings", lambda *a, **kw: {"o/r1": [{}]}
        )
        # Non-True/None result -> failed
        monkeypatch.setattr(
            ts, "process_repo", lambda repo, *a, **kw: (repo, False)
        )

        rc = ts.main()
        # Pin: any failed repo trips workflow exit-2 so the GHA step fails
        assert rc == 2

    def test_worker_exception_counts_as_failure(
        self, monkeypatch, tmp_path, patched_setup_logging, patched_load_excludes
    ):
        ndjson = tmp_path / "findings.ndjson"
        ndjson.write_text("{}\n")
        monkeypatch.setattr(
            ts, "parse_args",
            lambda: _ns(ndjson=str(ndjson), write_summary=False),
        )
        monkeypatch.setattr(ts, "get_auth_token", lambda: "tok")
        monkeypatch.setattr(
            ts, "load_findings", lambda *a, **kw: {"o/r1": [{}]}
        )
        monkeypatch.setattr(
            ts, "process_repo",
            mock.MagicMock(side_effect=RuntimeError("worker crash")),
        )

        rc = ts.main()
        # Pin: ThreadPool exception in worker -> failed_repos -> rc=2
        assert rc == 2

    def test_dry_run_with_failure_returns_zero(
        self, monkeypatch, tmp_path, patched_setup_logging, patched_load_excludes
    ):
        ndjson = tmp_path / "findings.ndjson"
        ndjson.write_text("{}\n")
        monkeypatch.setattr(
            ts, "parse_args",
            lambda: _ns(ndjson=str(ndjson), write_summary=False, dry_run=True),
        )
        monkeypatch.setattr(ts, "get_auth_token", lambda: "tok")
        monkeypatch.setattr(
            ts, "load_findings", lambda *a, **kw: {"o/r1": [{}]}
        )
        monkeypatch.setattr(
            ts, "process_repo", lambda repo, *a, **kw: (repo, False)
        )

        rc = ts.main()
        # Pin: dry-run treats failures as informational, exits 0
        assert rc == 0


class TestTargetRepoConsolidation:
    def test_target_repo_collapses_findings_into_single_bucket(
        self, monkeypatch, tmp_path, patched_setup_logging, patched_load_excludes
    ):
        ndjson = tmp_path / "findings.ndjson"
        ndjson.write_text("{}\n")
        monkeypatch.setattr(
            ts, "parse_args",
            lambda: _ns(
                ndjson=str(ndjson),
                target_repo="org/consolidated",
                write_summary=False,
            ),
        )
        monkeypatch.setattr(ts, "get_auth_token", lambda: "tok")
        monkeypatch.setattr(
            ts, "load_findings",
            lambda *a, **kw: {"o/r1": [{"x": 1}], "o/r2": [{"y": 2}]},
        )
        captured = {}

        def fake_process(repo, items, *a, **kw):
            captured.setdefault("calls", []).append((repo, len(items)))
            return (repo, True)

        monkeypatch.setattr(ts, "process_repo", fake_process)

        rc = ts.main()
        assert rc == 0
        # Pin: target-repo consolidation = ONE process_repo call with merged items
        assert captured["calls"] == [("org/consolidated", 2)]


class TestSecureEntrypointExitCodes:
    def test_keyboard_interrupt_exits_130(self, monkeypatch):
        monkeypatch.setattr(
            ts, "main", mock.MagicMock(side_effect=KeyboardInterrupt())
        )
        with pytest.raises(SystemExit) as exc:
            ts._secure_entrypoint()
        # Pin: SIGINT -> 128+2 = 130 (POSIX convention)
        assert exc.value.code == 130

    def test_unhandled_exception_exits_one(self, monkeypatch):
        monkeypatch.setattr(
            ts, "main", mock.MagicMock(side_effect=RuntimeError("nope"))
        )
        with pytest.raises(SystemExit) as exc:
            ts._secure_entrypoint()
        # Pin: any other exception -> 1, never crash with traceback
        assert exc.value.code == 1

    def test_normal_return_passes_through_exit_code(self, monkeypatch):
        monkeypatch.setattr(ts, "main", mock.MagicMock(return_value=2))
        with pytest.raises(SystemExit) as exc:
            ts._secure_entrypoint()
        assert exc.value.code == 2
