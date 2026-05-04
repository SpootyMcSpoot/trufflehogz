"""
Contract tests pinning detailed-mode + errors-table branches of write_markdown_summary,
and diagnose_findings_dir behavior.

These pin currently-uncovered behavior in trufflehog_scanner.py:
    write_markdown_summary lines 1399-1462 (include_detailed verified/unverified,
                                            errors table)
    diagnose_findings_dir lines 1797-1818
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from trufflehog_scanner import (
    diagnose_findings_dir,
    write_markdown_summary,
)


def _write_ndjson(rows):
    fd, path = tempfile.mkstemp(suffix=".ndjson")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return path


def _write_errors_ndjson(rows):
    fd, path = tempfile.mkstemp(suffix=".errors.ndjson")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return path


def _summary_tmp():
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False)
    f.close()
    return f.name


def _verified_row(repo, det, fpath, line=1, sha="deadbeef"):
    return {
        "DetectorName": det,
        "Verified": True,
        "SourceMetadata": {
            "Data": {
                "Git": {
                    "repository": repo,
                    "file": fpath,
                    "line": line,
                    "commit": sha,
                }
            }
        },
    }


def _unverified_row(repo, det, fpath, line=1, sha="deadbeef"):
    r = _verified_row(repo, det, fpath, line, sha)
    r["Verified"] = False
    return r


class TestWriteMarkdownSummaryDetailedVerified:
    def test_detailed_section_heading_emitted(self):
        nd = _write_ndjson([_verified_row("org/r1", "AWS", "a.py", 10)])
        out = _summary_tmp()
        try:
            with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": out}):
                write_markdown_summary(nd, "test-org", [], include_detailed=True)
            content = Path(out).read_text()
            assert "### Detailed verified findings (issues created)" in content
        finally:
            os.unlink(nd)
            os.unlink(out)

    def test_verified_table_header_present(self):
        nd = _write_ndjson([_verified_row("org/r1", "AWS", "a.py", 10)])
        out = _summary_tmp()
        try:
            with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": out}):
                write_markdown_summary(nd, "test-org", [], include_detailed=True)
            content = Path(out).read_text()
            assert "| Severity | Detector | File | Line | View | Blame |" in content
        finally:
            os.unlink(nd)
            os.unlink(out)

    def test_verified_repo_subheading_with_backticks(self):
        nd = _write_ndjson([_verified_row("org/myrepo", "AWS", "a.py", 10)])
        out = _summary_tmp()
        try:
            with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": out}):
                write_markdown_summary(nd, "test-org", [], include_detailed=True)
            content = Path(out).read_text()
            assert "#### `org/myrepo`" in content
        finally:
            os.unlink(nd)
            os.unlink(out)

    def test_verified_row_includes_severity_uppercased(self):
        nd = _write_ndjson([_verified_row("org/r1", "AWS", "a.py", 10)])
        out = _summary_tmp()
        try:
            with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": out}):
                write_markdown_summary(nd, "test-org", [], include_detailed=True)
            content = Path(out).read_text()
            # AWS is sec:critical -> displayed as CRITICAL
            assert "| CRITICAL | AWS |" in content
        finally:
            os.unlink(nd)
            os.unlink(out)

    def test_verified_row_emits_view_and_blame_links(self):
        nd = _write_ndjson([_verified_row("org/r1", "AWS", "src/a.py", 42)])
        out = _summary_tmp()
        try:
            with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": out}):
                write_markdown_summary(nd, "test-org", [], include_detailed=True)
            content = Path(out).read_text()
            assert "[View](" in content
            assert "[Blame](" in content
            assert "src/a.py" in content
        finally:
            os.unlink(nd)
            os.unlink(out)

    def test_per_repo_cap_truncates_rows(self):
        rows = [
            _verified_row("org/r1", "AWS", f"f{i}.py", line=i)
            for i in range(1, 11)
        ]
        nd = _write_ndjson(rows)
        out = _summary_tmp()
        try:
            with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": out}):
                write_markdown_summary(nd, "test-org", [],
                                       include_detailed=True,
                                       detailed_per_repo=2)
            content = Path(out).read_text()
            # Only files f1 and f2 should appear, f3..f10 truncated
            assert "f1.py" in content
            assert "f2.py" in content
            assert "f3.py" not in content
        finally:
            os.unlink(nd)
            os.unlink(out)

    def test_max_total_truncates_with_omitted_message(self):
        rows = [
            _verified_row(f"org/r{i}", "AWS", "a.py", line=1) for i in range(5)
        ]
        nd = _write_ndjson(rows)
        out = _summary_tmp()
        try:
            with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": out}):
                write_markdown_summary(nd, "test-org", [],
                                       include_detailed=True,
                                       detailed_per_repo=10,
                                       detailed_max_total=2)
            content = Path(out).read_text()
            assert "more verified finding(s) omitted" in content
        finally:
            os.unlink(nd)
            os.unlink(out)


class TestWriteMarkdownSummaryDetailedUnverified:
    def test_unverified_section_emits_collapsed_details(self):
        nd = _write_ndjson([_unverified_row("org/r1", "AWS", "a.py", 10)])
        out = _summary_tmp()
        try:
            with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": out}):
                write_markdown_summary(nd, "test-org", [], include_detailed=True)
            content = Path(out).read_text()
            assert "### Unverified findings (informational" in content
            assert "<details>" in content
            assert "<summary>Expand to view unverified findings</summary>" in content
            assert "</details>" in content
        finally:
            os.unlink(nd)
            os.unlink(out)

    def test_unverified_row_renders_repo_subheading(self):
        nd = _write_ndjson([_unverified_row("org/unver", "Slack", "s.py", 5)])
        out = _summary_tmp()
        try:
            with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": out}):
                write_markdown_summary(nd, "test-org", [], include_detailed=True)
            content = Path(out).read_text()
            assert "#### `org/unver`" in content
            assert "Slack" in content
        finally:
            os.unlink(nd)
            os.unlink(out)

    def test_unverified_per_repo_cap(self):
        rows = [
            _unverified_row("org/r1", "AWS", f"u{i}.py", line=i)
            for i in range(1, 6)
        ]
        nd = _write_ndjson(rows)
        out = _summary_tmp()
        try:
            with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": out}):
                write_markdown_summary(nd, "test-org", [],
                                       include_detailed=True,
                                       detailed_per_repo=2)
            content = Path(out).read_text()
            assert "u1.py" in content
            assert "u2.py" in content
            assert "u3.py" not in content
        finally:
            os.unlink(nd)
            os.unlink(out)

    def test_unverified_max_total_truncates_with_omitted_message(self):
        rows = [
            _unverified_row(f"org/r{i}", "AWS", "a.py", line=1)
            for i in range(5)
        ]
        nd = _write_ndjson(rows)
        out = _summary_tmp()
        try:
            with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": out}):
                write_markdown_summary(nd, "test-org", [],
                                       include_detailed=True,
                                       detailed_per_repo=10,
                                       detailed_max_total=2)
            content = Path(out).read_text()
            assert "more unverified finding(s) omitted" in content
        finally:
            os.unlink(nd)
            os.unlink(out)


class TestWriteMarkdownSummaryErrorsTable:
    def test_errors_table_emitted_when_errors_present(self):
        nd = _write_ndjson([_verified_row("org/r1", "AWS", "a.py", 1)])
        errs = _write_errors_ndjson([
            {"repo": "org/r1", "stage": "scan", "status": 500,
             "message": "boom"},
        ])
        out = _summary_tmp()
        try:
            with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": out}):
                write_markdown_summary(nd, "test-org", [], errors_path=errs)
            content = Path(out).read_text()
            assert "### API/Scan errors observed" in content
            assert "| Repo | Stage | Status | Message |" in content
            assert "`org/r1`" in content
            assert "scan" in content
            assert "500" in content
            assert "boom" in content
        finally:
            os.unlink(nd)
            os.unlink(errs)
            os.unlink(out)

    def test_errors_table_pipe_in_message_escaped(self):
        nd = _write_ndjson([_verified_row("org/r1", "AWS", "a.py", 1)])
        errs = _write_errors_ndjson([
            {"repo": "org/r1", "stage": "scan", "status": 500,
             "message": "broken|pipe"},
        ])
        out = _summary_tmp()
        try:
            with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": out}):
                write_markdown_summary(nd, "test-org", [], errors_path=errs)
            content = Path(out).read_text()
            assert "broken&#124;pipe" in content
            assert "broken|pipe" not in content
        finally:
            os.unlink(nd)
            os.unlink(errs)
            os.unlink(out)

    def test_errors_section_omitted_when_errors_path_none(self):
        nd = _write_ndjson([_verified_row("org/r1", "AWS", "a.py", 1)])
        out = _summary_tmp()
        try:
            with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": out}):
                write_markdown_summary(nd, "test-org", [], errors_path=None)
            content = Path(out).read_text()
            assert "### API/Scan errors observed" not in content
        finally:
            os.unlink(nd)
            os.unlink(out)

    def test_errors_section_omitted_when_errors_file_empty(self):
        nd = _write_ndjson([_verified_row("org/r1", "AWS", "a.py", 1)])
        errs = _write_errors_ndjson([])
        out = _summary_tmp()
        try:
            with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": out}):
                write_markdown_summary(nd, "test-org", [], errors_path=errs)
            content = Path(out).read_text()
            assert "### API/Scan errors observed" not in content
        finally:
            os.unlink(nd)
            os.unlink(errs)
            os.unlink(out)


class TestWriteMarkdownSummaryFallbackStdout:
    def test_writes_to_stdout_when_no_step_summary_env(self, capsys):
        nd = _write_ndjson([_verified_row("org/r1", "AWS", "a.py", 1)])
        try:
            env = {k: v for k, v in os.environ.items()
                   if k != "GITHUB_STEP_SUMMARY"}
            with mock.patch.dict(os.environ, env, clear=True):
                write_markdown_summary(nd, "test-org", [], include_detailed=True)
            captured = capsys.readouterr().out
            assert "Detailed verified findings" in captured
        finally:
            os.unlink(nd)


class TestDiagnoseFindingsDir:
    def test_missing_dir_returns_silently(self, caplog):
        with caplog.at_level("INFO"):
            diagnose_findings_dir("/nonexistent/path/xyz", "test-org",
                                  max_files=5, max_lines=5)
        assert any("diag dir not found" in r.message
                   for r in caplog.records)

    def test_empty_string_dir_logs_not_found(self, caplog):
        with caplog.at_level("INFO"):
            diagnose_findings_dir("", "test-org",
                                  max_files=5, max_lines=5)
        assert any("diag dir not found" in r.message
                   for r in caplog.records)

    def test_walks_ndjson_files_and_prints_path(self, tmp_path, capsys):
        f = tmp_path / "r1.ndjson"
        f.write_text(json.dumps(_verified_row("org/r1", "AWS", "a.py", 1)) + "\n")
        diagnose_findings_dir(str(tmp_path), "test-org",
                              max_files=5, max_lines=5)
        captured = capsys.readouterr().out
        assert "diag file:" in captured
        assert "r1.ndjson" in captured
        assert "bytes=" in captured

    def test_max_files_caps_traversal(self, tmp_path, capsys):
        for i in range(5):
            (tmp_path / f"r{i}.ndjson").write_text(
                json.dumps(_verified_row(f"org/r{i}", "AWS", "a.py", 1)) + "\n"
            )
        diagnose_findings_dir(str(tmp_path), "test-org",
                              max_files=2, max_lines=5)
        captured = capsys.readouterr().out
        diag_lines = [l for l in captured.splitlines() if "diag file:" in l]
        assert len(diag_lines) == 2

    def test_max_lines_caps_per_file(self, tmp_path, capsys):
        rows = [
            _verified_row("org/r1", "AWS", f"f{i}.py", line=i)
            for i in range(1, 11)
        ]
        f = tmp_path / "r1.ndjson"
        f.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        diagnose_findings_dir(str(tmp_path), "test-org",
                              max_files=5, max_lines=3)
        captured = capsys.readouterr().out
        # _print_one_sanitized writes pipe-delimited preview lines for each
        # finding emitted; cap=3 means only 3 of 10 should appear
        finding_lines = [l for l in captured.splitlines()
                         if "AWS" in l and "f" in l and ".py" in l]
        assert len(finding_lines) == 3

    def test_non_ndjson_files_skipped(self, tmp_path, capsys):
        (tmp_path / "noise.txt").write_text("not ndjson")
        f = tmp_path / "r1.ndjson"
        f.write_text(json.dumps(_verified_row("org/r1", "AWS", "a.py", 1)) + "\n")
        diagnose_findings_dir(str(tmp_path), "test-org",
                              max_files=5, max_lines=5)
        captured = capsys.readouterr().out
        assert "noise.txt" not in captured
        assert "r1.ndjson" in captured
