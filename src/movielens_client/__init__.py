"""A thin, disposable client for movielens.org's unpublished JSON API.

    from movielens_client import login, AuthenticationError, MovieLensAPIError

Stores nothing, logs nothing, encrypts nothing: the caller owns persistence.
"""

from .client import DEFAULT_BASE_URL, MovieLensSession, login
from .errors import AuthenticationError, MovieLensAPIError, MovieLensError
from .models import (
    Account,
    ExportedRating,
    Movie,
    MovieDetail,
    Prediction,
    Rating,
    canonical_imdb_id,
)

__version__ = "0.1.0"

__all__ = [
    "login",
    "MovieLensSession",
    "DEFAULT_BASE_URL",
    "MovieLensError",
    "AuthenticationError",
    "MovieLensAPIError",
    "Account",
    "Movie",
    "ExportedRating",
    "Rating",
    "Prediction",
    "MovieDetail",
    "canonical_imdb_id",
    "__version__",
]
