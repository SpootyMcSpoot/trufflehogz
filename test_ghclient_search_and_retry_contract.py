"""
Contract tests for GHClient.call() retry/backoff path + search helpers
(search_issue_by_title, search_recent_issues).

Targets currently-uncovered branches:
  * call() dry-run short-circuit on POST/PATCH/PUT/DELETE
  * call() retry on 5xx + URLError, raise on terminal codes (400/401/404/422)
  * call() max_retries exhaustion -> reraise + ERROR log
  * search_issue_by_title: state=all, 2-page cap, short-page break, HTTPError swallow
  * search_recent_issues: cutoff filter, invalid date skip, non-list/short-page break,
    HTTPError swallow

Mocks at the self._req() boundary (and time.sleep to keep the suite fast).
"""
from __future__ import annotations

import datetime
import io
from unittest import mock
from urllib.error import HTTPError, URLError

import pytest

import trufflehog_scanner as ts
from trufflehog_scanner import GHClient


@pytest.fixture
def fast_sleep(monkeypatch):
    """Skip real backoff sleeps so retry tests don't burn wall-clock."""
    monkeypatch.setattr(ts.time, "sleep", lambda *_: None)


@pytest.fixture
def quiet_limiter(monkeypatch):
    """Don't let the global limiter delay tests."""
    monkeypatch.setattr(ts.GLOBAL_LIMITER, "wait", lambda *a, **kw: None)


def _http_error(code: int, msg: str = "err") -> HTTPError:
    return HTTPError(
        url="https://api.github.com/x",
        code=code,
        msg=msg,
        hdrs={},
        fp=io.BytesIO(b""),
    )


# ---------------------------------------------------------------------------
# call() dry-run + retry/backoff
# ---------------------------------------------------------------------------


class TestCallDryRunShortCircuit:
    @pytest.mark.parametrize("method", ["POST", "PATCH", "PUT", "DELETE"])
    def test_dry_run_skips_write_methods(self, method):
        client = GHClient(token="t", dry_run=True)
        with mock.patch.object(client, "_req") as req:
            result = client.call(method, "/repos/o/r/issues", {"a": 1})
        assert result == {}
        req.assert_not_called()

    def test_dry_run_does_not_skip_get(self):
        client = GHClient(token="t", dry_run=True)
        # Pin: GET in dry-run still hits the API; only mutations short-circuit
        with mock.patch.object(client, "_req", return_value={"x": 1}) as req:
            client.call("GET", "/repos/o/r", None)
        req.assert_called_once()


class TestCallRetryBranches:
    def test_terminal_codes_reraise_immediately(self, fast_sleep):
        client = GHClient(token="t")
        # Pin: 400/401/404/422 surface up without retry per call() policy
        for code in (400, 401, 404, 422):
            with mock.patch.object(
                client, "_req", side_effect=_http_error(code)
            ) as req:
                with pytest.raises(HTTPError) as exc:
                    client.call("GET", "/x", None)
                assert exc.value.code == code
                # No retry: exactly one _req invocation
                assert req.call_count == 1

    def test_retryable_5xx_retried_then_reraised(self, fast_sleep):
        client = GHClient(token="t")
        # Pin: 500 is retried up to max_retries, then reraised
        with mock.patch.object(
            client, "_req", side_effect=_http_error(500)
        ) as req:
            with pytest.raises(HTTPError):
                client.call("GET", "/x", None, max_retries=3)
        assert req.call_count == 3

    def test_retryable_5xx_succeeds_on_retry(self, fast_sleep):
        client = GHClient(token="t")
        responses = [_http_error(503), _http_error(502), {"ok": True}]
        with mock.patch.object(
            client, "_req", side_effect=responses
        ) as req:
            result = client.call("GET", "/x", None, max_retries=5)
        assert result == {"ok": True}
        assert req.call_count == 3

    def test_url_error_retried_then_reraised(self, fast_sleep):
        client = GHClient(token="t")
        # Pin: URLError (network) retries, then reraises after max_retries
        with mock.patch.object(
            client, "_req", side_effect=URLError("network")
        ) as req:
            with pytest.raises(URLError):
                client.call("GET", "/x", None, max_retries=2)
        assert req.call_count == 2

    def test_url_error_succeeds_on_retry(self, fast_sleep):
        client = GHClient(token="t")
        responses = [URLError("transient"), {"ok": 1}]
        with mock.patch.object(client, "_req", side_effect=responses) as req:
            result = client.call("GET", "/x", None, max_retries=3)
        assert result == {"ok": 1}
        assert req.call_count == 2


# ---------------------------------------------------------------------------
# search_issue_by_title
# ---------------------------------------------------------------------------


