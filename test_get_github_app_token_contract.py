"""
Contract tests for get_github_app_token covering the JWT mint + token
exchange path in trufflehog_scanner.py (lines 145-182).

Pin behaviour:
  * JWT_AVAILABLE=False short-circuits to RuntimeError with install hint
  * JWT payload: iat/exp/iss with 10-minute expiry, RS256 algorithm
  * Token-exchange URL hits API_BASE/app/installations/<id>/access_tokens
  * Headers: Bearer <jwt>, Accept github+json, X-GitHub-Api-Version
  * POST method
  * Empty/missing 'token' field in response raises ValueError
  * HTTPError logs at ERROR level and re-raises
  * Generic Exception (URL/connection) logs and re-raises
  * Success logs at INFO with installation_id
"""
from __future__ import annotations

import io
import json
import logging
from unittest import mock
from urllib.error import HTTPError

import pytest

import trufflehog_scanner as ts


@pytest.fixture
def jwt_available(monkeypatch):
    monkeypatch.setattr(ts, "JWT_AVAILABLE", True)
    return True


def _mock_response(payload: dict):
    """Return a context-manager-compatible mock for urlopen()."""
    body = json.dumps(payload).encode("utf-8")
    resp = mock.MagicMock()
    resp.read.return_value = body
    resp.__enter__ = mock.MagicMock(return_value=resp)
    resp.__exit__ = mock.MagicMock(return_value=False)
    return resp


class TestJwtAvailableGuard:
    def test_jwt_unavailable_raises_runtime_error(self, monkeypatch):
        monkeypatch.setattr(ts, "JWT_AVAILABLE", False)
        with pytest.raises(RuntimeError) as exc:
            ts.get_github_app_token("123", "key", "456")
        assert "PyJWT is required" in str(exc.value)
        assert "pip install PyJWT" in str(exc.value)


class TestJwtPayloadShape:
    def test_jwt_payload_includes_iat_exp_iss(self, jwt_available, monkeypatch):
        captured = {}

        def fake_encode(payload, key, algorithm):
            captured["payload"] = payload
            captured["key"] = key
            captured["algorithm"] = algorithm
            return "stub.jwt.token"

        monkeypatch.setattr(ts.jwt, "encode", fake_encode)
        monkeypatch.setattr(
            ts, "urlopen",
            mock.MagicMock(return_value=_mock_response({"token": "ghs_xyz"})),
        )

        ts.get_github_app_token("APPID", "PRIV", "INSTID")

        assert "iat" in captured["payload"]
        assert "exp" in captured["payload"]
        assert captured["payload"]["iss"] == "APPID"

    def test_jwt_expiry_is_10_minutes_after_iat(self, jwt_available, monkeypatch):
        captured = {}

        def fake_encode(payload, key, algorithm):
            captured["payload"] = payload
            return "stub"

        monkeypatch.setattr(ts.jwt, "encode", fake_encode)
        monkeypatch.setattr(
            ts, "urlopen",
            mock.MagicMock(return_value=_mock_response({"token": "ghs_xyz"})),
        )
        ts.get_github_app_token("APPID", "PRIV", "INSTID")

        # Pin: exp = iat + 600 (10 minutes). Drift to e.g. 11 min trips
        # the JWT validity window on GitHub's side.
        delta = captured["payload"]["exp"] - captured["payload"]["iat"]
        assert delta == 600

    def test_jwt_algorithm_is_rs256(self, jwt_available, monkeypatch):
        captured = {}

        def fake_encode(payload, key, algorithm):
            captured["algorithm"] = algorithm
            return "stub"

        monkeypatch.setattr(ts.jwt, "encode", fake_encode)
        monkeypatch.setattr(
            ts, "urlopen",
            mock.MagicMock(return_value=_mock_response({"token": "ghs_xyz"})),
        )
        ts.get_github_app_token("APPID", "PRIV", "INSTID")

        # GitHub Apps mandate RS256; HS256 would silently fail at exchange.
        assert captured["algorithm"] == "RS256"

    def test_jwt_signed_with_private_key_argument(self, jwt_available, monkeypatch):
        captured = {}

        def fake_encode(payload, key, algorithm):
            captured["key"] = key
            return "stub"

        monkeypatch.setattr(ts.jwt, "encode", fake_encode)
        monkeypatch.setattr(
            ts, "urlopen",
            mock.MagicMock(return_value=_mock_response({"token": "ghs_xyz"})),
        )
        ts.get_github_app_token("APPID", "MY-PRIVATE-KEY", "INSTID")

        assert captured["key"] == "MY-PRIVATE-KEY"


