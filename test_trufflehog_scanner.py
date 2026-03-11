"""
Unit tests for trufflehog_scanner.py

Run with: pytest test_trufflehog_scanner.py -v
"""

import json
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
    blame_link,
    normalize_detector,
    labels_for_items,
    highest_severity,
    make_finding_key,
    compose_match_string,
    is_excluded,
    iter_ndjson,
    compute_findings_hash,
    load_findings,
    repo_is_actionable,
    build_issue_body,
    write_markdown_summary,
    DETECTOR_REMEDIATION,
    DETECTOR_SEVERITY,
    SUPPRESSION_LABELS,
    SUPPRESSION_LABEL_NAMES,
    COMMENT_COMMANDS,
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

    def test_github_lowercase_source_metadata(self):
        """TruffleHog uses 'Github' (lowercase h) in its output."""
        obj = {
            "SourceMetadata": {
                "Data": {
                    "Github": {
                        "repository": "https://github.com/trufflesecurity/test_keys.git"
                    }
                }
            }
        }
        assert find_repo(obj) == "trufflesecurity/test_keys"

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


class TestBlameLink:
    """Tests for blame_link function."""

    def test_full_blame_with_commit_and_line(self):
        link = blame_link("owner/repo", "abc123", "src/file.py", 42)
        assert link == "https://github.com/owner/repo/blame/abc123/src/file.py#L42"

    def test_blame_without_commit(self):
        link = blame_link("owner/repo", None, "src/file.py", 42)
        assert link == "https://github.com/owner/repo/blame/HEAD/src/file.py#L42"

    def test_blame_without_line(self):
        link = blame_link("owner/repo", "abc123", "src/file.py", None)
        assert link == "https://github.com/owner/repo/blame/abc123/src/file.py"

    def test_blame_missing_repo_returns_empty(self):
        assert blame_link("", None, "file.py", 1) == ""

    def test_blame_missing_path_returns_empty(self):
        assert blame_link("owner/repo", "abc", "", 1) == ""

    def test_blame_url_encodes_path(self):
        link = blame_link("owner/repo", "abc", "path with spaces/file.py", 1)
        assert "path%20with%20spaces" in link
        assert "#L1" in link


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
        assert "needs-triage" in labels
        assert "security" in labels
        assert "tool:trufflehog" in labels
        # Detector-type labels (e.g. secret:aws) removed in label reduction
        assert "secret:aws" not in labels

    def test_multiple_findings_highest_severity(self):
        """Only the single highest severity label should be present."""
        items = [
            {"detector": "GitHub"},  # sec:high
            {"detector": "AWS"},     # sec:critical
        ]
        labels = labels_for_items(items)
        assert "sec:critical" in labels
        # Only highest severity kept — sec:high should NOT be present
        assert "sec:high" not in labels

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


