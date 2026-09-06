"""Two failure modes, deliberately kept apart.

The consumer defers work when MovieLens is unreachable and must *not* defer
when the credentials are wrong — otherwise a bad password looks like a
permanent outage. Collapsing these into one type destroys that distinction, so
they are separate classes and every raise site picks one on purpose.
"""

from __future__ import annotations

__all__ = ["MovieLensError", "AuthenticationError", "MovieLensAPIError"]


class MovieLensError(Exception):
    """Base class, so a consumer can catch everything from this package."""


class AuthenticationError(MovieLensError):
    """Credentials were rejected, or the session is no longer accepted.

    Not transient. Retrying with the same credentials will not help; the user
    has to supply new ones.
    """


class MovieLensAPIError(MovieLensError):
    """MovieLens was unreachable, slow, or answered with something unusable.

    Transient as far as this package is concerned: connection errors, timeouts,
    5xx, non-JSON bodies, and 4xx rejections of a request's contents all land
    here. The consumer should back off and try again later.
    """

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code