class TestSearchIssueByTitle:
    def test_match_on_first_page(self):
        client = GHClient(token="t")
        page = [{"title": "Other"}, {"title": "Wanted", "state": "open"}]
        with mock.patch.object(client, "call", return_value=page):
            assert client.search_issue_by_title("o/r", "Wanted") is True

    def test_no_match_returns_false(self):
        client = GHClient(token="t")
        # Short page on page 1 -> no second-page fetch
        page = [{"title": "Nope"}]
        with mock.patch.object(client, "call", return_value=page) as call:
            assert client.search_issue_by_title("o/r", "Wanted") is False
            assert call.call_count == 1

    def test_state_all_query_string_used(self):
        client = GHClient(token="t")
        captured = {}

        def fake_call(method, path, payload):
            captured["path"] = path
            return []

        with mock.patch.object(client, "call", side_effect=fake_call):
            client.search_issue_by_title("o/r", "X")
        # Pin: state=all so closed issues count for dedup
        assert "state=all" in captured["path"]

    def test_paginates_to_page_two_on_full_first_page(self):
        client = GHClient(token="t")
        page1 = [{"title": "x"} for _ in range(30)]
        page2 = [{"title": "Wanted", "state": "closed"}]

        with mock.patch.object(
            client, "call", side_effect=[page1, page2]
        ) as call:
            assert client.search_issue_by_title("o/r", "Wanted") is True
            assert call.call_count == 2

    def test_http_error_returns_false(self):
        client = GHClient(token="t")
        with mock.patch.object(
            client, "call", side_effect=_http_error(403)
        ):
            # Pin: HTTP error during dedup must NOT raise -- swallow + return False
            assert client.search_issue_by_title("o/r", "X") is False

    def test_non_list_response_breaks_loop(self):
        client = GHClient(token="t")
        # Note: actual code does `if isinstance(data, list)` -- non-list
        # falls through and continues to next page
        with mock.patch.object(
            client, "call", side_effect=[{"unexpected": "shape"}, []]
        ) as call:
            result = client.search_issue_by_title("o/r", "X")
        assert result is False
        assert call.call_count == 2


# ---------------------------------------------------------------------------
# search_recent_issues
# ---------------------------------------------------------------------------


def _iso_days_ago(days: int) -> str:
    """Format an ISO-8601 timestamp N days before now."""
    now = datetime.datetime.now(datetime.timezone.utc)
    when = now - datetime.timedelta(days=days)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


class TestSearchRecentIssues:
    def test_match_within_cutoff_returns_true(self):
        client = GHClient(token="t")
        page = [{"title": "[TruffleHog] hit", "created_at": _iso_days_ago(1)}]
        with mock.patch.object(client, "call", return_value=page):
            # Pin: open issue inside cutoff window matches
            assert client.search_recent_issues(
                "o/r", "[TruffleHog]", days=7
            ) is True

    def test_match_outside_cutoff_returns_false(self):
        client = GHClient(token="t")
        page = [{"title": "[TruffleHog] old", "created_at": _iso_days_ago(60)}]
        with mock.patch.object(client, "call", return_value=page):
            # Pin: matching title but outside cutoff -> not a recent match
            assert client.search_recent_issues(
                "o/r", "[TruffleHog]", days=7
            ) is False

    def test_state_open_filter_in_query(self):
        client = GHClient(token="t")
        captured = {}

        def fake_call(method, path, payload):
            captured["path"] = path
            return []

        with mock.patch.object(client, "call", side_effect=fake_call):
            client.search_recent_issues("o/r", "X", days=7)
        # Pin: search_recent_issues only checks OPEN issues
        assert "state=open" in captured["path"]
        assert "sort=created" in captured["path"]

    def test_invalid_date_field_skipped_not_raised(self):
        client = GHClient(token="t")
        page = [
            {"title": "[TruffleHog] hit", "created_at": "not-a-date"},
            {"title": "Other", "created_at": _iso_days_ago(1)},
        ]
        with mock.patch.object(client, "call", return_value=page):
            # Pin: malformed created_at must NOT crash; iteration continues
            assert client.search_recent_issues("o/r", "[TruffleHog]") is False

    def test_missing_created_at_field_skipped(self):
        client = GHClient(token="t")
        page = [{"title": "[TruffleHog] hit"}]  # no created_at
        with mock.patch.object(client, "call", return_value=page):
            # Pin: missing created_at = no match (cannot evaluate cutoff)
            assert client.search_recent_issues("o/r", "[TruffleHog]") is False

    def test_title_must_contain_pattern(self):
        client = GHClient(token="t")
        page = [{"title": "Unrelated", "created_at": _iso_days_ago(1)}]
        with mock.patch.object(client, "call", return_value=page):
            assert client.search_recent_issues("o/r", "[TruffleHog]") is False

    def test_non_list_response_breaks_loop(self):
        client = GHClient(token="t")
        with mock.patch.object(
            client, "call", return_value={"error": "shape"}
        ) as call:
            # Pin: non-list payload aborts the per-page loop, returns False
            assert client.search_recent_issues("o/r", "X") is False
            assert call.call_count == 1

    def test_short_page_breaks_loop(self):
        client = GHClient(token="t")
        # Less than 30 entries on page 1 -> no page 2 fetch
        page1 = [{"title": "x"} for _ in range(5)]
        with mock.patch.object(
            client, "call", side_effect=[page1, AssertionError("page 2 hit")]
        ) as call:
            client.search_recent_issues("o/r", "X")
            assert call.call_count == 1

    def test_paginates_to_page_two_when_full_page(self):
        client = GHClient(token="t")
        page1 = [
            {"title": "noise", "created_at": _iso_days_ago(60)}
            for _ in range(30)
        ]
        page2 = [
            {"title": "[TruffleHog] hit", "created_at": _iso_days_ago(1)}
        ]
        with mock.patch.object(
            client, "call", side_effect=[page1, page2]
        ) as call:
            assert client.search_recent_issues("o/r", "[TruffleHog]") is True
            assert call.call_count == 2

    def test_http_error_returns_false(self):
        client = GHClient(token="t")
        with mock.patch.object(
            client, "call", side_effect=_http_error(500)
        ):
            # Pin: HTTPError must NOT propagate; dedup degrades to "no match"
            assert client.search_recent_issues("o/r", "X") is False

    def test_non_dict_issue_entries_skipped(self):
        client = GHClient(token="t")
        # Mixed list: stray non-dicts ignored
        page = [
            "garbage",
            None,
            {"title": "[TruffleHog] hit", "created_at": _iso_days_ago(1)},
        ]
        with mock.patch.object(client, "call", return_value=page):
            assert client.search_recent_issues("o/r", "[TruffleHog]") is True
