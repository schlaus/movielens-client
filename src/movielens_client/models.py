"""Typed results. The consumer never sees a raw response dict."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

__all__ = [
    "canonical_imdb_id",
    "Account",
    "Movie",
    "Rating",
    "Prediction",
    "MovieDetail",
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
        account = data.get("account") or {}
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
        payload = payload or {}
        tmdb = payload.get("tmdbMovieId")
        year = payload.get("releaseYear")
        return cls(
            movie_id=int(payload.get("movieId")),
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
