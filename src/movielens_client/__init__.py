"""A thin, disposable client for movielens.org's unpublished JSON API.

Stores nothing, logs nothing, encrypts nothing: the caller owns persistence.
"""

from .errors import AuthenticationError, MovieLensAPIError, MovieLensError
from .models import (
    Account,
    Movie,
    MovieDetail,
    Prediction,
    Rating,
    canonical_imdb_id,
)

__version__ = "0.1.0"

__all__ = [
    "MovieLensError",
    "AuthenticationError",
    "MovieLensAPIError",
    "Account",
    "Movie",
    "Rating",
    "Prediction",
    "MovieDetail",
    "canonical_imdb_id",
    "__version__",
]
