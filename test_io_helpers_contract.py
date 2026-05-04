"""Contract pin: NDJSON / exclude-regex / preview / errors-file helpers.

Existing coverage (test_trufflehog_scanner.py): the pure helpers that
operate on a single finding dict (sanitize_repo, file_path, line_no,
deepget, etc.) plus the larger flows (load_findings,
build_issue_body, GHClient).

What's STILL unpinned and matters in production:

  * ``load_exclude_regexes`` -- the false-positive filter file. Drift
    in regex compilation tolerance silently swallows bad patterns OR
    crashes the whole scan. We pin: missing path -> [], invalid path
    -> [] + log warning, blank/comment lines skipped, bad regex
    skipped (NOT raised), case-insensitive compile.
  * ``ndjson_stats`` -- the dashboard-facing tuple
    ``(total, bad, repos, dets)``. Drift in any field silently breaks
    the run-summary panel. Pin: missing file = (0,0,0,0); blank lines
    skipped; bad-JSON counts toward bad; excluded findings still
    count in total but NOT in repos/dets.
  * ``load_errors_ndjson`` -- non-fatal scan errors fed into the
    summary. Pin: None/empty/missing -> []; non-dict JSON skipped
    silently (NOT raised); bad-JSON lines skipped.
  * ``_print_one_sanitized`` + ``print_sanitized_preview`` -- the
    operator-facing stdout preview. Pin: max_lines<=0 short-circuits
    (no IO); max_lines stops iteration; format string contains
    ``[<org>] <repo> | <detector> | ...`` (dashboards grep for the
    pipe-delimited shape).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from typing import List

import pytest

from trufflehog_scanner import (
    _print_one_sanitized,
    load_errors_ndjson,
    load_exclude_regexes,
    ndjson_stats,
    print_sanitized_preview,
)


# ---------------------------------------------------------------------------
# load_exclude_regexes
# ---------------------------------------------------------------------------


class TestLoadExcludeRegexes:
    def test_none_path_returns_empty(self) -> None:
        # Pin: a None argument is the "no exclude file configured"
        # signal -- MUST return [] (NOT raise). The scanner runs
        # unconfigured by default.
        assert load_exclude_regexes(None) == []

    def test_missing_path_returns_empty_and_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Pin: a stale path in CI config MUST log a warning + return
        # [] -- NOT raise. We don't want a typo'd path to brick scans.
        caplog.set_level("WARNING")
        out = load_exclude_regexes("/nonexistent/path/to/excludes.txt")
        assert out == []
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert any("not found" in r.message.lower() for r in warnings), (
            "expected a 'not found' warning"
        )

    def test_valid_patterns_compiled_case_insensitive(
        self, tmp_path
    ) -> None:
        # Pin: patterns are compiled case-insensitive. A scanner-side
        # flip to case-sensitive would silently miss half the false
        # positives.
        p = tmp_path / "excludes.txt"
        p.write_text("AKIA[A-Z0-9]+\nfoo.*bar\n")
        regs = load_exclude_regexes(str(p))
        assert len(regs) == 2
        # Case-insensitive: lowercase input matches uppercase pattern.
        assert regs[0].search("akiaABC123") is not None
        assert regs[1].search("FOObazBAR") is not None

    def test_blank_and_comment_lines_skipped(self, tmp_path) -> None:
        # Pin: ``#`` lines + blank lines are treated as comments. A
        # regression that compiled the literal "#" line would match
        # every string and false-suppress everything.
        p = tmp_path / "excludes.txt"
        p.write_text("\n# this is a comment\n\nrealpattern\n  \n")
        regs = load_exclude_regexes(str(p))
        assert len(regs) == 1
        assert regs[0].search("realpattern matches") is not None

    def test_bad_regex_skipped_not_raised(
        self, tmp_path, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Pin: a malformed regex MUST log a warning and continue --
        # one bad line shouldn't brick the whole scan.
        caplog.set_level("WARNING")
        p = tmp_path / "excludes.txt"
        p.write_text("good_pattern\n[unclosed\nanother_good\n")
        regs = load_exclude_regexes(str(p))
        assert len(regs) == 2
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert any("bad regex" in r.message.lower() for r in warnings)


# ---------------------------------------------------------------------------
# ndjson_stats
# ---------------------------------------------------------------------------


class TestNdjsonStats:
    def test_missing_file_returns_all_zeros(self) -> None:
        # Pin: the run-summary panel shows 0/0/0/0 for an org that
        # produced no NDJSON -- NOT a stack trace.
        out = ndjson_stats("/nonexistent/findings.ndjson", [], "demo-org")
        assert out == (0, 0, 0, 0)

    def test_counts_total_repos_dets(self, tmp_path) -> None:
        # Pin: tuple shape ``(total, bad, repos, dets)`` -- repos +
        # detectors are de-duplicated counts, total includes excluded.
        rows = [
            {
                "DetectorName": "AWS",
                "SourceMetadata": {
                    "Data": {
                        "Git": {
                            "repository": "https://github.com/o/a.git",
                            "file": "x.py",
                            "line": 1,
                        }
                    }
                },
            },
            {
                "DetectorName": "AWS",
                "SourceMetadata": {
                    "Data": {
                        "Git": {
                            "repository": "https://github.com/o/a.git",
                            "file": "y.py",
                            "line": 2,
                        }
                    }
                },
            },
            {
                "DetectorName": "GitHub",
                "SourceMetadata": {
                    "Data": {
                        "Git": {
                            "repository": "https://github.com/o/b.git",
                            "file": "z.py",
                            "line": 3,
                        }
                    }
                },
            },
        ]
        p = tmp_path / "f.ndjson"
        p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        total, bad, repos, dets = ndjson_stats(str(p), [], "demo-org")
        assert total == 3
        assert bad == 0
        # Two distinct repos (o/a, o/b).
        assert repos == 2
        # Two distinct detectors (AWS, GitHub).
        assert dets == 2

    def test_blank_lines_skipped(self, tmp_path) -> None:
        # Pin: blank lines MUST NOT count toward total or bad.
        p = tmp_path / "f.ndjson"
        p.write_text("\n\n   \n")
        assert ndjson_stats(str(p), [], "demo-org") == (0, 0, 0, 0)

    def test_bad_json_counts_as_bad_not_total_repos(self, tmp_path) -> None:
        # Pin: malformed JSON MUST increment ``bad`` AND ``total`` (a
        # bad line still costs IO) but contribute nothing to repos/dets.
        p = tmp_path / "f.ndjson"
        p.write_text("not-json\n{garbage\n")
        total, bad, repos, dets = ndjson_stats(str(p), [], "demo-org")
        assert total == 2
        assert bad == 2
        assert repos == 0
        assert dets == 0

    def test_excluded_finding_counts_total_but_not_repos(
        self, tmp_path
    ) -> None:
        # Pin: a finding matched by an exclude-regex still bumps total
        # (visibility into how many were filtered) but NOT repos/dets.
        row = {
            "DetectorName": "AWS",
            "SourceMetadata": {
                "Data": {
                    "Git": {
                        "repository": "https://github.com/o/excluded-repo.git",
                        "file": "x.py",
                        "line": 1,
                    }
                }
            },
        }
        p = tmp_path / "f.ndjson"
        p.write_text(json.dumps(row) + "\n")
        # Match the repo via compose_match_string format
        # ``repo=o/excluded-repo``.
        regs = [re.compile(r"excluded-repo", re.IGNORECASE)]
        total, bad, repos, dets = ndjson_stats(str(p), regs, "demo-org")
        assert total == 1
        assert bad == 0
        assert repos == 0
        assert dets == 0


# ---------------------------------------------------------------------------
# load_errors_ndjson
# ---------------------------------------------------------------------------


class TestLoadErrorsNdjson:
    def test_none_returns_empty(self) -> None:
        # Pin: errors_path is optional; None -> [] (NOT raise).
        assert load_errors_ndjson(None) == []

    def test_missing_path_returns_empty(self) -> None:
        assert load_errors_ndjson("/no/such/path.ndjson") == []

    def test_zero_size_returns_empty(self, tmp_path) -> None:
        # Pin: an empty file MUST NOT trigger an open-and-read; we
        # short-circuit on os.path.getsize(path)==0.
        p = tmp_path / "errors.ndjson"
        p.write_text("")
        assert load_errors_ndjson(str(p)) == []

    def test_dict_lines_loaded_non_dict_skipped(self, tmp_path) -> None:
        # Pin: only ``isinstance(o, dict)`` rows are kept -- a
        # JSON list/scalar would otherwise leak a non-dict shape into
        # the summary writer that expects ``.get(...)``.
        p = tmp_path / "errors.ndjson"
        p.write_text(
            json.dumps({"err": "boom"}) + "\n"
            + json.dumps([1, 2, 3]) + "\n"
            + json.dumps("scalar") + "\n"
            + json.dumps({"err": "again"}) + "\n"
        )
        out = load_errors_ndjson(str(p))
        assert out == [{"err": "boom"}, {"err": "again"}]

    def test_bad_json_lines_skipped(self, tmp_path) -> None:
        # Pin: malformed JSON MUST be silently skipped (not raised) --
        # one bad line shouldn't brick summary generation.
        p = tmp_path / "errors.ndjson"
        p.write_text(
            "not-json\n"
            + json.dumps({"err": "real"}) + "\n"
            + "{garbage\n"
        )
        assert load_errors_ndjson(str(p)) == [{"err": "real"}]

    def test_blank_lines_skipped(self, tmp_path) -> None:
        p = tmp_path / "errors.ndjson"
        p.write_text("\n\n" + json.dumps({"err": "x"}) + "\n\n")
        assert load_errors_ndjson(str(p)) == [{"err": "x"}]


# ---------------------------------------------------------------------------
# _print_one_sanitized + print_sanitized_preview
# ---------------------------------------------------------------------------


class TestSanitizedPreview:
    def _row(self, repo: str, det: str, fp: str, line: int) -> dict:
        return {
            "DetectorName": det,
            "Verified": True,
            "SourceMetadata": {
                "Data": {
                    "Git": {
                        "repository": f"https://github.com/{repo}.git",
                        "file": fp,
                        "line": line,
                        "commit": "deadbeef",
                    }
                }
            },
        }

    def test_print_one_sanitized_includes_org_and_pipe_format(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        # Pin: stdout format contains ``[<org>]`` prefix + pipe-delim
        # fields. Dashboards grep for ``|`` to split fields; drift to
        # comma-separated would break parsing.
        _print_one_sanitized(self._row("o/r", "AWS", "x.py", 42), "demo-org")
        captured = capsys.readouterr().out
        assert "[demo-org]" in captured
        assert "o/r" in captured
        assert "AWS" in captured
        assert " | " in captured
        # Line number rendered with the file
        assert "42" in captured

    def test_preview_max_lines_zero_short_circuits(
        self, tmp_path, capsys: pytest.CaptureFixture
    ) -> None:
        # Pin: max_lines<=0 MUST exit without opening the file -- a
        # regression that opened the file would crash on missing path.
        # We pass a guaranteed-missing path: the function MUST NOT
        # raise (proves the early-return guard).
        print_sanitized_preview(
            "/no/such/findings.ndjson", "demo-org", 0, []
        )
        assert capsys.readouterr().out == ""

    def test_preview_stops_at_max_lines(
        self, tmp_path, capsys: pytest.CaptureFixture
    ) -> None:
        # Pin: max_lines truncates output. Drift to "no cap" would
        # flood operator stdout on noisy scans.
        rows = [self._row("o/r", "AWS", f"f{i}.py", i) for i in range(5)]
        p = tmp_path / "f.ndjson"
        p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        print_sanitized_preview(str(p), "demo-org", 2, [])
        out = capsys.readouterr().out.strip().splitlines()
        # Exactly 2 sanitized lines (no log lines on stdout).
        assert len(out) == 2
        assert all("[demo-org]" in line for line in out)

    def test_preview_only_emits_verified(
        self, tmp_path, capsys: pytest.CaptureFixture
    ) -> None:
        # Pin: print_sanitized_preview iterates with verified_only=True.
        # An unverified finding MUST NOT show up in the operator
        # preview -- noisy false positives would drown the signal.
        verified = self._row("o/r", "AWS", "v.py", 1)
        unverified = self._row("o/r", "GitHub", "u.py", 2)
        unverified["Verified"] = False
        p = tmp_path / "f.ndjson"
        p.write_text(
            json.dumps(verified) + "\n" + json.dumps(unverified) + "\n"
        )
        print_sanitized_preview(str(p), "demo-org", 10, [])
        out = capsys.readouterr().out
        assert "v.py" in out
        assert "u.py" not in out
