"""
Contract tests for process_repo (the ThreadPoolExecutor worker).

Pins per-repo lifecycle branches:
  * Non-actionable repo (archived/missing meta) -> (repo, None)
  * for_org() called when repo has 'org/' prefix
  * Comment-command processing exception swallowed + continues
  * Suppression hash hit -> (repo, None)
  * Exact-title dedup hit -> (repo, None)
  * Hash dedup hit -> (repo, None)
  * force_create skips all three dedup checks
  * create_labels_if_needed HTTPError swallowed -> body still built
  * create_suppression_labels generic exception swallowed -> body still built
  * Successful create + verify -> (repo, True)
  * Verify failure -> (repo, False)
  * No issue number returned -> (repo, False) unless dry_run
  * Dry-run with no number -> (repo, True)
  * HTTPError raised by create_issue -> (repo, False)
"""
from __future__ import annotations

import io
from unittest import mock
from urllib.error import HTTPError

import pytest

import trufflehog_scanner as ts


def _http_error(code: int, msg: str = "err") -> HTTPError:
    return HTTPError(
        url="https://api.github.com/x",
        code=code,
        msg=msg,
        hdrs={},
        fp=io.BytesIO(b""),
    )


@pytest.fixture
def gh():
    """Stub GHClient with all the methods process_repo touches."""
    client = mock.MagicMock(spec=ts.GHClient)
    # for_org returns self by default so calls keep using same mock
    client.for_org.return_value = client
    client.dry_run = False
    # Default happy-path returns
    client.repo_meta.return_value = {"archived": False, "fork": False}
    client.get_suppressed_hashes.return_value = set()
    client.search_issue_by_title.return_value = False
    client.search_recent_issues_with_hash.return_value = False
    client.create_issue.return_value = {"number": 42}
    client.verify_issue_exists.return_value = True
    return client


@pytest.fixture
def items():
    return [
        {
            "DetectorName": "AWS",
            "Verified": True,
            "SourceMetadata": {
                "Data": {"Git": {"file": "x.txt", "line": 1, "commit": "abc"}}
            },
            "Raw": "secret",
        }
    ]


class TestNonActionableRepoShortCircuit:
    def test_archived_repo_returns_none(self, gh, items):
        gh.repo_meta.return_value = {"archived": True}
        result = ts.process_repo("o/r", items, gh, "[TH]", "run", None, False)
        assert result == ("o/r", None)
        # Pin: short-circuit BEFORE comment-commands, dedup, create
        gh.process_comment_commands.assert_not_called()
        gh.create_issue.assert_not_called()

    def test_missing_repo_meta_returns_none(self, gh, items):
        gh.repo_meta.return_value = None
        assert ts.process_repo("o/r", items, gh, "[TH]", "", None, False) == (
            "o/r",
            None,
        )


class TestForOrgAuthScope:
    def test_for_org_called_with_owner_when_slash_in_repo(self, gh, items):
        # Pin: GitHub App auth needs per-org installation token
        ts.process_repo("myorg/myrepo", items, gh, "[TH]", "", None, False)
        gh.for_org.assert_called_once_with("myorg")

    def test_for_org_skipped_when_no_slash(self, gh, items):
        gh.repo_meta.return_value = None  # short-circuit fast
        ts.process_repo("plainname", items, gh, "[TH]", "", None, False)
        gh.for_org.assert_not_called()


class TestCommentCommandException:
    def test_exception_swallowed_processing_continues(self, gh, items):
        gh.process_comment_commands.side_effect = RuntimeError("boom")
        # Pin: comment-command failure must NOT poison issue creation
        result = ts.process_repo("o/r", items, gh, "[TH]", "", None, False)
        assert result == ("o/r", True)
        gh.create_issue.assert_called_once()


