"""
Unit tests for trufflehog_scanner.py

Run with: pytest test_trufflehog_scanner.py -v
"""

import os
import re
import tempfile
from unittest import mock

import pytest

# Import functions to test
from trufflehog_scanner import (
    _safe_int_env,
    _severity_rank,
    deepget,
    sanitize_repo,
    extract_repo,
    find_repo,
    file_path,
    line_no,
    commit_sha,
    shortpath,
    line_link,
    normalize_detector,
    labels_for_items,
    make_finding_key,
    compose_match_string,
    is_excluded,
    iter_json,
    compute_findings_hash,
    RateLimiter,
)


class TestSafeIntEnv:
    """Tests for _safe_int_env helper function."""

    def test_valid_int(self):
        with mock.patch.dict(os.environ, {"TEST_VAR": "42"}):
            assert _safe_int_env("TEST_VAR", 0) == 42

    def test_missing_env_returns_default(self):
        assert _safe_int_env("NONEXISTENT_VAR_12345", 99) == 99

    def test_invalid_int_returns_default(self):
        with mock.patch.dict(os.environ, {"TEST_VAR": "not_a_number"}):
            assert _safe_int_env("TEST_VAR", 50) == 50

    def test_empty_string_returns_default(self):
        with mock.patch.dict(os.environ, {"TEST_VAR": ""}):
            assert _safe_int_env("TEST_VAR", 25) == 25


class TestSeverityRank:
    """Tests for _severity_rank helper function."""

    def test_known_severities(self):
        assert _severity_rank("sec:low") == 0
        assert _severity_rank("sec:medium") == 1
        assert _severity_rank("sec:high") == 2
        assert _severity_rank("sec:critical") == 3

    def test_unknown_severity_defaults_to_medium(self):
        assert _severity_rank("unknown") == 1
        assert _severity_rank("") == 1


class TestDeepget:
    """Tests for deepget nested dictionary access."""

    def test_simple_path(self):
        obj = {"a": {"b": {"c": 42}}}
        assert deepget(obj, ["a", "b", "c"]) == 42

    def test_missing_key_returns_none(self):
        obj = {"a": {"b": 1}}
        assert deepget(obj, ["a", "c"]) is None

    def test_empty_path_returns_obj(self):
        obj = {"a": 1}
        assert deepget(obj, []) == obj

    def test_non_dict_in_path_returns_none(self):
        obj = {"a": [1, 2, 3]}
        assert deepget(obj, ["a", "b"]) is None


class TestSanitizeRepo:
    """Tests for sanitize_repo function."""

    def test_removes_git_suffix(self):
        assert sanitize_repo("owner/repo.git") == "owner/repo"
        assert sanitize_repo("owner/repo.GIT") == "owner/repo"

    def test_preserves_valid_repo(self):
        assert sanitize_repo("owner/repo") == "owner/repo"

    def test_empty_string(self):
        assert sanitize_repo("") == ""

    def test_no_slash_returns_as_is(self):
        assert sanitize_repo("noslash") == "noslash"

    def test_none_returns_empty(self):
        assert sanitize_repo(None) == ""


class TestExtractRepo:
    """Tests for extract_repo function."""

    def test_github_https_url(self):
        assert extract_repo("https://github.com/owner/repo") == "owner/repo"
        assert extract_repo("https://github.com/owner/repo.git") == "owner/repo"
        assert extract_repo("https://github.com/owner/repo/blob/main/file.py") == "owner/repo"

    def test_simple_owner_repo(self):
        assert extract_repo("owner/repo") == "owner/repo"

    def test_none_returns_none(self):
        assert extract_repo(None) is None

    def test_empty_returns_none(self):
        assert extract_repo("") is None

    def test_single_segment_returns_none(self):
        assert extract_repo("justowner") is None


class TestFindRepo:
    """Tests for find_repo function."""

    def test_git_source_metadata(self):
        obj = {
            "SourceMetadata": {
                "Data": {
                    "Git": {
                        "repository": "https://github.com/owner/repo"
                    }
                }
            }
        }
        assert find_repo(obj) == "owner/repo"

    def test_github_source_metadata(self):
        obj = {
            "SourceMetadata": {
                "Data": {
                    "GitHub": {
                        "repository": "owner/repo"
                    }
                }
            }
        }
        assert find_repo(obj) == "owner/repo"

    def test_fallback_to_url_pattern(self):
        obj = {"Raw": "found at https://github.com/found/here/blob/main/file.py"}
        assert find_repo(obj) == "found/here"

    def test_empty_obj_returns_none(self):
        assert find_repo({}) is None


class TestFilePath:
    """Tests for file_path function."""

    def test_extracts_file_from_git_metadata(self):
        obj = {"SourceMetadata": {"Data": {"Git": {"file": "src/config.py"}}}}
        assert file_path(obj) == "src/config.py"

    def test_missing_file_returns_empty(self):
        assert file_path({}) == ""