class TestIterNdjson:
    """Tests for iter_ndjson generator function."""

    def test_reads_ndjson_lines(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ndjson', delete=False) as f:
            f.write('{"key": "value1"}\n')
            f.write('{"key": "value2"}\n')
            f.name
        try:
            results = list(iter_ndjson(f.name))
            assert len(results) == 2
            assert results[0]["key"] == "value1"
            assert results[1]["key"] == "value2"
        finally:
            os.unlink(f.name)

    def test_skips_invalid_json(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ndjson', delete=False) as f:
            f.write('{"valid": true}\n')
            f.write('not valid json\n')
            f.write('{"also_valid": true}\n')
            f.name
        try:
            results = list(iter_ndjson(f.name))
            assert len(results) == 2
        finally:
            os.unlink(f.name)

    def test_verified_only_filter(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ndjson', delete=False) as f:
            f.write('{"Verified": true, "id": 1}\n')
            f.write('{"Verified": false, "id": 2}\n')
            f.write('{"id": 3}\n')
            f.name
        try:
            results = list(iter_ndjson(f.name, verified_only=True))
            assert len(results) == 1
            assert results[0]["id"] == 1
        finally:
            os.unlink(f.name)

    def test_nonexistent_file_yields_nothing(self):
        results = list(iter_ndjson("/nonexistent/path/file.ndjson"))
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


# Import GHClient for integration tests
from trufflehog_scanner import GHClient


class TestGHClient:
    """Integration tests for GHClient GitHub API wrapper."""

    def test_dry_run_mode_does_not_call_api(self):
        """Dry run mode should not make actual API calls for mutating methods."""
        client = GHClient(token="fake-token", dry_run=True)
        # In dry run, POST/PUT/PATCH/DELETE should return empty dict without calling API
        result = client.call("POST", "/repos/test/test/issues", {"title": "test"})
        assert result == {}

    def test_dry_run_put_returns_empty(self):
        """Dry run PUT requests should return empty dict without calling API."""
        client = GHClient(token="fake-token", dry_run=True)
        result = client.call("PUT", "/repos/test/test/labels", {"name": "test"})
        assert result == {}

    @mock.patch('trufflehog_scanner.urlopen')
    def test_successful_api_call(self, mock_urlopen):
        """Test successful API call parsing."""
        mock_response = mock.MagicMock()
        mock_response.read.return_value = b'{"id": 123, "title": "test issue"}'
        mock_response.status = 200
        mock_response.__enter__ = mock.MagicMock(return_value=mock_response)
        mock_response.__exit__ = mock.MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        client = GHClient(token="fake-token", dry_run=False)
        result = client.call("GET", "/repos/owner/repo/issues/1", None)
        
        assert result["id"] == 123
        assert result["title"] == "test issue"

    @mock.patch('trufflehog_scanner.urlopen')
    def test_http_error_raises(self, mock_urlopen):
        """Test that HTTP errors are raised after retries exhausted."""
        from urllib.error import HTTPError
        mock_urlopen.side_effect = HTTPError(
            url="https://api.github.com/test",
            code=404,
            msg="Not Found",
            hdrs={},
            fp=None
        )

        client = GHClient(token="fake-token", dry_run=False)
        with pytest.raises(HTTPError):
            client.call("GET", "/repos/owner/nonexistent", None, max_retries=1)

    @mock.patch('trufflehog_scanner.urlopen')
    def test_rate_limit_retry(self, mock_urlopen):
        """Test that rate limit (403) triggers retry with backoff."""
        from urllib.error import HTTPError
        
        # First call: rate limited, second call: success
        mock_response = mock.MagicMock()
        mock_response.read.return_value = b'{"success": true}'
        mock_response.status = 200
        mock_response.__enter__ = mock.MagicMock(return_value=mock_response)
        mock_response.__exit__ = mock.MagicMock(return_value=False)
        
        rate_limit_error = HTTPError(
            url="https://api.github.com/test",
            code=403,
            msg="Rate limited",
            hdrs={},
            fp=None
        )
        
        mock_urlopen.side_effect = [rate_limit_error, mock_response]

        client = GHClient(token="fake-token", dry_run=False)
        result = client.call("GET", "/repos/owner/repo", None, max_retries=3, backoff_base=0.01)
        
        assert result["success"] is True
        assert mock_urlopen.call_count == 2

    def test_search_issue_by_title_dry_run(self):
        """Test search_issue_by_title in dry run mode."""
        client = GHClient(token="fake-token", dry_run=True)
        # Dry run should return False (no issue found)
        result = client.search_issue_by_title("owner/repo", "Test Issue")
        assert result is False

    def test_search_recent_issues_dry_run(self):
        """Test search_recent_issues in dry run mode."""
        client = GHClient(token="fake-token", dry_run=True)
        result = client.search_recent_issues("owner/repo", "[TruffleHog]")
        assert result is False

    def test_search_recent_issues_with_hash_dry_run(self):
        """Test search_recent_issues_with_hash in dry run mode."""
        client = GHClient(token="fake-token", dry_run=True)
        result = client.search_recent_issues_with_hash("owner/repo", "[TruffleHog]", "abc123")
        assert result is False

    @mock.patch('trufflehog_scanner.urlopen')
    def test_create_issue_returns_url(self, mock_urlopen):
        """Test that create_issue returns the issue URL."""
        mock_response = mock.MagicMock()
        mock_response.read.return_value = b'{"html_url": "https://github.com/owner/repo/issues/42", "number": 42}'
        mock_response.status = 201
        mock_response.__enter__ = mock.MagicMock(return_value=mock_response)
        mock_response.__exit__ = mock.MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        client = GHClient(token="fake-token", dry_run=False)
        result = client.create_issue("owner/repo", "Test Title", "Test Body", ["bug"])
        
        assert result["html_url"] == "https://github.com/owner/repo/issues/42"
        assert result["number"] == 42

    @mock.patch('trufflehog_scanner.urlopen')
    def test_repo_meta_returns_metadata(self, mock_urlopen):
        """Test that repo_meta returns repository metadata."""
        mock_response = mock.MagicMock()
        mock_response.read.return_value = b'{"archived": false, "disabled": false, "full_name": "owner/repo"}'
        mock_response.status = 200
        mock_response.__enter__ = mock.MagicMock(return_value=mock_response)
        mock_response.__exit__ = mock.MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        client = GHClient(token="fake-token", dry_run=False)
        meta = client.repo_meta("owner/repo")
        
        assert meta["archived"] is False
        assert meta["full_name"] == "owner/repo"

    @mock.patch('trufflehog_scanner.urlopen')
    def test_repo_meta_dry_run_still_fetches(self, mock_urlopen):
        """Test that repo_meta in dry run still makes GET (read-only) calls."""
        mock_response = mock.MagicMock()
        mock_response.read.return_value = b'{"archived": false, "full_name": "owner/repo"}'
        mock_response.__enter__ = mock.MagicMock(return_value=mock_response)
        mock_response.__exit__ = mock.MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        client = GHClient(token="fake-token", dry_run=True)
        meta = client.repo_meta("owner/repo")
        # Dry run only blocks mutating ops; GET still goes through
        assert meta["full_name"] == "owner/repo"
        mock_urlopen.assert_called_once()


class TestProcessRepo:
    """Integration tests for process_repo function."""

    @mock.patch.object(GHClient, 'for_org')
    @mock.patch.object(GHClient, 'repo_meta')
    @mock.patch.object(GHClient, 'search_issue_by_title')
    @mock.patch.object(GHClient, 'search_recent_issues_with_hash')
    @mock.patch.object(GHClient, 'create_issue')
    @mock.patch.object(GHClient, 'create_labels_if_needed')
    @mock.patch.object(GHClient, 'get_suppressed_hashes')
    @mock.patch.object(GHClient, 'process_comment_commands')
    @mock.patch.object(GHClient, 'create_suppression_labels')
    def test_process_repo_creates_issue(self, mock_supp_labels, mock_comments, mock_suppressed,
                                         mock_labels, mock_create, mock_hash_search, 
                                         mock_title_search, mock_meta, mock_for_org):
        """Test that process_repo creates an issue when none exists."""
        from trufflehog_scanner import process_repo
        
        mock_meta.return_value = {"archived": False, "disabled": False}
        mock_title_search.return_value = False
        mock_hash_search.return_value = False
        mock_suppressed.return_value = set()
        mock_comments.return_value = 0
        mock_create.return_value = {"html_url": "https://github.com/owner/repo/issues/1", "number": 1}
        mock_labels.return_value = None
        mock_supp_labels.return_value = None

        client = GHClient(token="fake-token", dry_run=False)
        mock_for_org.return_value = client
        items = [{"detector": "AWS", "file": "config.py", "line": 10, "commit": "abc123"}]
        
        repo, created = process_repo(
            repo="owner/repo",
            items=items,
            gh=client,
            title_prefix="[TruffleHog]",
            run_url="https://github.com/owner/repo/actions/runs/123",
            extra_labels=[],
            dry_run=False
        )
        
        assert repo == "owner/repo"
        assert created is True
        mock_create.assert_called_once()
        mock_for_org.assert_called_once_with("owner")
        mock_suppressed.assert_called_once()  # Suppression check should be called
        mock_supp_labels.assert_called_once()  # Suppression labels should be created

    @mock.patch.object(GHClient, 'for_org')
    @mock.patch.object(GHClient, 'repo_meta')
    @mock.patch.object(GHClient, 'search_issue_by_title')
    @mock.patch.object(GHClient, 'get_suppressed_hashes')
    @mock.patch.object(GHClient, 'process_comment_commands')
    def test_process_repo_skips_existing_issue(self, mock_comments, mock_suppressed,
                                                mock_title_search, mock_meta, mock_for_org):
        """Test that process_repo skips when issue already exists."""
        from trufflehog_scanner import process_repo
        
        mock_meta.return_value = {"archived": False, "disabled": False}
        mock_suppressed.return_value = set()
        mock_comments.return_value = 0
        mock_title_search.return_value = True  # Issue exists

        client = GHClient(token="fake-token", dry_run=False)
        mock_for_org.return_value = client
        items = [{"detector": "AWS", "file": "config.py", "line": 10, "commit": "abc123"}]
        
        repo, created = process_repo(
            repo="owner/repo",
            items=items,
            gh=client,
            title_prefix="[TruffleHog]",
            run_url="https://github.com/owner/repo/actions/runs/123",
            extra_labels=[],
            dry_run=False
        )
        
        assert repo == "owner/repo"
        assert created is False

    @mock.patch.object(GHClient, 'for_org')
    @mock.patch.object(GHClient, 'repo_meta')
    @mock.patch.object(GHClient, 'search_issue_by_title')
    @mock.patch.object(GHClient, 'search_recent_issues_with_hash')
    @mock.patch.object(GHClient, 'create_issue')
    @mock.patch.object(GHClient, 'create_labels_if_needed')
    @mock.patch.object(GHClient, 'get_suppressed_hashes')
    @mock.patch.object(GHClient, 'process_comment_commands')
    @mock.patch.object(GHClient, 'create_suppression_labels')
    def test_process_repo_force_create_bypasses_dedup(self, mock_supp_labels, mock_comments, mock_suppressed,
                                                        mock_labels, mock_create, 
                                                        mock_hash_search, mock_title_search, mock_meta, mock_for_org):
        """Test that force_create=True bypasses deduplication checks."""
        from trufflehog_scanner import process_repo
        
        mock_meta.return_value = {"archived": False, "disabled": False}
        mock_title_search.return_value = True  # Issue would normally exist
        mock_hash_search.return_value = True   # Hash would normally match
        mock_create.return_value = {"html_url": "https://github.com/owner/repo/issues/2", "number": 2}
        mock_labels.return_value = None
        mock_suppressed.return_value = set()
        mock_comments.return_value = 0
        mock_supp_labels.return_value = None

        client = GHClient(token="fake-token", dry_run=False)
        mock_for_org.return_value = client
        items = [{"detector": "AWS", "file": "config.py", "line": 10, "commit": "abc123"}]
        
        repo, created = process_repo(
            repo="owner/repo",
            items=items,
            gh=client,
            title_prefix="[TruffleHog]",
            run_url="https://github.com/owner/repo/actions/runs/123",
            extra_labels=[],
            dry_run=False,
            force_create=True  # Force creation despite existing issues
        )
        
        assert repo == "owner/repo"
        assert created is True
        # Dedup checks should not be called when force_create=True
        mock_title_search.assert_not_called()
        mock_hash_search.assert_not_called()
        mock_suppressed.assert_not_called()  # Suppression also skipped
        mock_create.assert_called_once()

    @mock.patch.object(GHClient, 'for_org')
    @mock.patch.object(GHClient, 'repo_meta')
    def test_process_repo_skips_archived_repo(self, mock_meta, mock_for_org):
        """Test that process_repo skips archived repositories."""
        from trufflehog_scanner import process_repo
        
        mock_meta.return_value = {"archived": True, "disabled": False}

        client = GHClient(token="fake-token", dry_run=False)
        mock_for_org.return_value = client
        items = [{"detector": "AWS", "file": "config.py", "line": 10, "commit": "abc123"}]
        
        repo, created = process_repo(
            repo="owner/repo",
            items=items,
            gh=client,
            title_prefix="[TruffleHog]",
            run_url="https://github.com/owner/repo/actions/runs/123",
            extra_labels=[],
            dry_run=False
        )
        
        assert repo == "owner/repo"
        assert created is False


class TestMultiOrgAuth:
    """Tests for multi-org GitHub App token support."""

    def test_for_org_returns_self_without_app_creds(self):
        """Without App credentials, for_org returns the same client (PAT mode)."""
        client = GHClient(token="pat-token", dry_run=False)
        result = client.for_org("some-org")
        assert result is client
        assert result.token == "pat-token"

    def test_for_org_returns_self_with_empty_app_creds(self):
        """With empty App credentials, for_org returns the same client."""
        client = GHClient(token="pat-token", dry_run=False, app_id="", app_private_key="")
        result = client.for_org("some-org")
        assert result is client

    @mock.patch.object(GHClient, '_get_installation_for_org')
    @mock.patch('trufflehog_scanner.get_github_app_token')
    def test_for_org_gets_scoped_token(self, mock_get_token, mock_get_install):
        """for_org should look up installation ID and get an org-scoped token."""
        mock_get_install.return_value = 99999
        mock_get_token.return_value = "ghs_org_scoped_token"

        client = GHClient(token="default-token", dry_run=False,
                          app_id="12345", app_private_key="fake-key")
        result = client.for_org("SLAC")

        mock_get_install.assert_called_once_with("SLAC")
        mock_get_token.assert_called_once_with("12345", "fake-key", "99999")
        assert result.token == "ghs_org_scoped_token"
        assert result is not client  # Should be a new client

    @mock.patch.object(GHClient, '_get_installation_for_org')
    @mock.patch('trufflehog_scanner.get_github_app_token')
    def test_for_org_caches_token(self, mock_get_token, mock_get_install):
        """Repeated for_org calls for the same org should use cached token."""
        mock_get_install.return_value = 99999
        mock_get_token.return_value = "ghs_cached"

        client = GHClient(token="default-token", dry_run=False,
                          app_id="12345", app_private_key="fake-key")
        result1 = client.for_org("SLAC")
        result2 = client.for_org("SLAC")

        # Should only call the API once (cached on second call)
        mock_get_install.assert_called_once()
        mock_get_token.assert_called_once()
        assert result1.token == "ghs_cached"
        assert result2.token == "ghs_cached"

    @mock.patch.object(GHClient, '_get_installation_for_org')
    @mock.patch('trufflehog_scanner.get_github_app_token')
    def test_for_org_different_orgs_get_different_tokens(self, mock_get_token, mock_get_install):
        """Different orgs should get different installation lookups."""
        mock_get_install.side_effect = [111, 222]
        mock_get_token.side_effect = ["ghs_token_a", "ghs_token_b"]

        client = GHClient(token="default-token", dry_run=False,
                          app_id="12345", app_private_key="fake-key")
        result_a = client.for_org("org-a")
        result_b = client.for_org("org-b")

        assert result_a.token == "ghs_token_a"
        assert result_b.token == "ghs_token_b"
        assert mock_get_install.call_count == 2

    @mock.patch.object(GHClient, '_get_installation_for_org')
    def test_for_org_falls_back_on_error(self, mock_get_install):
        """If installation lookup fails, for_org returns the original client."""
        mock_get_install.side_effect = Exception("API error")

        client = GHClient(token="default-token", dry_run=False,
                          app_id="12345", app_private_key="fake-key")
        result = client.for_org("bad-org")

        assert result is client  # Falls back to original
        assert result.token == "default-token"

    def test_for_org_preserves_dry_run(self):
        """Child clients from for_org should preserve dry_run setting."""
        client = GHClient(token="token", dry_run=True,
                          app_id="12345", app_private_key="fake-key")
        with mock.patch.object(client, '_get_installation_for_org', return_value=100):
            with mock.patch('trufflehog_scanner.get_github_app_token', return_value="ghs_new"):
                result = client.for_org("test-org")
                assert result.dry_run is True


# ═══════════════════════════════════════════════════════════════════════════════
# New coverage: highest_severity, repo_is_actionable, load_findings,
# build_issue_body, write_markdown_summary, DETECTOR_REMEDIATION map
# ═══════════════════════════════════════════════════════════════════════════════


class TestHighestSeverity:
    """Tests for highest_severity function."""

    def test_single_critical(self):
        items = [{"detector": "AWS"}]
        assert highest_severity(items) == "sec:critical"

    def test_mixed_returns_highest(self):
        items = [
            {"detector": "Slack"},       # sec:medium
            {"detector": "GitHub"},      # sec:high
            {"detector": "AWS"},         # sec:critical
        ]
        assert highest_severity(items) == "sec:critical"

    def test_all_low(self):
        items = [{"detector": "SomeUnknown"}, {"detector": "AnotherUnknown"}]
        # Unknown detectors normalize to "Generic" → sec:medium default
        assert highest_severity(items) == "sec:medium"

    def test_empty_items_returns_low(self):
        assert highest_severity([]) == "sec:low"

    def test_high_without_critical(self):
        items = [
            {"detector": "GitHub"},     # sec:high
            {"detector": "Slack"},      # sec:medium
        ]
        assert highest_severity(items) == "sec:high"


class TestRepoIsActionable:
    """Tests for repo_is_actionable function."""

    def test_normal_repo(self):
        assert repo_is_actionable({"archived": False, "has_issues": True}) is True

    def test_archived_repo(self):
        assert repo_is_actionable({"archived": True}) is False

    def test_issues_disabled(self):
        assert repo_is_actionable({"archived": False, "has_issues": False}) is False

    def test_none_meta(self):
        assert repo_is_actionable(None) is False

    def test_empty_meta(self):
        assert repo_is_actionable({}) is False  # empty dict is falsy

    def test_missing_has_issues_defaults_true(self):
        # If has_issues is not in metadata, default to True (actionable)
        assert repo_is_actionable({"archived": False}) is True


class TestLoadFindings:
    """Tests for load_findings function."""

    def _write_ndjson(self, lines):
        """Helper: write NDJSON lines to a temp file and return path."""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.ndjson', delete=False)
        for line in lines:
            f.write(json.dumps(line) + "\n")
        f.close()
        return f.name

    def test_loads_verified_only_by_default(self):
        path = self._write_ndjson([
            {"DetectorName": "AWS", "Verified": True,
             "SourceMetadata": {"Data": {"Git": {"repository": "owner/repo", "file": "a.py"}}}},
            {"DetectorName": "Slack", "Verified": False,
             "SourceMetadata": {"Data": {"Git": {"repository": "owner/repo", "file": "b.py"}}}},
        ])
        try:
            result = load_findings(path, [])
            assert "owner/repo" in result
            assert len(result["owner/repo"]) == 1
            assert result["owner/repo"][0]["detector"] == "AWS"
        finally:
            os.unlink(path)

    def test_include_unverified(self):
        path = self._write_ndjson([
            {"DetectorName": "AWS", "Verified": True,
             "SourceMetadata": {"Data": {"Git": {"repository": "owner/repo", "file": "a.py"}}}},
            {"DetectorName": "Slack", "Verified": False,
             "SourceMetadata": {"Data": {"Git": {"repository": "owner/repo", "file": "b.py"}}}},
        ])
        try:
            result = load_findings(path, [], include_unverified=True)
            assert len(result["owner/repo"]) == 2
        finally:
            os.unlink(path)

    def test_deduplication(self):
        """Duplicate findings (same repo/detector/file/line/commit) should be collapsed."""
        finding = {"DetectorName": "AWS", "Verified": True,
                   "SourceMetadata": {"Data": {"Git": {
                       "repository": "owner/repo", "file": "a.py",
                       "line": 10, "commit": "abc123"}}}}
        path = self._write_ndjson([finding, finding, finding])
        try:
            result = load_findings(path, [])
            assert len(result["owner/repo"]) == 1
        finally:
            os.unlink(path)

    def test_exclude_regex(self):
        path = self._write_ndjson([
            {"DetectorName": "EXAMPLE_DETECTOR", "Verified": True, "Redacted": "test",
             "SourceMetadata": {"Data": {"Git": {"repository": "owner/repo", "file": "a.py"}}}},
        ])
        try:
            result = load_findings(path, [re.compile(r"EXAMPLE")])
            assert len(result) == 0
        finally:
            os.unlink(path)

    def test_allow_no_repo_groups_filesystem(self):
        """Findings without repo metadata should be grouped under _filesystem_."""
        path = self._write_ndjson([
            {"DetectorName": "AWS", "Verified": True, "SourceMetadata": {"Data": {}}},
        ])
        try:
            result = load_findings(path, [], allow_no_repo=True)
            assert "_filesystem_" in result
            assert len(result["_filesystem_"]) == 1
        finally:
            os.unlink(path)

    def test_no_repo_skipped_without_allow(self):
        path = self._write_ndjson([
            {"DetectorName": "AWS", "Verified": True, "SourceMetadata": {"Data": {}}},
        ])
        try:
            result = load_findings(path, [], allow_no_repo=False)
            assert len(result) == 0
        finally:
            os.unlink(path)

    def test_nonexistent_file(self):
        result = load_findings("/tmp/nonexistent_12345.ndjson", [])
        assert result == {}

    def test_items_have_redacted_and_verified(self):
        """Each loaded item should carry the redacted and verified fields."""
        path = self._write_ndjson([
            {"DetectorName": "AWS", "Verified": True, "Redacted": "AKIA***",
             "SourceMetadata": {"Data": {"Git": {"repository": "o/r", "file": "f.py"}}}},
        ])
        try:
            result = load_findings(path, [])
            item = result["o/r"][0]
            assert item["redacted"] == "AKIA***"
            assert item["verified"] is True
        finally:
            os.unlink(path)


class TestBuildIssueBody:
    """Tests for build_issue_body function."""

    def test_contains_severity_badge(self):
        items = [{"detector": "AWS", "file": "a.py", "line": 1, "commit": "abc", "verified": True}]
        body = build_issue_body("owner/repo", items, "https://github.com/runs/1")
        assert "CRITICAL" in body
        assert "🔴" in body

    def test_contains_findings_table(self):
        items = [{"detector": "AWS", "file": "a.py", "line": 10, "commit": "abc123", "verified": True}]
        body = build_issue_body("owner/repo", items, "https://github.com/runs/1")
        assert "| Detector | File |" in body
        assert "AWS" in body
        assert "`a.py`" in body

    def test_contains_remediation_steps(self):
        items = [{"detector": "AWS", "file": "a.py", "line": 1, "commit": "abc", "verified": True}]
        body = build_issue_body("owner/repo", items, "https://github.com/runs/1")
        assert "Remediation Steps" in body
        assert "Rotate/Revoke" in body

    def test_contains_detector_specific_remediation(self):
        items = [{"detector": "AWS", "file": "a.py", "line": 1, "commit": "abc", "verified": True}]
        body = build_issue_body("owner/repo", items, "https://github.com/runs/1")
        assert "AWS" in body
        # AWS detector remediation should have IAM-related content
        assert "IAM" in body or "aws" in body.lower()

    def test_findings_hash_included(self):
        items = [{"detector": "AWS", "file": "a.py", "line": 1, "commit": "abc", "verified": True}]
        body = build_issue_body("owner/repo", items, "https://github.com/runs/1", findings_hash="deadbeef")
        assert "deadbeef" in body
        assert "deduplication" in body

    def test_false_positive_guide(self):
        """Issue body should contain response options including false-positive label."""
        items = [{"detector": "AWS", "file": "a.py", "line": 1, "commit": "abc", "verified": True}]
        body = build_issue_body("owner/repo", items, "https://github.com/runs/1")
        assert "trufflehog:false-positive" in body
        assert "Response Options" in body

    def test_redacted_preview(self):
        items = [{"detector": "AWS", "file": "a.py", "line": 1, "commit": "abc",
                  "verified": True, "redacted": "AKIA***EXAMPLE"}]
        body = build_issue_body("owner/repo", items, "https://github.com/runs/1")
        assert "AKIA***EXAMPLE" in body

    def test_verified_checkmark(self):
        items = [{"detector": "AWS", "file": "a.py", "line": 1, "commit": "abc", "verified": True}]
        body = build_issue_body("owner/repo", items, "https://github.com/runs/1")
        assert "✅" in body

    def test_unverified_question_mark(self):
        items = [{"detector": "AWS", "file": "a.py", "line": 1, "commit": "abc", "verified": False}]
        body = build_issue_body("owner/repo", items, "https://github.com/runs/1")
        assert "❓" in body

    def test_scanner_repo_link(self):
        items = [{"detector": "AWS", "file": "a.py", "line": 1, "commit": "abc", "verified": True}]
        body = build_issue_body("owner/repo", items, "https://github.com/runs/1",
                                scanner_repo="org/trufflehog")
        assert "org/trufflehog" in body

    def test_multiple_items(self):
        items = [
            {"detector": "AWS", "file": "a.py", "line": 1, "commit": "abc", "verified": True},
            {"detector": "GitHub", "file": "b.py", "line": 5, "commit": "def", "verified": True},
        ]
        body = build_issue_body("owner/repo", items, "https://github.com/runs/1")
        # Should say "2 verified secrets" or "2 findings"
        assert "2" in body
        assert "AWS" in body
        assert "GitHub" in body

    def test_security_notice_present(self):
        items = [{"detector": "AWS", "file": "a.py", "line": 1, "commit": "abc", "verified": True}]
        body = build_issue_body("owner/repo", items, "https://github.com/runs/1")
        assert "SECURITY NOTICE" in body

    def test_blame_link_in_issue_body(self):
        items = [{"detector": "AWS", "file": "src/creds.py", "line": 42, "commit": "abc123", "verified": True}]
        body = build_issue_body("owner/repo", items, "https://github.com/runs/1")
        assert "[Blame](" in body
        assert "/blame/abc123/src/creds.py#L42)" in body

    def test_filesystem_links_use_target_repo_not_filesystem(self):
        """Items with original_repo='_filesystem_' should use the issue repo for links."""
        items = [{"detector": "AWS", "file": "/repo/secrets.txt", "line": 3, "commit": None,
                  "verified": False, "original_repo": "_filesystem_"}]
        body = build_issue_body("slac-it/trufflehog", items, "https://github.com/runs/1")
        assert "_filesystem_" not in body
        assert "slac-it/trufflehog" in body
        assert "[Blame](https://github.com/slac-it/trufflehog/blame/HEAD/secrets.txt#L3)" in body


class TestWriteMarkdownSummary:
    """Tests for write_markdown_summary function."""

    def _write_ndjson(self, lines):
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.ndjson', delete=False)
        for line in lines:
            f.write(json.dumps(line) + "\n")
        f.close()
        return f.name

    def test_writes_to_step_summary(self):
        """Summary should be written to GITHUB_STEP_SUMMARY file."""
        ndjson_path = self._write_ndjson([
            {"DetectorName": "AWS", "Verified": True,
             "SourceMetadata": {"Data": {"Git": {"repository": "owner/repo", "file": "a.py"}}}},
        ])
        summary_file = tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False)
        summary_file.close()
        try:
            with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": summary_file.name}):
                write_markdown_summary(ndjson_path, "test-org", [])
            with open(summary_file.name) as f:
                content = f.read()
            assert "test-org" in content
            assert "owner/repo" in content
        finally:
            os.unlink(ndjson_path)
            os.unlink(summary_file.name)

    def test_severity_breakdown_table(self):
        """Summary should include severity breakdown with Verified/Unverified columns."""
        ndjson_path = self._write_ndjson([
            {"DetectorName": "AWS", "Verified": True,
             "SourceMetadata": {"Data": {"Git": {"repository": "owner/repo", "file": "a.py"}}}},
            {"DetectorName": "Slack", "Verified": False,
             "SourceMetadata": {"Data": {"Git": {"repository": "owner/repo", "file": "b.py"}}}},
        ])
        summary_file = tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False)
        summary_file.close()
        try:
            with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": summary_file.name}):
                write_markdown_summary(ndjson_path, "test-org", [])
            with open(summary_file.name) as f:
                content = f.read()
            assert "Findings by severity" in content
            assert "Verified" in content
            assert "Unverified" in content
        finally:
            os.unlink(ndjson_path)
            os.unlink(summary_file.name)

    def test_empty_ndjson(self):
        """Empty NDJSON should produce a summary with zero counts."""
        ndjson_path = self._write_ndjson([])
        summary_file = tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False)
        summary_file.close()
        try:
            with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": summary_file.name}):
                write_markdown_summary(ndjson_path, "test-org", [])
            with open(summary_file.name) as f:
                content = f.read()
            assert "**0**" in content
        finally:
            os.unlink(ndjson_path)
            os.unlink(summary_file.name)

    def test_filter_statistics(self):
        """Summary should show excluded count when exclusions apply."""
        ndjson_path = self._write_ndjson([
            {"DetectorName": "EXAMPLE_DETECTOR", "Verified": True, "Redacted": "test",
             "SourceMetadata": {"Data": {"Git": {"repository": "owner/repo", "file": "a.py"}}}},
            {"DetectorName": "AWS", "Verified": True,
             "SourceMetadata": {"Data": {"Git": {"repository": "owner/repo", "file": "b.py"}}}},
        ])
        summary_file = tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False)
        summary_file.close()
        try:
            with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": summary_file.name}):
                write_markdown_summary(ndjson_path, "test-org", [re.compile(r"EXAMPLE")])
            with open(summary_file.name) as f:
                content = f.read()
            assert "false-positive" in content.lower() or "Filtered" in content
        finally:
            os.unlink(ndjson_path)
            os.unlink(summary_file.name)


    def test_unverified_repo_detector_crosstab(self):
        """Summary should include unverified repo × detector cross-tab table."""
        ndjson_path = self._write_ndjson([
            {"DetectorName": "AWS", "Verified": False,
             "SourceMetadata": {"Data": {"Git": {"repository": "org/repo-a", "file": "a.py"}}}},
            {"DetectorName": "Slack", "Verified": False,
             "SourceMetadata": {"Data": {"Git": {"repository": "org/repo-a", "file": "b.py"}}}},
            {"DetectorName": "AWS", "Verified": False,
             "SourceMetadata": {"Data": {"Git": {"repository": "org/repo-b", "file": "c.py"}}}},
            {"DetectorName": "GitHub", "Verified": True,
             "SourceMetadata": {"Data": {"Git": {"repository": "org/repo-a", "file": "d.py"}}}},
        ])
        summary_file = tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False)
        summary_file.close()
        try:
            with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": summary_file.name}):
                write_markdown_summary(ndjson_path, "test-org", [])
            with open(summary_file.name) as f:
                content = f.read()
            assert "repo × detector" in content
            assert "org/repo-a" in content
            assert "org/repo-b" in content
            # The cross-tab should show repo-a with AWS and Slack, repo-b with AWS
            assert "AWS" in content
            assert "Slack" in content
        finally:
            os.unlink(ndjson_path)
            os.unlink(summary_file.name)

    def test_by_repo_table_shows_collapsible_for_many_repos(self):
        """When >50 repos have findings, excess should be in a collapsible section."""
        findings = []
        for i in range(60):
            findings.append({
                "DetectorName": "AWS", "Verified": False,
                "SourceMetadata": {"Data": {"Git": {"repository": f"org/repo-{i:03d}", "file": "a.py"}}}
            })
        ndjson_path = self._write_ndjson(findings)
        summary_file = tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False)
        summary_file.close()
        try:
            with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": summary_file.name}):
                write_markdown_summary(ndjson_path, "test-org", [])
            with open(summary_file.name) as f:
                content = f.read()
            assert "Show remaining 10 repositories" in content
        finally:
            os.unlink(ndjson_path)
            os.unlink(summary_file.name)

    def test_filesystem_findings_with_target_repo(self):
        """Filesystem scan findings (no repo metadata) should appear in summary when target_repo is set."""
        ndjson_path = self._write_ndjson([
            {"DetectorName": "Postgres", "Verified": False,
             "SourceMetadata": {"Data": {"Filesystem": {"file": "/repo/.env.example", "line": 24}}}},
            {"DetectorName": "AWS", "Verified": False,
             "SourceMetadata": {"Data": {"Filesystem": {"file": "/repo/secrets.txt", "line": 3}}}},
            {"DetectorName": "SlackWebhook", "Verified": False,
             "SourceMetadata": {"Data": {"Filesystem": {"file": "/repo/config.py", "line": 10}}}},
        ])
        summary_file = tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False)
        summary_file.close()
        try:
            with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": summary_file.name}):
                write_markdown_summary(ndjson_path, "self-scan", [], target_repo="slac-it/trufflehog")
            with open(summary_file.name) as f:
                content = f.read()
            # All 3 findings should be counted
            assert "**3**" in content
            assert "Unverified (informational) | **3**" in content
            # The target repo should be used as the repo name
            assert "slac-it/trufflehog" in content
            # Detectors should appear
            assert "Postgres" in content
            assert "AWS" in content
            assert "SlackWebhook" in content
            # Without target_repo, findings would be skipped (0 count)
        finally:
            os.unlink(ndjson_path)
            os.unlink(summary_file.name)

    def test_filesystem_findings_without_target_repo_skipped(self):
        """Filesystem scan findings without target_repo should be skipped (no repo = skip)."""
        ndjson_path = self._write_ndjson([
            {"DetectorName": "Postgres", "Verified": False,
             "SourceMetadata": {"Data": {"Filesystem": {"file": "/repo/.env", "line": 1}}}},
        ])
        summary_file = tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False)
        summary_file.close()
        try:
            with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": summary_file.name}):
                write_markdown_summary(ndjson_path, "self-scan", [])
            with open(summary_file.name) as f:
                content = f.read()
            # Finding should be skipped — 0 total
            assert "**0**" in content
        finally:
            os.unlink(ndjson_path)
            os.unlink(summary_file.name)

    def test_filesystem_file_path_and_line_no(self):
        """file_path() and line_no() should extract Filesystem metadata."""
        obj = {"SourceMetadata": {"Data": {"Filesystem": {"file": "/repo/test.py", "line": 42}}}}
        assert file_path(obj) == "/repo/test.py"
        assert line_no(obj) == 42


