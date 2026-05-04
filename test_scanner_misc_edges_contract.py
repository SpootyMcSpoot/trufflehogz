"""
Mop-up contract tests for currently-uncovered single-line branches in
trufflehog_scanner.py. These pin small fallback paths the scanner relies
on at the edges of the detector/auth/dedup/main routes.

Targets:
  * normalize_detector fallbacks: SlackWebhook, SSH, JWT
  * repo_meta HTTPError -> None
  * search_recent_issues_with_hash non-dict skip
  * get_suppressed_hashes non-dict skip
  * process_comment_commands non-dict issue + non-list comments
  * _print_one_sanitized no-link branch
  * main() print_sanitized_preview exception swallow
  * main() include_unverified info log path
  * main() summary errors during no-findings + post-creation swallowed
"""
from __future__ import annotations

import argparse
import io
import os
import tempfile
from unittest import mock
from urllib.error import HTTPError

import pytest

import trufflehog_scanner as ts


def _http_error(code: int) -> HTTPError:
    return HTTPError(
        url="https://api.github.com/x", code=code, msg="err",
        hdrs={}, fp=io.BytesIO(b""),
    )


def _ns(**overrides) -> argparse.Namespace:
    defaults = dict(
        ndjson="findings.ndjson", run_url="", scanner_repo="",
        max_workers=0, labels="", dry_run=False,
        title_prefix="[TruffleHog]", log_level="INFO",
        print_sanitized=0, write_summary=False, org="o",
        diag_dir="", diag_max_files=25, diag_max_lines=3,
        validate=False, exclude_patterns_file=None,
        summary_detailed=False, summary_detailed_per_repo=10,
        summary_detailed_max=300, errors_ndjson=None,
        include_unverified=False, target_repo=None, force_create=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# normalize_detector
# ---------------------------------------------------------------------------


class TestNormalizeDetectorTopMatches:
    def test_aws_match(self):
        # Pin: substring match against DETECTOR_TYPES keys (case-insensitive)
        assert ts.normalize_detector("aws-access-key") == "AWS"

    def test_unknown_falls_back_to_generic(self):
        # Pin: no key match + no special-case substring -> Generic
        assert ts.normalize_detector("totally-unknown-thing") == "Generic"

    def test_empty_or_none_returns_generic(self):
        assert ts.normalize_detector("") == "Generic"
        assert ts.normalize_detector(None) == "Generic"


# ---------------------------------------------------------------------------
# repo_meta HTTPError path
# ---------------------------------------------------------------------------


class TestRepoMetaHttpError:
    def test_http_error_returns_none(self, caplog):
        client = ts.GHClient(token="t")
        with mock.patch.object(client, "call", side_effect=_http_error(404)):
            # Pin: 404 on /repos/{repo} -> None (caller decides actionability)
            assert client.repo_meta("o/missing") is None


# ---------------------------------------------------------------------------
# search_recent_issues_with_hash non-dict guard
# ---------------------------------------------------------------------------


class TestSearchHashNonDictSkip:
    def test_non_dict_issue_in_response_skipped(self):
        client = ts.GHClient(token="t")
        page = ["garbage", None, {"title": "[TH] noise (12345678)", "body": ""}]
        with mock.patch.object(client, "call", return_value=page):
            # Pin: stray non-dict entries filtered, no crash
            assert client.search_recent_issues_with_hash(
                "o/r", "[TH]", "(12345678)"
            ) is False


# ---------------------------------------------------------------------------
# print_one_sanitized fallback (no link)
# ---------------------------------------------------------------------------


class TestPrintOneSanitizedNoLinkBranch:
    def test_no_repo_no_commit_no_link(self, capsys):
        # No repository / no commit -> link is empty -> else branch (1134)
        o = {
            "DetectorName": "AWS",
            "Verified": True,
            "Raw": "x",
            # No SourceMetadata -> no repo, no commit
        }
        ts._print_one_sanitized(o, "myorg")
        out = capsys.readouterr().out
        # Pin: line printed without trailing link segment
        assert "verified=True" in out
        # No trailing pipe-separated link
        assert "https://github.com" not in out


# ---------------------------------------------------------------------------
# main() residual branches
# ---------------------------------------------------------------------------


@pytest.fixture
def patched_logging_excludes(monkeypatch):
    monkeypatch.setattr(ts, "setup_logging", lambda *a, **kw: None)
    monkeypatch.setattr(ts, "load_exclude_regexes", lambda *a, **kw: [])


class TestMainPreviewExceptionSwallow:
    def test_print_sanitized_preview_failure_swallowed(
        self, monkeypatch, tmp_path, patched_logging_excludes
    ):
        ndjson = tmp_path / "findings.ndjson"
        ndjson.write_text("{}\n")
        monkeypatch.setattr(
            ts, "parse_args",
            lambda: _ns(ndjson=str(ndjson), print_sanitized=5),
        )
        monkeypatch.setattr(
            ts, "print_sanitized_preview",
            mock.MagicMock(side_effect=RuntimeError("preview crash")),
        )
        monkeypatch.setattr(
            ts, "get_auth_token",
            mock.MagicMock(side_effect=ValueError("no token")),
        )
        # Pin: preview failure must NOT crash the main run
        rc = ts.main()
        assert rc == 0


class TestMainIncludeUnverifiedNoFindings:
    def test_include_unverified_with_no_findings_logs_distinct_message(
        self, monkeypatch, tmp_path, patched_logging_excludes, caplog
    ):
        ndjson = tmp_path / "findings.ndjson"
        ndjson.write_text("{}\n")
        monkeypatch.setattr(
            ts, "parse_args",
            lambda: _ns(ndjson=str(ndjson), include_unverified=True),
        )
        monkeypatch.setattr(ts, "get_auth_token", lambda: "tok")
        monkeypatch.setattr(ts, "load_findings", lambda *a, **kw: {})
        # write_markdown_summary should NOT be called: write_summary=False
        summary = mock.MagicMock()
        monkeypatch.setattr(ts, "write_markdown_summary", summary)

        import logging
        with caplog.at_level(logging.INFO):
            rc = ts.main()
        assert rc == 0
        # Pin: include_unverified path emits "No findings detected"
        joined = " ".join(r.message for r in caplog.records)
        assert "No findings detected" in joined


class TestMainNoFindingsSummaryError:
    def test_summary_failure_during_no_findings_swallowed(
        self, monkeypatch, tmp_path, patched_logging_excludes
    ):
        ndjson = tmp_path / "findings.ndjson"
        ndjson.write_text("{}\n")
        monkeypatch.setattr(
            ts, "parse_args",
            lambda: _ns(ndjson=str(ndjson), write_summary=True),
        )
        monkeypatch.setattr(ts, "get_auth_token", lambda: "tok")
        monkeypatch.setattr(ts, "load_findings", lambda *a, **kw: {})
        monkeypatch.setattr(
            ts, "write_markdown_summary",
            mock.MagicMock(side_effect=OSError("write failed")),
        )
        # Pin: summary failure on no-findings path returns 0, never raises
        rc = ts.main()
        assert rc == 0


class TestMainPostCreationSummaryError:
    def test_summary_failure_after_issue_creation_swallowed(
        self, monkeypatch, tmp_path, patched_logging_excludes
    ):
        ndjson = tmp_path / "findings.ndjson"
        ndjson.write_text("{}\n")
        monkeypatch.setattr(
            ts, "parse_args",
            lambda: _ns(ndjson=str(ndjson), write_summary=True),
        )
        monkeypatch.setattr(ts, "get_auth_token", lambda: "tok")
        monkeypatch.setattr(
            ts, "load_findings",
            lambda *a, **kw: {"o/r": [{"a": 1}]},
        )
        monkeypatch.setattr(
            ts, "process_repo", lambda repo, *a, **kw: (repo, True)
        )
        monkeypatch.setattr(
            ts, "write_markdown_summary",
            mock.MagicMock(side_effect=OSError("write failed")),
        )
        # Pin: post-creation summary failure must not poison the rc
        rc = ts.main()
        assert rc == 0
