"""
Contract tests for residual write_markdown_summary edges.

Targets currently-uncovered branches in write_markdown_summary:
  * 1246 -- blank line in ndjson skipped
  * 1250-1252 -- malformed JSON line increments total_bad
  * 1279, 1290 -- dedup hits (seen_verified / seen_unverified)
  * 1314 -- failed_repos summary row
  * 1325 -- malformed-lines counter rendered in summary
  * No-target fallthrough when finding has no repo metadata (1265 already
    covered via target_repo, but no-target route emits 'continue' silently)

Drives write_markdown_summary with carefully-shaped NDJSON inputs and
captures the markdown via GITHUB_STEP_SUMMARY to a tempfile.
"""
from __future__ import annotations

import json
import os
import tempfile
from unittest import mock

import pytest

import trufflehog_scanner as ts


def _run_summary(ndjson_lines: list, **kwargs) -> str:
    """Write NDJSON to a temp file, capture summary markdown, return it."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ndjson", delete=False
    ) as nd:
        for line in ndjson_lines:
            nd.write(line)
            if not line.endswith("\n"):
                nd.write("\n")
        ndjson_path = nd.name

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False
    ) as out:
        out_path = out.name

    try:
        with mock.patch.dict(
            os.environ, {"GITHUB_STEP_SUMMARY": out_path}
        ):
            ts.write_markdown_summary(ndjson_path, "testorg", [], **kwargs)
        with open(out_path) as f:
            return f.read()
    finally:
        os.unlink(ndjson_path)
        os.unlink(out_path)


def _verified_finding(
    repo: str = "o/r", det: str = "AWS", path: str = "a.py", line: int = 1
) -> str:
    o = {
        "DetectorName": det,
        "Verified": True,
        "SourceMetadata": {
            "Data": {
                "Git": {
                    "repository": f"https://github.com/{repo}.git",
                    "file": path,
                    "line": line,
                    "commit": "abc",
                }
            }
        },
        "Raw": "secret",
    }
    return json.dumps(o)


def _unverified_finding(
    repo: str = "o/r", det: str = "AWS", path: str = "a.py", line: int = 1
) -> str:
    o = json.loads(_verified_finding(repo, det, path, line))
    o["Verified"] = False
    return json.dumps(o)


class TestBlankAndMalformedLines:
    def test_blank_lines_skipped(self):
        # Pin: blank lines must not increment total_lines or total_bad
        out = _run_summary(["", "", _verified_finding()])
        # Single finding survives -- total counted as 1
        assert "**1**" in out

    def test_malformed_json_line_increments_total_bad(self):
        # Pin: invalid JSON line counts toward total_bad and renders the row
        out = _run_summary(
            ["{not json", "also-not-json", _verified_finding()]
        )
        # Pin: 'Malformed NDJSON lines' row with count 2
        assert "Malformed NDJSON" in out
        assert "**2**" in out

    def test_zero_malformed_omits_row(self):
        out = _run_summary([_verified_finding()])
        # Pin: row only renders when total_bad > 0
        assert "Malformed NDJSON" not in out


class TestDedupHits:
    def test_duplicate_verified_finding_counted_once(self):
        # Same repo/detector/file/line/commit twice -> dedup'd
        line = _verified_finding()
        out = _run_summary([line, line, line])
        # Only 1 unique verified finding -- not 3
        # Pin: Total unique findings = 1 confirms dedup
        assert "Total unique findings | **1**" in out

    def test_duplicate_unverified_finding_counted_once(self):
        line = _unverified_finding()
        out = _run_summary([line, line])
        # Pin: Unverified dedup also active (separate from verified)
        assert "Total unique findings | **1**" in out


class TestFailedReposSummaryRow:
    def test_failed_repos_row_rendered_when_present(self):
        # Pin: 'Repos with issue creation failures' row only when count present
        out = _run_summary(
            [_verified_finding()],
            created_count=0,
            skipped_count=0,
            failed_repos=["org/broken1", "org/broken2"],
        )
        assert "Repos with issue creation failures" in out
        assert "**2**" in out

    def test_failed_repos_row_omitted_when_empty(self):
        out = _run_summary(
            [_verified_finding()],
            created_count=1,
            skipped_count=0,
            failed_repos=[],
        )
        # Pin: empty list -> row suppressed
        assert "Repos with issue creation failures" not in out

    def test_unaccounted_repos_warning_when_gap(self):
        # 1 verified finding in o/r -> ver_repo_count=1
        # created+skipped+failed = 0+0+0 = 0 -> gap of 1 -> warning row
        out = _run_summary(
            [_verified_finding()],
            created_count=0,
            skipped_count=0,
            failed_repos=[],
        )
        # Pin: unaccounted-repos warning row when gap > 0
        assert "Repos unaccounted for" in out


class TestNoRepoNoTargetSkipped:
    def test_finding_without_repo_or_target_dropped(self):
        # No SourceMetadata -> no repo extracted; with no target_repo, drop
        bad = json.dumps({
            "DetectorName": "AWS", "Verified": True, "Raw": "x",
        })
        good = _verified_finding()
        out = _run_summary([bad, good])
        # Pin: 'bad' silently dropped, 'good' counted -> total = 1
        assert "Total unique findings | **1**" in out


class TestMissingNdjson:
    def test_missing_ndjson_still_writes_summary(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False
        ) as out:
            out_path = out.name
        try:
            with mock.patch.dict(
                os.environ, {"GITHUB_STEP_SUMMARY": out_path}
            ):
                # Pin: missing path -> summary still emitted with zeros
                ts.write_markdown_summary("/nonexistent.ndjson", "org", [])
            with open(out_path) as f:
                content = f.read()
            assert "Total unique findings | **0**" in content
        finally:
            os.unlink(out_path)

    def test_empty_ndjson_still_writes_summary(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".ndjson", delete=False
        ) as nd:
            nd_path = nd.name  # zero-byte file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False
        ) as out:
            out_path = out.name
        try:
            with mock.patch.dict(
                os.environ, {"GITHUB_STEP_SUMMARY": out_path}
            ):
                ts.write_markdown_summary(nd_path, "org", [])
            with open(out_path) as f:
                content = f.read()
            # Pin: zero-byte file skipped (size>0 guard), summary still emits
            assert "Total unique findings | **0**" in content
        finally:
            os.unlink(nd_path)
            os.unlink(out_path)