class TestDetectorRemediationMap:
    """Validate DETECTOR_REMEDIATION and DETECTOR_SEVERITY coverage."""

    def test_all_severity_detectors_have_remediation(self):
        """Most detectors in DETECTOR_SEVERITY should have remediation guidance."""
        # These detectors are recognized but don't yet have specific remediation steps
        known_exceptions = {"Generic", "Private Key", "Docker", "JWT", "Mailchimp"}
        for det in DETECTOR_SEVERITY:
            if det not in known_exceptions:
                assert det in DETECTOR_REMEDIATION, \
                    f"Detector '{det}' has severity mapping but no remediation guidance"

    def test_remediation_values_are_nonempty(self):
        for det, guidance in DETECTOR_REMEDIATION.items():
            assert isinstance(guidance, str), f"Remediation for '{det}' is not a string"
            assert len(guidance.strip()) > 10, f"Remediation for '{det}' is too short"

    def test_severity_values_are_valid(self):
        valid_severities = {"sec:critical", "sec:high", "sec:medium", "sec:low"}
        for det, sev in DETECTOR_SEVERITY.items():
            assert sev in valid_severities, \
                f"Detector '{det}' has invalid severity '{sev}'"


# ---- Suppression labels and feedback loop tests ----

class TestSuppressionConstants:
    """Verify suppression label constants are well-formed."""

    def test_suppression_labels_have_required_keys(self):
        for label_name, props in SUPPRESSION_LABELS.items():
            assert "color" in props, f"{label_name} missing 'color'"
            assert "description" in props, f"{label_name} missing 'description'"
            assert isinstance(props["color"], str) and len(props["color"]) == 6, \
                f"{label_name} color should be 6-char hex"

    def test_suppression_label_names_match(self):
        assert SUPPRESSION_LABEL_NAMES == set(SUPPRESSION_LABELS.keys())

    def test_comment_commands_map_to_valid_labels(self):
        for cmd, label in COMMENT_COMMANDS.items():
            assert cmd.startswith("/trufflehog "), f"Command '{cmd}' should start with '/trufflehog '"
            assert label in SUPPRESSION_LABELS, f"Command '{cmd}' maps to unknown label '{label}'"

    def test_expected_suppression_labels_exist(self):
        expected = {"trufflehog:false-positive", "trufflehog:accepted-risk",
                    "trufflehog:wont-fix", "trufflehog:remediated"}
        assert expected == SUPPRESSION_LABEL_NAMES


