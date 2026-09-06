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
    ExportedRating,
    Movie,
    MovieDetail,
    Prediction,
    Rating,
    detail_from_result,
    parse_ratings_csv,
    canonical_imdb_id,
    prediction_from_result,
    rating_from_result,
)

__all__ = ["MovieLensSession", "login", "DEFAULT_BASE_URL"]

DEFAULT_BASE_URL = "https://movielens.org"
DEFAULT_TIMEOUT = 30.0
DEFAULT_PAGE_SIZE = 100

#: Bounds for :meth:`MovieLensSession.find_movie_by_imdb_id`. A title search
#: is the only way to reach a film by IMDb id, and a common word matches
#: thousands — the recorded default page reports 10,000 items. 4 pages of 50
#: is 200 candidates: comfortably more than any real title search needs (the
#: live "Parasite" search returns 9, "The Matrix" 18) and a hard stop on the
#: pathological one.
SEARCH_PAGE_SIZE = 50
SEARCH_MAX_PAGES = 4

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

#: The export endpoint answers ``text/csv`` whatever is asked for — verified
#: 2026-09-06 — but the session sends ``Accept: application/json`` for every
#: other call, and asking a CSV endpoint for JSON is how that would break if
#: MovieLens ever started honouring the header.
_CSV_HEADERS = {"Accept": "text/csv"}


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

    def export_ratings(self) -> list[ExportedRating]:
        """``GET /api/users/me/movielens-ratings.csv`` → the whole history.

        One request for every rating on the account, rather than the dozens of
        pages :meth:`iter_ratings` walks; that removes the paging failure modes
        from a full sync entirely. Returns a list, not a generator: the body
        arrives whole, so there is nothing to be lazy about, and deferring the
        parse would surface shape errors half way through a consumer's write.

        The rows carry ``average_rating`` — the community mean — alongside the
        user's ``rating``, and no ``prediction`` or ``rated_at``. When those
        two matter, :meth:`iter_ratings` still carries them.
        """
        text = self._text_request(
            "GET", "/api/users/me/movielens-ratings.csv", headers=_CSV_HEADERS
        )
        return parse_ratings_csv(text)

    def find_movie_by_imdb_id(
        self,
        imdb_id: str | None,
        title: str,
        *,
        page_size: int = SEARCH_PAGE_SIZE,
        max_pages: int = SEARCH_MAX_PAGES,
    ) -> Movie | None:
        """Resolve an IMDb id to a MovieLens movie, or ``None``.

        Rating a film MovieLens has never shown the caller needs its
        ``movieId``, and there is no direct lookup. The only route that works
        is ``GET /api/movies/explore?q=<title>`` matched on
        ``movie.imdbMovieId`` — so ``title`` is what is searched with and
        ``imdb_id`` is what decides the answer.

        Two things that look like lookups are not, both verified on
        2026-09-06 and both silent about it:

        * ``explore?imdbMovieId=…`` ignores the parameter and answers an
          unrelated default page — a 10,000-item result set whose first film
          has nothing to do with the id. Nothing errors.
        * ``explore?q=tt0133093`` answers zero results.

        So the match is on the id and never on position or title equality: a
        search for "Parasite" returns three films titled exactly that, with
        three different IMDb ids, and the wanted one is fourth. Titles differ
        by language and subtitle where ids do not.

        ``None`` is returned when nothing matches — a film MovieLens does not
        carry is a normal answer, not a failure. An outage still raises, so
        the caller can tell "not there" from "could not ask".

        No ``year`` parameter: appending a year to the query returns zero
        results (``q=Parasite 2019`` and ``q=Parasite (2019)`` both did on
        2026-09-06), and filtering candidates by year could only *lose* the
        right film, since MovieLens' ``releaseYear`` and IMDb's disagree for
        festival and limited releases. The match is on a unique id; there is
        nothing left for a year to disambiguate.
        """
        wanted = canonical_imdb_id(imdb_id)
        if wanted is None:
            # An id that canonicalises to nothing must never be compared with
            # a result's id: MovieLens carries films with no imdbMovieId, and
            # None == None would return one of them — an unrelated film, which
            # the caller would then rate.
            return None

        query = (title or "").strip()
        if not query:
            return None

        for result in self._iter_explore(
            {"q": query}, page_size, max_pages=max_pages
        ):
            movie = Movie.from_payload(result.get("movie") or {})
            if movie.imdb_id is not None and movie.imdb_id == wanted:
                return movie
        return None

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
        self,
        params: Mapping[str, Any],
        page_size: int,
        *,
        max_pages: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Walk ``/api/movies/explore`` a page at a time.

        ``limit`` is ignored by the API; ``pageSize`` and ``page`` work.
        Stops on the first empty page, on reaching ``pager.totalItems``, or if
        the server stops advancing the page parameter — that last guard turns a
        server-side surprise into a stop rather than an endless loop.

        ``max_pages`` bounds the walk. It is None for the account's own
        streams, which must run to the end or silently truncate a mirror, and
        set for title searches, where the caller wants one film out of a
        result set that can report ten thousand.
        """
        if page_size < 1:
            raise ValueError("page_size must be at least 1")
        if max_pages is not None and max_pages < 1:
            raise ValueError("max_pages must be at least 1")

        page = FIRST_PAGE
        yielded = 0
        pages_read = 0
        previous_first_id: Any = None

        while True:
            body = self._request(
                "GET",
                "/api/movies/explore",
                params={**params, "pageSize": page_size, "page": page},
            )
            data = body.get("data") or {}
            results = data.get("searchResults") or []
            if not isinstance(results, list):
                raise MovieLensAPIError(
                    "MovieLens returned a searchResults block that is not a list",
                )
            if not results:
                return

            pages_read += 1
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
            if max_pages is not None and pages_read >= max_pages:
                return
            # Deliberately no "short page means last page" shortcut. Asking for
            # 100 and getting 50 would end the stream silently if MovieLens
            # ever clamped pageSize server-side, and a mirroring consumer would
            # see a truncated account with no error. It does not clamp today —
            # pageSize up to 1000 was honoured exactly on 2026-09-06 — but the
            # cost of not assuming it is one extra request for the empty page
            # at the end of the stream.
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

    def _text_request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> str:
        return _text_request(
            self._http,
            method,
            f"{self._base_url}{path}",
            params=params,
            headers=headers,
            timeout=self._timeout,
        )


def _send(
    http: Any,
    method: str,
    url: str,
    *,
    params: Mapping[str, Any] | None,
    payload: Mapping[str, Any] | None,
    timeout: float,
    headers: Mapping[str, str] | None = None,
) -> Any:
    """Issue the request, turning transport trouble into MovieLensAPIError.

    Never let a raw requests exception escape: the consumer would have to
    depend on requests to tell an outage from a bad password.
    """
    try:
        return http.request(
            method,
            url,
            params=params,
            json=payload,
            timeout=timeout,
            headers=headers,
        )
    except requests.RequestException as exc:
        raise MovieLensAPIError(f"request to MovieLens failed: {exc}") from exc


def _raise_for_status(status_code: int, message: str) -> None:
    """401/403 → AuthenticationError; any other 4xx/5xx → MovieLensAPIError.

    Deliberately never widened: a 500 is an outage whatever endpoint it hits,
    and routing it to AuthenticationError would tell the consumer that good
    credentials are bad.
    """
    if status_code in (401, 403):
        raise AuthenticationError(message or "MovieLens rejected the session")
    if status_code >= 400:
        raise MovieLensAPIError(
            message or f"HTTP {status_code}", status_code=status_code
        )


def _message_of(body: Any) -> str:
    return str(body.get("message") or "") if isinstance(body, dict) else ""


def _json_of(response: Any) -> Any:
    try:
        return response.json()
    except (ValueError, requests.RequestException):
        return None


def _text_request(
    http: Any,
    method: str,
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float,
) -> str:
    """Fetch a body that is not JSON — today, the ratings CSV export.

    Shares the status handling with :func:`_request` so a rejected session is
    an :class:`AuthenticationError` here too, but does not require a
    ``status: success`` envelope: a CSV body has no envelope to carry one. The
    body is returned unparsed; deciding whether it is really CSV is the
    parser's job, and it does refuse anything else.
    """
    response = _send(
        http, method, url, params=params, payload=None, timeout=timeout, headers=headers
    )
    _raise_for_status(response.status_code, _message_of(_json_of(response)))
    return response.text


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

    ``auth_failure=True`` is for the login endpoint, and applies only to a
    body-level ``status: fail`` on an otherwise-successful response, where it
    means the credentials were rejected. It deliberately does not widen 4xx or
    5xx into an auth failure: those are outages whatever endpoint they hit.
    """
    response = _send(
        http, method, url, params=params, payload=payload, timeout=timeout
    )
    status_code = response.status_code
    body = _json_of(response)

    # Deliberately not conditioned on auth_failure. A rejected login is a 401
    # and is caught here; a 200 carrying status: fail is caught below. Nothing
    # else is a credential rejection, so routing 4xx/5xx to AuthenticationError
    # at login would turn a MovieLens outage into "your password is wrong" —
    # the consumer would stop deferring and mark good credentials invalid.
    _raise_for_status(status_code, _message_of(body))

    if not isinstance(body, dict):
        raise MovieLensAPIError(
            f"MovieLens returned a non-JSON body (HTTP {status_code})",
            status_code=status_code,
        )

    if body.get("status") != "success":
        # A 200 carrying status: fail. On login this means bad credentials; if
        # it were read as success the caller would see 401s from then on and
        # mistake a bad password for an outage.
        detail = _message_of(body) or "MovieLens reported failure"
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