class TestTokenExchangeRequest:
    def _setup(self, monkeypatch, response_payload=None, http_error=None):
        monkeypatch.setattr(ts.jwt, "encode",
                            lambda *a, **kw: "stub.jwt.token")
        if http_error is not None:
            urlopen = mock.MagicMock(side_effect=http_error)
        else:
            urlopen = mock.MagicMock(
                return_value=_mock_response(response_payload or {"token": "x"})
            )
        monkeypatch.setattr(ts, "urlopen", urlopen)
        return urlopen

    def test_request_url_targets_app_installations_endpoint(
        self, jwt_available, monkeypatch
    ):
        urlopen = self._setup(monkeypatch)
        ts.get_github_app_token("APPID", "PRIV", "12345")
        req = urlopen.call_args[0][0]
        assert req.full_url == (
            f"{ts.API_BASE}/app/installations/12345/access_tokens"
        )

    def test_request_method_is_post(self, jwt_available, monkeypatch):
        urlopen = self._setup(monkeypatch)
        ts.get_github_app_token("APPID", "PRIV", "12345")
        req = urlopen.call_args[0][0]
        assert req.get_method() == "POST"

    def test_authorization_header_is_bearer_jwt(self, jwt_available, monkeypatch):
        urlopen = self._setup(monkeypatch)
        ts.get_github_app_token("APPID", "PRIV", "12345")
        req = urlopen.call_args[0][0]
        # Request lowercases headers
        assert req.headers.get("Authorization") == "Bearer stub.jwt.token"

    def test_accept_header_is_github_v3_json(self, jwt_available, monkeypatch):
        urlopen = self._setup(monkeypatch)
        ts.get_github_app_token("APPID", "PRIV", "12345")
        req = urlopen.call_args[0][0]
        assert req.headers.get("Accept") == "application/vnd.github+json"

    def test_api_version_header_pinned(self, jwt_available, monkeypatch):
        urlopen = self._setup(monkeypatch)
        ts.get_github_app_token("APPID", "PRIV", "12345")
        req = urlopen.call_args[0][0]
        # GitHub deprecates older API versions; pin the one we ship.
        assert req.headers.get("X-github-api-version") == "2022-11-28"

    def test_request_uses_default_api_timeout(self, jwt_available, monkeypatch):
        urlopen = self._setup(monkeypatch)
        ts.get_github_app_token("APPID", "PRIV", "12345")
        # urlopen is invoked with timeout kwarg = DEFAULT_API_TIMEOUT_SEC
        kwargs = urlopen.call_args.kwargs
        assert kwargs.get("timeout") == ts.DEFAULT_API_TIMEOUT_SEC


class TestTokenExtraction:
    def test_extracts_token_field_from_response(self, jwt_available, monkeypatch):
        monkeypatch.setattr(ts.jwt, "encode",
                            lambda *a, **kw: "stub.jwt.token")
        monkeypatch.setattr(
            ts, "urlopen",
            mock.MagicMock(
                return_value=_mock_response({"token": "ghs_actual_token_xyz"})
            ),
        )
        token = ts.get_github_app_token("APPID", "PRIV", "12345")
        assert token == "ghs_actual_token_xyz"

    def test_missing_token_field_raises_value_error(
        self, jwt_available, monkeypatch
    ):
        monkeypatch.setattr(ts.jwt, "encode",
                            lambda *a, **kw: "stub.jwt.token")
        monkeypatch.setattr(
            ts, "urlopen",
            mock.MagicMock(
                return_value=_mock_response({"message": "Bad credentials"})
            ),
        )
        with pytest.raises(ValueError) as exc:
            ts.get_github_app_token("APPID", "PRIV", "12345")
        assert "No token" in str(exc.value)

    def test_empty_token_field_raises_value_error(
        self, jwt_available, monkeypatch
    ):
        monkeypatch.setattr(ts.jwt, "encode",
                            lambda *a, **kw: "stub.jwt.token")
        monkeypatch.setattr(
            ts, "urlopen",
            mock.MagicMock(
                return_value=_mock_response({"token": ""})
            ),
        )
        with pytest.raises(ValueError):
            ts.get_github_app_token("APPID", "PRIV", "12345")


class TestErrorHandling:
    def test_http_error_logged_and_reraised(
        self, jwt_available, monkeypatch, caplog
    ):
        monkeypatch.setattr(ts.jwt, "encode",
                            lambda *a, **kw: "stub.jwt.token")
        err = HTTPError(
            url="https://api.github.com/app/installations/1/access_tokens",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=io.BytesIO(b""),
        )
        monkeypatch.setattr(
            ts, "urlopen", mock.MagicMock(side_effect=err)
        )
        with caplog.at_level(logging.ERROR):
            with pytest.raises(HTTPError):
                ts.get_github_app_token("APPID", "PRIV", "12345")
        # Error log includes the HTTP code
        joined = " ".join(r.message for r in caplog.records)
        assert "GitHub App" in joined
        assert "401" in joined

    def test_generic_exception_logged_and_reraised(
        self, jwt_available, monkeypatch, caplog
    ):
        monkeypatch.setattr(ts.jwt, "encode",
                            lambda *a, **kw: "stub.jwt.token")
        monkeypatch.setattr(
            ts, "urlopen",
            mock.MagicMock(side_effect=ConnectionError("network down")),
        )
        with caplog.at_level(logging.ERROR):
            with pytest.raises(ConnectionError):
                ts.get_github_app_token("APPID", "PRIV", "12345")
        joined = " ".join(r.message for r in caplog.records)
        assert "GitHub App" in joined
        assert "network down" in joined


class TestSuccessLogging:
    def test_success_logs_installation_id(
        self, jwt_available, monkeypatch, caplog
    ):
        monkeypatch.setattr(ts.jwt, "encode",
                            lambda *a, **kw: "stub.jwt.token")
        monkeypatch.setattr(
            ts, "urlopen",
            mock.MagicMock(return_value=_mock_response({"token": "ghs_x"})),
        )
        with caplog.at_level(logging.INFO):
            ts.get_github_app_token("APPID", "PRIV", "MY-INST-99")
        joined = " ".join(r.message for r in caplog.records)
        assert "Successfully authenticated" in joined
        assert "MY-INST-99" in joined


class TestGlobalLimiterCalled:
    def test_global_limiter_wait_invoked_before_exchange(
        self, jwt_available, monkeypatch
    ):
        monkeypatch.setattr(ts.jwt, "encode",
                            lambda *a, **kw: "stub.jwt.token")
        monkeypatch.setattr(
            ts, "urlopen",
            mock.MagicMock(return_value=_mock_response({"token": "x"})),
        )
        wait_mock = mock.MagicMock()
        monkeypatch.setattr(ts.GLOBAL_LIMITER, "wait", wait_mock)
        ts.get_github_app_token("APPID", "PRIV", "12345")
        wait_mock.assert_called_once()