class TestDedupShortCircuits:
    def test_suppression_hash_hit_returns_none(self, gh, items):
        # Make any computed hash a suppressed one
        gh.get_suppressed_hashes.return_value = {
            ts.compute_findings_hash(items)
        }
        result = ts.process_repo("o/r", items, gh, "[TH]", "", None, False)
        assert result == ("o/r", None)
        gh.search_issue_by_title.assert_not_called()
        gh.create_issue.assert_not_called()

    def test_exact_title_dedup_hit_returns_none(self, gh, items):
        gh.search_issue_by_title.return_value = True
        result = ts.process_repo("o/r", items, gh, "[TH]", "", None, False)
        assert result == ("o/r", None)
        # Pin: hash search SKIPPED once exact-title hits
        gh.search_recent_issues_with_hash.assert_not_called()
        gh.create_issue.assert_not_called()

    def test_hash_search_dedup_hit_returns_none(self, gh, items):
        gh.search_recent_issues_with_hash.return_value = True
        result = ts.process_repo("o/r", items, gh, "[TH]", "", None, False)
        assert result == ("o/r", None)
        gh.create_issue.assert_not_called()

    def test_force_create_skips_all_dedup(self, gh, items):
        # Even with suppression + exact match, force_create proceeds
        gh.get_suppressed_hashes.return_value = {
            ts.compute_findings_hash(items)
        }
        gh.search_issue_by_title.return_value = True
        gh.search_recent_issues_with_hash.return_value = True
        result = ts.process_repo(
            "o/r", items, gh, "[TH]", "", None, False, force_create=True
        )
        # Pin: force_create bypass for self-test runs
        assert result == ("o/r", True)
        gh.get_suppressed_hashes.assert_not_called()
        gh.search_issue_by_title.assert_not_called()
        gh.create_issue.assert_called_once()


class TestLabelCreationFailures:
    def test_create_labels_http_error_swallowed_continues(self, gh, items):
        gh.create_labels_if_needed.side_effect = _http_error(403)
        # Pin: 403 on labels endpoint must NOT abort issue creation
        result = ts.process_repo("o/r", items, gh, "[TH]", "", None, False)
        assert result == ("o/r", True)
        gh.create_issue.assert_called_once()

    def test_create_suppression_labels_exception_swallowed(self, gh, items):
        gh.create_suppression_labels.side_effect = RuntimeError("boom")
        # Pin: suppression-label failure also non-blocking
        result = ts.process_repo("o/r", items, gh, "[TH]", "", None, False)
        assert result == ("o/r", True)
        gh.create_issue.assert_called_once()


class TestIssueCreationOutcomes:
    def test_successful_create_and_verify_returns_true(self, gh, items):
        gh.create_issue.return_value = {"number": 99}
        gh.verify_issue_exists.return_value = True
        assert ts.process_repo(
            "o/r", items, gh, "[TH]", "", None, False
        ) == ("o/r", True)
        gh.verify_issue_exists.assert_called_once_with("o/r", 99)

    def test_create_returns_number_but_verify_fails_returns_false(
        self, gh, items
    ):
        gh.create_issue.return_value = {"number": 99}
        gh.verify_issue_exists.return_value = False
        # Pin: missing post-creation issue is treated as a creation failure
        assert ts.process_repo(
            "o/r", items, gh, "[TH]", "", None, False
        ) == ("o/r", False)

    def test_create_returns_no_number_real_run_returns_false(
        self, gh, items
    ):
        gh.create_issue.return_value = {}
        # Pin: empty response from create_issue (no number) = failure
        assert ts.process_repo(
            "o/r", items, gh, "[TH]", "", None, False
        ) == ("o/r", False)

    def test_create_returns_no_number_dry_run_returns_true(self, gh, items):
        gh.create_issue.return_value = {}
        gh.dry_run = True
        # Pin: dry-run paints "would create" as success
        assert ts.process_repo(
            "o/r", items, gh, "[TH]", "", None, True
        ) == ("o/r", True)
        gh.verify_issue_exists.assert_not_called()

    def test_create_issue_http_error_returns_false(self, gh, items):
        gh.create_issue.side_effect = _http_error(500)
        # Pin: HTTPError from create_issue logged + (repo, False)
        assert ts.process_repo(
            "o/r", items, gh, "[TH]", "", None, False
        ) == ("o/r", False)

    def test_create_returns_zero_number_returns_false(self, gh, items):
        # number=0 fails the `> 0` guard
        gh.create_issue.return_value = {"number": 0}
        assert ts.process_repo(
            "o/r", items, gh, "[TH]", "", None, False
        ) == ("o/r", False)

    def test_create_returns_non_int_number_returns_false(self, gh, items):
        gh.create_issue.return_value = {"number": "not-an-int"}
        # Pin: non-int issue number rejected (defends against API drift)
        assert ts.process_repo(
            "o/r", items, gh, "[TH]", "", None, False
        ) == ("o/r", False)
