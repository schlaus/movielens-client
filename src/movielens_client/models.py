"""Typed results. The consumer never sees a raw response dict."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Any, Iterator

from .errors import MovieLensAPIError

__all__ = [
    "canonical_imdb_id",
    "Account",
    "Movie",
    "Rating",
    "Prediction",
    "MovieDetail",
    "ExportedRating",
]


def canonical_imdb_id(raw: Any) -> str | None:
    """Convert MovieLens' ``imdbMovieId`` to canonical IMDb form.

    MovieLens returns a bare, zero-padded numeric string — ``"0133093"`` for
    The Matrix — with no ``tt`` prefix. IMDb's canonical form is ``tt0133093``,
    zero-padded to at least seven digits. Returns ``None`` for a missing,
    empty or non-numeric value rather than inventing an id; a consumer joining
    on IMDb ids would rather have a hole than a wrong key.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text[:2].lower() == "tt":
        text = text[2:]
    if not text.isdigit():
        return None
    return f"tt{text.zfill(7)}"


@contextmanager
def _shape_errors(what: str) -> Iterator[None]:
    """Turn payload-shape surprises into MovieLensAPIError.

    This is an unpublished API with no contract, so drift is the failure most
    likely to arrive unannounced. A consumer wrapping its mirror loop in
    ``except MovieLensError`` should defer when MovieLens starts returning
    something unrecognisable, not crash on a TypeError raised from inside a
    dataclass constructor — that exception escapes the hierarchy entirely.
    """
    try:
        yield
    except (KeyError, TypeError, ValueError) as exc:
        raise MovieLensAPIError(
            f"MovieLens returned an unexpected {what} payload: {exc!r}"
        ) from exc


