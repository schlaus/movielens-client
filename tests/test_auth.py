"""Auth failure and transport failure must never be confused for each other."""

import logging

import pytest
import requests

from movielens_client import (
    AuthenticationError,
    MovieLensAPIError,
    MovieLensError,
    login,
)
from conftest import FakeHTTP, FakeResponse, response_from_fixture

USERNAME = "fixture-user"
PASSWORD = "fixture-password-not-real"


def test_login_success_returns_a_session_and_posts_the_credentials(logged_in_http):
    session = login(USERNAME, PASSWORD, http=logged_in_http)

    assert session is not None
    call = logged_in_http.calls[0]
    assert (call["method"], call["path"]) == ("POST", "/api/sessions")
    assert call["json"] == {"userName": USERNAME, "password": PASSWORD}


def test_bad_password_raises_authentication_error():
    http = FakeHTTP({("POST", "/api/sessions"): response_from_fixture("login_fail")})
    with pytest.raises(AuthenticationError):
        login(USERNAME, PASSWORD, http=http)


def test_login_failure_reported_with_http_200_is_still_an_auth_error():
    """The body's ``status`` field decides, not the HTTP code.

    If login branched on the status code alone, a 200-with-``status: fail``
    would look like success, every later call would 401, and those 401s would
    be reported as an outage — a bad password looking like MovieLens being down
    forever. This is exactly the failure the two exception types exist to stop.
    """
    body = {"status": "fail", "message": "authentication failed"}
    http = FakeHTTP({("POST", "/api/sessions"): FakeResponse(200, body)})
    with pytest.raises(AuthenticationError):
        login(USERNAME, PASSWORD, http=http)


def test_session_rejected_mid_stream_is_an_auth_error_not_an_outage(logged_in_http):
    logged_in_http.routes[("GET", "/api/users/me")] = response_from_fixture(
        "unauthenticated_401"
    )
    session = login(USERNAME, PASSWORD, http=logged_in_http)
    with pytest.raises(AuthenticationError):
        session.account()


def test_server_error_is_an_api_error_not_an_auth_error(logged_in_http):
    logged_in_http.routes[("GET", "/api/movies/explore")] = response_from_fixture(
        "server_error_500"
    )
    session = login(USERNAME, PASSWORD, http=logged_in_http)
    with pytest.raises(MovieLensAPIError) as excinfo:
        next(session.iter_ratings())
    assert not isinstance(excinfo.value, AuthenticationError)
    assert excinfo.value.status_code == 500


def test_connection_failure_is_an_api_error_not_an_auth_error(logged_in_http):
    logged_in_http.routes[("GET", "/api/users/me")] = requests.ConnectionError("boom")
    session = login(USERNAME, PASSWORD, http=logged_in_http)
    with pytest.raises(MovieLensAPIError) as excinfo:
        session.account()
    assert not isinstance(excinfo.value, AuthenticationError)


def test_connection_failure_during_login_is_an_api_error_not_an_auth_error():
    """A network blip while logging in must not be read as 'bad password'."""
    http = FakeHTTP({("POST", "/api/sessions"): requests.Timeout("slow")})
    with pytest.raises(MovieLensAPIError) as excinfo:
        login(USERNAME, PASSWORD, http=http)
    assert not isinstance(excinfo.value, AuthenticationError)


def test_non_json_body_is_an_api_error(logged_in_http):
    logged_in_http.routes[("GET", "/api/users/me")] = FakeResponse(
        200, None, text="<html>maintenance</html>"
    )
    session = login(USERNAME, PASSWORD, http=logged_in_http)
    with pytest.raises(MovieLensAPIError):
        session.account()


def test_both_error_types_share_a_base_but_are_not_each_other():
    assert issubclass(AuthenticationError, MovieLensError)
    assert issubclass(MovieLensAPIError, MovieLensError)
    assert not issubclass(AuthenticationError, MovieLensAPIError)
    assert not issubclass(MovieLensAPIError, AuthenticationError)


def test_nothing_is_logged_and_no_credential_reaches_a_repr(logged_in_http, caplog):
    with caplog.at_level(logging.DEBUG):
        session = login(USERNAME, PASSWORD, http=logged_in_http)
    assert caplog.records == []

    rendered = f"{session!r} {session!s}"
    assert PASSWORD not in rendered
    assert USERNAME not in rendered
    assert "ml4_session" not in rendered
    assert "cookie" not in rendered.lower()


def test_failed_login_does_not_echo_the_password_in_the_exception():
    http = FakeHTTP({("POST", "/api/sessions"): response_from_fixture("login_fail")})
    with pytest.raises(AuthenticationError) as excinfo:
        login(USERNAME, PASSWORD, http=http)
    assert PASSWORD not in str(excinfo.value)


@pytest.mark.parametrize("status_code", [429, 500, 503])
def test_an_outage_during_login_is_an_api_error_not_an_auth_error(status_code):
    """MovieLens being down while logging in is not a bad password.

    If a 5xx at login raised AuthenticationError, the nightly mirror would stop
    deferring, mark stored credentials invalid and ask the user to re-enter a
    password that was never wrong. That is the precise inversion these two
    types exist to prevent, and it survives the outage — the credentials stay
    marked bad afterwards.
    """
    body = {"status": "error", "message": "MovieLens application error MLERR0"}
    http = FakeHTTP({("POST", "/api/sessions"): FakeResponse(status_code, body)})

    with pytest.raises(MovieLensAPIError) as excinfo:
        login(USERNAME, PASSWORD, http=http)

    assert not isinstance(excinfo.value, AuthenticationError)
    assert excinfo.value.status_code == status_code


def test_a_recorded_500_at_login_is_an_api_error():
    http = FakeHTTP({("POST", "/api/sessions"): response_from_fixture("server_error_500")})

    with pytest.raises(MovieLensAPIError) as excinfo:
        login(USERNAME, PASSWORD, http=http)

    assert not isinstance(excinfo.value, AuthenticationError)