class TestGetSuppressedHashes:
    """Tests for GHClient.get_suppressed_hashes()."""

    @mock.patch.object(GHClient, 'call')
    def test_returns_empty_when_no_suppressed_issues(self, mock_call):
        """No issues with suppression labels → empty set."""
        mock_call.return_value = []
        client = GHClient(token="fake-token", dry_run=False)
        result = client.get_suppressed_hashes("owner/repo")
        assert result == set()

    @mock.patch.object(GHClient, 'call')
    def test_extracts_hash_from_suppressed_issue(self, mock_call):
        """Issue with false-positive label → hash extracted from title."""
        mock_call.return_value = [
            {"title": "[TruffleHog] Secrets scan report (ab12cd34)", "state": "open",
             "labels": [{"name": "trufflehog:false-positive"}]},
        ]
        client = GHClient(token="fake-token", dry_run=False)
        result = client.get_suppressed_hashes("owner/repo")
        assert "ab12cd34" in result

    @mock.patch.object(GHClient, 'call')
    def test_ignores_non_trufflehog_issues(self, mock_call):
        """Issues without [TruffleHog] in title should be ignored."""
        mock_call.return_value = [
            {"title": "Some other issue (ab12cd34)", "state": "open"},
        ]
        client = GHClient(token="fake-token", dry_run=False)
        result = client.get_suppressed_hashes("owner/repo")
        assert result == set()

    @mock.patch.object(GHClient, 'call')
    def test_extracts_multiple_hashes(self, mock_call):
        """Multiple suppressed issues → multiple hashes."""
        mock_call.return_value = [
            {"title": "[TruffleHog] Secrets scan report (aaaa1111)", "state": "open"},
            {"title": "[TruffleHog] Secrets scan report (bbbb2222)", "state": "closed"},
        ]
        client = GHClient(token="fake-token", dry_run=False)
        result = client.get_suppressed_hashes("owner/repo")
        assert "aaaa1111" in result
        assert "bbbb2222" in result

    @mock.patch.object(GHClient, 'call')
    def test_handles_api_error_gracefully(self, mock_call):
        """HTTP error during label search → returns empty set, no crash."""
        mock_call.side_effect = HTTPError(
            "https://api.github.com", 403, "Forbidden", {}, None
        )
        client = GHClient(token="fake-token", dry_run=False)
        result = client.get_suppressed_hashes("owner/repo")
        assert result == set()

    @mock.patch.object(GHClient, 'call')
    def test_ignores_invalid_hash_format(self, mock_call):
        """Titles without valid 8-char hex hash are skipped."""
        mock_call.return_value = [
            {"title": "[TruffleHog] Secrets scan report (not-a-hash)", "state": "open"},
            {"title": "[TruffleHog] Secrets scan report", "state": "open"},
        ]
        client = GHClient(token="fake-token", dry_run=False)
        result = client.get_suppressed_hashes("owner/repo")
        assert result == set()