def _parse_timestamp(raw: Any) -> datetime | None:
    """Parse MovieLens' ``"2026-09-06T08:33:03.000Z"`` into an aware datetime."""
    if not raw:
        return None
    text = str(raw)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _float_or_none(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class Account:
    user_name: str
    num_ratings: int

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> "Account":
        account = (data or {}).get("account") or {}
        with _shape_errors("account"):
            return cls(
                user_name=account.get("userName") or "",
                num_ratings=int(data.get("numRatings") or 0),
            )


@dataclass(frozen=True)
class Movie:
    """A MovieLens movie. ``movie_id`` and ``imdb_id`` are never interchangeable."""

    movie_id: int
    imdb_id: str | None
    tmdb_id: int | None
    title: str
    year: int | None
    genres: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "Movie":
        """Build a Movie, or raise MovieLensAPIError if the shape is wrong.

        ``movieId`` is the one field with no sensible default: without it the
        caller cannot address the movie at all. Everything else degrades to
        None or empty, matching how a null ``movieUserData`` is tolerated.
        """
        payload = payload or {}
        tmdb = payload.get("tmdbMovieId")
        year = payload.get("releaseYear")
        with _shape_errors("movie"):
            return cls(
                movie_id=int(payload["movieId"]),
                imdb_id=canonical_imdb_id(payload.get("imdbMovieId")),
                tmdb_id=int(tmdb) if tmdb is not None else None,
                title=payload.get("title") or "",
                year=int(year) if year else None,
                genres=tuple(payload.get("genres") or ()),
            )


class _HasMovie:
    """Shared conveniences so callers can skip a level of attribute access."""

    movie: Movie

    @property
    def movie_id(self) -> int:
        return self.movie.movie_id

    @property
    def imdb_id(self) -> str | None:
        return self.movie.imdb_id

    @property
    def title(self) -> str:
        return self.movie.title


@dataclass(frozen=True)
class Rating(_HasMovie):
    #: ``rating`` is populated for everything the ratings stream yields, but is
    #: typed optional because MovieLens can return a null in the same field.
    movie: Movie
    rating: float | None
    rated_at: datetime | None
    prediction: float | None


@dataclass(frozen=True)
class Prediction(_HasMovie):
    movie: Movie
    prediction: float | None


@dataclass(frozen=True)
class MovieDetail(_HasMovie):
    movie: Movie
    rating: float | None
    rated_at: datetime | None
    prediction: float | None
    wishlist: bool
    hidden: bool


def _user_data(result: dict[str, Any]) -> dict[str, Any]:
    """Pull ``movieUserData`` out of a search result or movie-detail payload.

    The per-user fields — including ``prediction`` — live *here*, never at the
    top level of the result. MovieLens does expose a top-level ``prediction``
    key on some responses and it is always null; reading it is the documented
    way to conclude, wrongly, that predictions are unavailable.
    """
    return result.get("movieUserData") or {}


def rating_from_result(result: dict[str, Any]) -> Rating:
    user_data = _user_data(result)
    return Rating(
        movie=Movie.from_payload(result.get("movie") or {}),
        rating=_float_or_none(user_data.get("rating")),
        rated_at=_parse_timestamp(user_data.get("dateRated")),
        prediction=_float_or_none(user_data.get("prediction")),
    )


def prediction_from_result(result: dict[str, Any]) -> Prediction:
    user_data = _user_data(result)
    return Prediction(
        movie=Movie.from_payload(result.get("movie") or {}),
        prediction=_float_or_none(user_data.get("prediction")),
    )


def detail_from_result(result: dict[str, Any]) -> MovieDetail:
    user_data = _user_data(result)
    return MovieDetail(
        movie=Movie.from_payload(result.get("movie") or {}),
        rating=_float_or_none(user_data.get("rating")),
        rated_at=_parse_timestamp(user_data.get("dateRated")),
        prediction=_float_or_none(user_data.get("prediction")),
        wishlist=bool(user_data.get("wishlist")),
        hidden=bool(user_data.get("hidden")),
    )


# ------------------------------------------------------------------ CSV export

#: The columns of ``/api/users/me/movielens-ratings.csv``, verified live on
#: 2026-09-06. Read by name, never by position: an added column would
#: otherwise shift ``title`` and nothing would say so.
EXPORT_COLUMNS = (
    "movie_id",
    "imdb_id",
    "tmdb_id",
    "rating",
    "average_rating",
    "title",
)


@dataclass(frozen=True)
class ExportedRating:
    """One row of the CSV export.

    Deliberately flat, and deliberately not a :class:`Movie`. The CSV carries
    no release year and no genres, so wrapping a Movie would report ``year:
    None`` for a film whose year is sitting in the title string — structure
    invented where there is none. ``title`` is passed through exactly as
    exported, year in parentheses included.

    ``rating`` is this account's. ``average_rating`` is the community mean and
    is never the user's opinion of anything.
    """

    movie_id: int
    imdb_id: str | None
    tmdb_id: int | None
    rating: float | None
    average_rating: float | None
    title: str


def _int_or_none(raw: Any) -> int | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def parse_ratings_csv(text: str) -> list[ExportedRating]:
    """Parse the ratings export into typed rows.

    Parsed with :mod:`csv`, never by splitting on commas or newlines: titles
    carry commas, escaped quotes and — in principle — embedded newlines, and
    the body ends with a trailing CRLF that a line splitter turns into a
    phantom row.

    The header is checked before any row is read. An expired session can
    answer ``200`` with an HTML login page; parsed loosely that is zero rows,
    and a consumer mirroring deletions would read it as an account that has
    rated nothing. A body whose header is not the export's is a shape problem
    and raises :class:`MovieLensAPIError` — extra columns are tolerated,
    missing ones are not.
    """
    reader = csv.DictReader(io.StringIO(text))
    columns = set(reader.fieldnames or ())
    missing = [name for name in EXPORT_COLUMNS if name not in columns]
    if missing:
        raise MovieLensAPIError(
            "MovieLens did not return the ratings export: its header is missing "
            f"{', '.join(missing)}"
        )

    rows: list[ExportedRating] = []
    with _shape_errors("ratings export"):
        for row in reader:
            movie_id = _int_or_none(row.get("movie_id"))
            if movie_id is None:
                raise MovieLensAPIError(
                    "MovieLens returned an export row with no usable movie_id"
                )
            rows.append(
                ExportedRating(
                    movie_id=movie_id,
                    imdb_id=canonical_imdb_id(row.get("imdb_id")),
                    tmdb_id=_int_or_none(row.get("tmdb_id")),
                    rating=_float_or_none(row.get("rating")),
                    average_rating=_float_or_none(row.get("average_rating")),
                    title=row.get("title") or "",
                )
            )
    return rows