class TestLineNo:
    """Tests for line_no function."""

    def test_int_line_number(self):
        obj = {"SourceMetadata": {"Data": {"Git": {"line": 42}}}}
        assert line_no(obj) == 42

    def test_string_line_number(self):
        obj = {"SourceMetadata": {"Data": {"Git": {"line": "100"}}}}
        assert line_no(obj) == 100

    def test_missing_returns_none(self):
        assert line_no({}) is None

    def test_non_numeric_string_returns_none(self):
        obj = {"SourceMetadata": {"Data": {"Git": {"line": "abc"}}}}
        assert line_no(obj) is None


class TestCommitSha:
    """Tests for commit_sha function."""

    def test_extracts_commit(self):
        obj = {"SourceMetadata": {"Data": {"Git": {"commit": "abc123def"}}}}
        assert commit_sha(obj) == "abc123def"

    def test_missing_returns_none(self):
        assert commit_sha({}) is None


class TestShortpath:
    """Tests for shortpath function."""

    def test_short_path_unchanged(self):
        assert shortpath("src/file.py") == "src/file.py"
        assert shortpath("a/b/c") == "a/b/c"

    def test_long_path_truncated(self):
        assert shortpath("a/b/c/d/e/f.py") == ".../d/e/f.py"

    def test_empty_string(self):
        assert shortpath("") == ""

    def test_none_returns_empty(self):
        assert shortpath(None) == ""


class TestLineLink:
    """Tests for line_link function."""

    def test_full_link_with_commit(self):
        link = line_link("owner/repo", "abc123", "src/file.py", 42)
        assert link == "https://github.com/owner/repo/blob/abc123/src/file.py"
        assert "#L42" not in link  # Line anchor intentionally omitted

    def test_link_without_commit(self):
        link = line_link("owner/repo", None, "src/file.py", 42)
        assert link == "https://github.com/owner/repo/blob/HEAD/src/file.py"

    def test_commit_only_link(self):
        link = line_link("owner/repo", "abc123", "", None)
        assert link == "https://github.com/owner/repo/commit/abc123"

    def test_missing_repo_returns_empty(self):
        assert line_link("", None, "file.py", 1) == ""

    def test_url_encodes_path(self):
        link = line_link("owner/repo", "abc", "path with spaces/file.py", 1)
        assert "path%20with%20spaces" in link


class TestNormalizeDetector:
    """Tests for normalize_detector function."""

    def test_aws_detector(self):
        assert normalize_detector("AWS") == "AWS"
        assert normalize_detector("aws_access_key") == "AWS"

    def test_github_detector(self):
        assert normalize_detector("GitHub") == "GitHub"
        assert normalize_detector("github_token") == "GitHub"

    def test_slack_detector(self):
        # Any input containing "slack" matches "Slack" detector
        # (dict iteration order means Slack checked before SlackWebhook)
        assert normalize_detector("Slack Webhook URL") == "Slack"
        assert normalize_detector("slackwebhook_token") == "Slack"
        assert normalize_detector("slack_api") == "Slack"

    def test_private_key(self):
        assert normalize_detector("RSA Private Key") == "Private Key"
        assert normalize_detector("PEM file") == "Private Key"

    def test_unknown_returns_generic(self):
        assert normalize_detector("SomeUnknownDetector") == "Generic"
        assert normalize_detector("") == "Generic"


class TestLabelsForItems:
    """Tests for labels_for_items function."""

    def test_aws_finding_labels(self):
        items = [{"detector": "AWS"}]
        labels = labels_for_items(items)
        assert "sec:critical" in labels
        assert "secret:aws" in labels
        assert "needs-triage" in labels

    def test_multiple_findings_highest_severity(self):
        items = [
            {"detector": "GitHub"},  # sec:high
            {"detector": "AWS"},     # sec:critical
        ]
        labels = labels_for_items(items)
        assert "sec:critical" in labels
        assert "sec:high" in labels

    def test_extra_labels_added(self):
        items = [{"detector": "GitHub"}]
        labels = labels_for_items(items, extra=["custom-label", "another-one"])
        assert "custom-label" in labels
        assert "another-one" in labels

    def test_empty_extra_labels_ignored(self):
        items = [{"detector": "AWS"}]
        labels = labels_for_items(items, extra=["", "  ", "valid"])
        assert "valid" in labels
        assert "" not in labels


