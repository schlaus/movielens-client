"""HTTP client for movielens.org's unpublished JSON API.

Shapes here were confirmed against the live service on 2026-09-06. There is no
official documentation; treat the comments as the record.

Nothing is persisted, cached or logged. The caller owns storage.
"""

from __future__ import annotations

from typing import Any, Iterator, Mapping

import requests

from .errors import AuthenticationError, MovieLensAPIError
from .models import (
    Account,
    MovieDetail,
    Prediction,
    Rating,
    detail_from_result,
    prediction_from_result,
    rating_from_result,
)

__all__ = ["MovieLensSession", "login", "DEFAULT_BASE_URL"]

DEFAULT_BASE_URL = "https://movielens.org"
DEFAULT_TIMEOUT = 30.0
DEFAULT_PAGE_SIZE = 100

#: MovieLens' explore endpoint is one-indexed. ``page=0`` answers with
#: ``500 MovieLens application error MLERR0``.
FIRST_PAGE = 1

#: Sent when the caller has no prediction and the movie has none either — a
#: cold-start account. MovieLens requires ``predictedRating`` on every write
#: and accepts 0.0 (the documented range is -1.0 to 5.0).
NO_PREDICTION_FALLBACK = 0.0

_JSON_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
}


def _new_http() -> requests.Session:
    http = requests.Session()
    http.headers.update(_JSON_HEADERS)
    return http


class MovieLensSession:
    """An authenticated MovieLens session.

    The session *is* the ``ml4_session`` cookie held by the underlying HTTP
    session. Obtain one from :func:`login`; every other call is a method here.

    The cookie is deliberately kept out of ``repr`` and ``str`` so it cannot
    leak into a traceback, and the package logs nothing at all.
    """

    def __init__(
        self,
        http: Any,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        owns_http: bool = False,
    ):
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._owns_http = owns_http

    def __repr__(self) -> str:
        return f"<MovieLensSession base_url={self._base_url!r}>"

    __str__ = __repr__

    def __enter__(self) -> "MovieLensSession":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        """Release the underlying connection pool, if this session owns it."""
        if self._owns_http:
            close = getattr(self._http, "close", None)
            if close is not None:
                close()

    # ---------------------------------------------------------------- account

    def account(self) -> Account:
        """``GET /api/users/me`` → username and total rating count."""
        body = self._request("GET", "/api/users/me")
        return Account.from_payload(body.get("data") or {})

    # ---------------------------------------------------------------- reading

    def iter_ratings(self, *, page_size: int = DEFAULT_PAGE_SIZE) -> Iterator[Rating]:
        """Stream every rating on the account, newest page first.

        A generator, one HTTP request per page, materialising nothing: a heavy
        account has thousands of ratings and the caller streams them.
        """
        for result in self._iter_explore({"hasRated": "yes"}, page_size):
            yield rating_from_result(result)

    def iter_predictions(
        self, *, page_size: int = DEFAULT_PAGE_SIZE
    ) -> Iterator[Prediction]:
        """Stream predicted ratings for films the account has not rated.

        Also a generator, and it needs to be: the live feed reports tens of
        thousands of items. ``Prediction.prediction`` is ``None`` until the
        account has rated enough films (roughly a dozen) — a cold start, not an
        error.
        """
        for result in self._iter_explore(
            {"hasRated": "no", "sortBy": "prediction"}, page_size
        ):
            yield prediction_from_result(result)

    def movie(self, movie_id: int) -> MovieDetail:
        """``GET /api/movies/<id>`` → the movie plus this user's data for it."""
        body = self._request("GET", f"/api/movies/{int(movie_id)}")
        details = (body.get("data") or {}).get("movieDetails") or {}
        return detail_from_result(details)

    # ---------------------------------------------------------------- writing

    def rate(
        self,
        movie_id: int,
        rating: float,
        *,
        predicted_rating: float | None = None,
    ) -> int:
        """Write a rating. Returns the account's new total rating count.

        ``predictedRating`` is required by the API — omitting it returns
        ``400 json is missing required double: predictedRating``. When the
        caller does not supply one this fetches the movie and uses its
        ``movieUserData.prediction``. If that is null too (a cold-start
        account) it sends :data:`NO_PREDICTION_FALLBACK` (0.0) rather than
        failing the write; MovieLens accepts it.

        The rating value is passed through unvalidated. MovieLens accepts
        -1.0 to 5.0 and rejects anything else with a 400, which surfaces as
        :class:`MovieLensAPIError`.
        """
        if predicted_rating is None:
            predicted_rating = self.movie(movie_id).prediction
        if predicted_rating is None:
            predicted_rating = NO_PREDICTION_FALLBACK

        body = self._request(
            "POST",
            "/api/users/me/ratings",
            payload={
                "movieId": int(movie_id),
                "rating": float(rating),
                "predictedRating": float(predicted_rating),
            },
        )
        return int((body.get("data") or {}).get("numRatings") or 0)

    # ---------------------------------------------------------------- interns

    def _iter_explore(
        self, params: Mapping[str, Any], page_size: int
    ) -> Iterator[dict[str, Any]]:
        """Walk ``/api/movies/explore`` a page at a time.

        ``limit`` is ignored by the API; ``pageSize`` and ``page`` work.
        Stops on the first empty page, on reaching ``pager.totalItems``, on a
        short page, or if the server stops advancing — the last guard turns a
        server-side surprise into a stop rather than an endless loop.
        """
        if page_size < 1:
            raise ValueError("page_size must be at least 1")

        page = FIRST_PAGE
        yielded = 0
        previous_first_id: Any = None

        while True:
            body = self._request(
                "GET",
                "/api/movies/explore",
                params={**params, "pageSize": page_size, "page": page},
            )
            data = body.get("data") or {}
            results = data.get("searchResults") or []
            if not results:
                return

            first_id = results[0].get("movieId")
            if previous_first_id is not None and first_id == previous_first_id:
                return  # the page parameter is not advancing; stop, don't loop
            previous_first_id = first_id

            for result in results:
                yield result
                yielded += 1

            total_items = (data.get("pager") or {}).get("totalItems")
            if isinstance(total_items, int) and yielded >= total_items:
                return
            if len(results) < page_size:
                return
            page += 1

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return _request(
            self._http,
            method,
            f"{self._base_url}{path}",
            params=params,
            payload=payload,
            timeout=self._timeout,
        )