class TestProcessCommentCommands:
    """Tests for GHClient.process_comment_commands()."""

    def test_dry_run_skips_processing(self):
        """Dry run mode should not process any comments."""
        client = GHClient(token="fake-token", dry_run=True)
        result = client.process_comment_commands("owner/repo")
        assert result == 0

    @mock.patch.object(GHClient, 'call')
    def test_applies_label_from_comment_command(self, mock_call):
        """Comment with /trufflehog false-positive → label applied."""
        # First call: list issues, Second: list comments, Third: apply label
        mock_call.side_effect = [
            # List issues (page 1)
            [{"title": "[TruffleHog] Secrets scan report (ab12cd34)",
              "number": 42, "labels": [], "state": "open"}],
            # List comments for issue 42
            [{"body": "/trufflehog false-positive", "user": {"login": "testuser"}}],
            # Apply label response
            [{"name": "trufflehog:false-positive"}],
        ]
        client = GHClient(token="fake-token", dry_run=False)
        result = client.process_comment_commands("owner/repo")
        assert result == 1
        # Verify POST call to add label
        label_call = mock_call.call_args_list[2]
        assert "labels" in str(label_call)

    @mock.patch.object(GHClient, 'call')
    def test_skips_already_labeled_issues(self, mock_call):
        """Issue already has the suppression label → no action."""
        mock_call.side_effect = [
            # List issues (page 1)
            [{"title": "[TruffleHog] Secrets scan report (ab12cd34)",
              "number": 42, "labels": [{"name": "trufflehog:false-positive"}], "state": "open"}],
            # List comments for issue 42
            [{"body": "/trufflehog false-positive", "user": {"login": "testuser"}}],
        ]
        client = GHClient(token="fake-token", dry_run=False)
        result = client.process_comment_commands("owner/repo")
        assert result == 0  # No labels applied (already exists)

    @mock.patch.object(GHClient, 'call')
    def test_ignores_non_trufflehog_issues(self, mock_call):
        """Issues without [TruffleHog] prefix are skipped."""
        mock_call.return_value = [
            {"title": "Some other issue", "number": 1, "labels": [], "state": "open"},
        ]
        client = GHClient(token="fake-token", dry_run=False)
        result = client.process_comment_commands("owner/repo")
        assert result == 0

    @mock.patch.object(GHClient, 'call')
    def test_handles_no_open_issues(self, mock_call):
        """No open issues → nothing to process."""
        mock_call.return_value = []
        client = GHClient(token="fake-token", dry_run=False)
        result = client.process_comment_commands("owner/repo")
        assert result == 0