class TestMakeFindingKey:
    """Tests for make_finding_key function."""

    def test_full_key(self):
        key = make_finding_key("owner/repo", "AWS", "file.py", 42, "abc123")
        assert key == ("owner/repo", "AWS", "file.py", 42, "abc123")

    def test_none_line_becomes_minus_one(self):
        key = make_finding_key("owner/repo", "AWS", "file.py", None, "abc123")
        assert key[3] == -1

    def test_none_commit_becomes_empty(self):
        key = make_finding_key("owner/repo", "AWS", "file.py", 42, None)
        assert key[4] == ""

    def test_sanitizes_repo(self):
        key = make_finding_key("owner/repo.git", "AWS", "file.py", 1, "abc")
        assert key[0] == "owner/repo"


class TestComposeMatchString:
    """Tests for compose_match_string function."""

    def test_composes_all_fields(self):
        obj = {
            "DetectorName": "AWS",
            "Redacted": "AKIA***EXAMPLE",
            "Verified": True,
            "SourceMetadata": {
                "Data": {
                    "Git": {
                        "file": "config.py",
                        "repository": "owner/repo"
                    }
                }
            }
        }
        match_str = compose_match_string(obj)
        assert "detector=AWS" in match_str
        assert "verified=True" in match_str
        assert "file=config.py" in match_str
        assert "repo=owner/repo" in match_str
        assert "redacted=AKIA***EXAMPLE" in match_str


class TestIsExcluded:
    """Tests for is_excluded function."""

    def test_matches_pattern(self):
        # Pattern matches against compose_match_string output (detector, repo, file, redacted)
        obj = {"DetectorName": "EXAMPLE_DETECTOR", "Redacted": "test"}
        patterns = [re.compile(r"EXAMPLE")]
        assert is_excluded(obj, patterns) is True

    def test_no_match(self):
        obj = {"DetectorName": "AWS", "Redacted": "REAL_SECRET"}
        patterns = [re.compile(r"NOTFOUND")]
        assert is_excluded(obj, patterns) is False

    def test_empty_patterns(self):
        obj = {"DetectorName": "anything"}
        assert is_excluded(obj, []) is False


class TestIterJson:
    """Tests for iter_json generator function."""

    def test_reads_json_lines(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write('{"key": "value1"}\n')
            f.write('{"key": "value2"}\n')
            f.name
        try:
            results = list(iter_json(f.name))
            assert len(results) == 2
            assert results[0]["key"] == "value1"
            assert results[1]["key"] == "value2"
        finally:
            os.unlink(f.name)

    def test_skips_invalid_json(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write('{"valid": true}\n')
            f.write('not valid json\n')
            f.write('{"also_valid": true}\n')
            f.name
        try:
            results = list(iter_json(f.name))
            assert len(results) == 2
        finally:
            os.unlink(f.name)

    def test_verified_only_filter(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write('{"Verified": true, "id": 1}\n')
            f.write('{"Verified": false, "id": 2}\n')
            f.write('{"id": 3}\n')
            f.name
        try:
            results = list(iter_json(f.name, verified_only=True))
            assert len(results) == 1
            assert results[0]["id"] == 1
        finally:
            os.unlink(f.name)

    def test_nonexistent_file_yields_nothing(self):
        results = list(iter_json("/nonexistent/path/file.json"))
        assert results == []


class TestComputeFindingsHash:
    """Tests for compute_findings_hash function."""

    def test_same_items_same_hash(self):
        items = [
            {"detector": "AWS", "file": "a.py", "line": 1, "commit": "abc"},
            {"detector": "GitHub", "file": "b.py", "line": 2, "commit": "def"},
        ]
        hash1 = compute_findings_hash(items)
        hash2 = compute_findings_hash(items)
        assert hash1 == hash2

    def test_different_items_different_hash(self):
        items1 = [{"detector": "AWS", "file": "a.py", "line": 1, "commit": "abc"}]
        items2 = [{"detector": "GitHub", "file": "b.py", "line": 2, "commit": "def"}]
        assert compute_findings_hash(items1) != compute_findings_hash(items2)

    def test_order_independent(self):
        items1 = [
            {"detector": "AWS", "file": "a.py", "line": 1, "commit": "abc"},
            {"detector": "GitHub", "file": "b.py", "line": 2, "commit": "def"},
        ]
        items2 = [
            {"detector": "GitHub", "file": "b.py", "line": 2, "commit": "def"},
            {"detector": "AWS", "file": "a.py", "line": 1, "commit": "abc"},
        ]
        assert compute_findings_hash(items1) == compute_findings_hash(items2)


class TestRateLimiter:
    """Tests for RateLimiter class."""

    def test_wait_respects_interval(self):
        limiter = RateLimiter(calls_per_sec=10.0)  # 0.1s interval
        assert limiter.interval == pytest.approx(0.1, rel=0.01)

    def test_handles_zero_rate(self):
        # Should not crash with zero rate
        limiter = RateLimiter(calls_per_sec=0)
        assert limiter.interval > 0

    def test_handles_negative_rate(self):
        # Should handle negative rate gracefully
        limiter = RateLimiter(calls_per_sec=-5.0)
        assert limiter.interval > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