def _request(
    http: Any,
    method: str,
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    payload: Mapping[str, Any] | None = None,
    timeout: float,
    auth_failure: bool = False,
) -> dict[str, Any]:
    """Issue one request and normalise every failure into the two error types.

    - transport trouble, non-JSON, 5xx, other 4xx → :class:`MovieLensAPIError`
    - 401/403, or a body saying auth failed → :class:`AuthenticationError`

    ``auth_failure=True`` is for the login endpoint, where a body-level
    ``status: fail`` means the credentials were rejected rather than the
    request being malformed.
    """
    try:
        response = http.request(
            method, url, params=params, json=payload, timeout=timeout
        )
    except requests.RequestException as exc:
        # Never let a raw requests exception escape: the consumer would have to
        # depend on requests to tell an outage from a bad password.
        raise MovieLensAPIError(f"request to MovieLens failed: {exc}") from exc

    status_code = response.status_code
    try:
        body = response.json()
    except (ValueError, requests.RequestException):
        body = None

    message = ""
    if isinstance(body, dict):
        message = str(body.get("message") or "")

    if status_code in (401, 403):
        raise AuthenticationError(message or "MovieLens rejected the session")

    if not isinstance(body, dict):
        raise MovieLensAPIError(
            f"MovieLens returned a non-JSON body (HTTP {status_code})",
            status_code=status_code,
        )

    if status_code >= 400:
        exc_type = AuthenticationError if auth_failure else MovieLensAPIError
        detail = message or f"HTTP {status_code}"
        if exc_type is AuthenticationError:
            raise AuthenticationError(detail)
        raise MovieLensAPIError(detail, status_code=status_code)

    if body.get("status") != "success":
        # A 200 carrying status: fail. On login this means bad credentials; if
        # it were read as success the caller would see 401s from then on and
        # mistake a bad password for an outage.
        detail = message or "MovieLens reported failure"
        if auth_failure:
            raise AuthenticationError(detail)
        raise MovieLensAPIError(detail, status_code=status_code)

    return body


def login(
    username: str,
    password: str,
    *,
    http: Any | None = None,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = DEFAULT_TIMEOUT,
) -> MovieLensSession:
    """``POST /api/sessions`` → an authenticated :class:`MovieLensSession`.

    Success is decided by the response body's ``status`` field, not by the HTTP
    code. Raises :class:`AuthenticationError` when the credentials are
    rejected and :class:`MovieLensAPIError` when MovieLens could not be
    reached or answered with something unusable.

    Pass ``http`` to supply your own ``requests.Session`` (connection reuse,
    proxies, tests). Credentials are sent once and kept nowhere.
    """
    owns_http = http is None
    if owns_http:
        http = _new_http()

    try:
        _request(
            http,
            "POST",
            f"{base_url.rstrip('/')}/api/sessions",
            payload={"userName": username, "password": password},
            timeout=timeout,
            auth_failure=True,
        )
    except Exception:
        if owns_http:
            http.close()
        raise

    return MovieLensSession(
        http, base_url=base_url, timeout=timeout, owns_http=owns_http
    )