class TestCreateSuppressionLabels:
    """Tests for GHClient.create_suppression_labels()."""

    @mock.patch.object(GHClient, 'call')
    def test_creates_all_suppression_labels(self, mock_call):
        """Should attempt to create all suppression labels."""
        mock_call.return_value = {"name": "test"}
        client = GHClient(token="fake-token", dry_run=False)
        client.create_suppression_labels("owner/repo")
        assert mock_call.call_count == len(SUPPRESSION_LABELS)

    @mock.patch.object(GHClient, 'call')
    def test_handles_existing_labels_gracefully(self, mock_call):
        """422 response (label exists) should not raise."""
        mock_call.side_effect = HTTPError(
            "https://api.github.com", 422, "Unprocessable", {}, None
        )
        client = GHClient(token="fake-token", dry_run=False)
        # Should not raise
        client.create_suppression_labels("owner/repo")

    @mock.patch.object(GHClient, 'call')
    def test_label_payloads_include_color_and_description(self, mock_call):
        """Each label creation should include color and description."""
        mock_call.return_value = {"name": "test"}
        client = GHClient(token="fake-token", dry_run=False)
        client.create_suppression_labels("owner/repo")
        for call_args in mock_call.call_args_list:
            payload = call_args[0][2]  # Third positional arg is the body
            assert "color" in payload
            assert "description" in payload
            assert "name" in payload


class TestProcessRepoSuppression:
    """Tests for suppression label integration in process_repo."""

    @mock.patch.object(GHClient, 'for_org')
    @mock.patch.object(GHClient, 'repo_meta')
    @mock.patch.object(GHClient, 'get_suppressed_hashes')
    @mock.patch.object(GHClient, 'process_comment_commands')
    def test_suppressed_hash_skips_issue_creation(self, mock_comments, mock_suppressed,
                                                    mock_meta, mock_for_org):
        """If the findings hash is in the suppressed set, issue creation is skipped."""
        from trufflehog_scanner import process_repo, compute_findings_hash

        items = [{"detector": "AWS", "file": "config.py", "line": 10, "commit": "abc123"}]
        expected_hash = compute_findings_hash(items)

        mock_meta.return_value = {"archived": False, "disabled": False}
        mock_suppressed.return_value = {expected_hash}  # This hash is suppressed
        mock_comments.return_value = 0

        client = GHClient(token="fake-token", dry_run=False)
        mock_for_org.return_value = client

        repo, created = process_repo(
            repo="owner/repo",
            items=items,
            gh=client,
            title_prefix="[TruffleHog]",
            run_url="https://github.com/owner/repo/actions/runs/123",
            extra_labels=[],
            dry_run=False
        )

        assert repo == "owner/repo"
        assert created is False  # Issue should NOT be created

    @mock.patch.object(GHClient, 'for_org')
    @mock.patch.object(GHClient, 'repo_meta')
    @mock.patch.object(GHClient, 'search_issue_by_title')
    @mock.patch.object(GHClient, 'search_recent_issues_with_hash')
    @mock.patch.object(GHClient, 'create_issue')
    @mock.patch.object(GHClient, 'create_labels_if_needed')
    @mock.patch.object(GHClient, 'get_suppressed_hashes')
    @mock.patch.object(GHClient, 'process_comment_commands')
    @mock.patch.object(GHClient, 'create_suppression_labels')
    def test_non_suppressed_hash_allows_issue_creation(self, mock_supp_labels, mock_comments, mock_suppressed,
                                                         mock_labels, mock_create, mock_hash_search,
                                                         mock_title_search, mock_meta, mock_for_org):
        """If the findings hash is NOT suppressed, issue creation proceeds normally."""
        from trufflehog_scanner import process_repo

        mock_meta.return_value = {"archived": False, "disabled": False}
        mock_suppressed.return_value = {"different_hash"}  # Different hash suppressed
        mock_comments.return_value = 0
        mock_title_search.return_value = False
        mock_hash_search.return_value = False
        mock_create.return_value = {"html_url": "https://github.com/owner/repo/issues/1", "number": 1}
        mock_labels.return_value = None
        mock_supp_labels.return_value = None

        client = GHClient(token="fake-token", dry_run=False)
        mock_for_org.return_value = client
        items = [{"detector": "AWS", "file": "config.py", "line": 10, "commit": "abc123"}]

        repo, created = process_repo(
            repo="owner/repo",
            items=items,
            gh=client,
            title_prefix="[TruffleHog]",
            run_url="https://github.com/owner/repo/actions/runs/123",
            extra_labels=[],
            dry_run=False
        )

        assert repo == "owner/repo"
        assert created is True


class TestBuildIssueBodyResponseOptions:
    """Tests for the Response Options section in build_issue_body."""

    def test_issue_body_contains_response_options_section(self):
        items = [{"detector": "AWS", "file": "config.py", "line": 10, "commit": "abc123", "verified": True}]
        body = build_issue_body("owner/repo", items, "https://run.url", findings_hash="ab12cd34")
        assert "Response Options" in body

    def test_issue_body_contains_suppression_labels(self):
        items = [{"detector": "AWS", "file": "config.py", "line": 10, "commit": "abc123", "verified": True}]
        body = build_issue_body("owner/repo", items, "https://run.url", findings_hash="ab12cd34")
        assert "trufflehog:false-positive" in body
        assert "trufflehog:accepted-risk" in body
        assert "trufflehog:wont-fix" in body
        assert "trufflehog:remediated" in body

    def test_issue_body_contains_comment_commands(self):
        items = [{"detector": "AWS", "file": "config.py", "line": 10, "commit": "abc123", "verified": True}]
        body = build_issue_body("owner/repo", items, "https://run.url", findings_hash="ab12cd34")
        assert "/trufflehog false-positive" in body
        assert "/trufflehog accepted-risk" in body
        assert "/trufflehog wont-fix" in body
        assert "/trufflehog remediated" in body

    def test_issue_body_contains_manual_suppression(self):
        items = [{"detector": "AWS", "file": "config.py", "line": 10, "commit": "abc123", "verified": True}]
        body = build_issue_body("owner/repo", items, "https://run.url", findings_hash="ab12cd34")
        assert "false_positives.txt" in body
        assert "Manual Suppression" in body

    def test_issue_body_still_contains_remediation(self):
        """The Response Options section should coexist with remediation guidance."""
        items = [{"detector": "AWS", "file": "config.py", "line": 10, "commit": "abc123", "verified": True}]
        body = build_issue_body("owner/repo", items, "https://run.url", findings_hash="ab12cd34")
        assert "Remediation Steps" in body
        assert "Rotate/Revoke" in body
        assert "Additional Resources" in body


from urllib.error import HTTPError


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
