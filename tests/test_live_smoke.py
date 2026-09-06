"""Live smoke test against the real service.

Not part of the suite: excluded by ``addopts = -m 'not live'`` in pyproject and
skipped outright when credentials are absent. Run it deliberately:

    pytest -m live

It rates a film on the test account — that account exists for this purpose.
The write is restored to the value it had beforehand.
"""

import re

import pytest

from movielens_client import AuthenticationError, login
from movielens_client.models import Prediction, Rating

pytestmark = pytest.mark.live

#: What the consumer joins on. MovieLens sends "0111161"; anything that is not
#: tt + at least seven digits by the time it reaches here is drift.
CANONICAL_IMDB = re.compile(r"tt\d{7,}")


@pytest.fixture(scope="module")
def live_session(live_credentials):
    username, password = live_credentials
    session = login(username, password)
    yield session
    session.close()


def test_login_and_account(live_session):
    account = live_session.account()
    assert account.user_name
    # Not >= 0: int(... or 0) makes that true even when parsing has broken.
    assert account.num_ratings > 0


def test_bad_password_is_rejected_as_an_auth_error(live_credentials):
    username, _ = live_credentials
    with pytest.raises(AuthenticationError):
        login(username, "definitely-not-the-password-zzz")


def test_paged_ratings_stream(live_session):
    expected = live_session.account().num_ratings
    seen = []
    for rating in live_session.iter_ratings(page_size=3):
        assert isinstance(rating, Rating)
        # Asserted unconditionally, not "is None or startswith". This suite is
        # the only place positioned to notice live imdbMovieId drift — the
        # offline tests read recordings, which by definition cannot drift — so
        # an assertion that also passes when every id is None would be the one
        # check that cannot fail where failing is the whole point.
        assert CANONICAL_IMDB.fullmatch(rating.imdb_id or ""), rating.movie_id
        assert rating.rating is not None
        seen.append(rating.movie_id)
    assert len(seen) == expected
    assert len(set(seen)) == len(seen)


def test_predictions_stream(live_session):
    # Deliberately the default page size: that is the code path the consumer
    # takes, and the prediction feed is the only one long enough to fill it.
    stream = live_session.iter_predictions()
    batch = [next(stream) for _ in range(5)]
    assert all(isinstance(p, Prediction) for p in batch)
    for prediction in batch:
        assert CANONICAL_IMDB.fullmatch(prediction.imdb_id or ""), prediction.movie_id
    # Predictions are null until the account has rated enough films. This
    # account has, so at least one must come back populated.
    assert any(p.prediction is not None for p in batch)


def test_rating_write_round_trips(live_session):
    original = next(live_session.iter_ratings(page_size=1))
    movie_id, previous, previous_prediction = (
        original.movie_id,
        original.rating,
        original.prediction,
    )
    new_value = 3.0 if previous != 3.0 else 3.5
    try:
        total = live_session.rate(movie_id, new_value)
        assert total >= 1
        assert live_session.movie(movie_id).rating == new_value
    finally:
        live_session.rate(movie_id, previous, predicted_rating=previous_prediction)
    assert live_session.movie(movie_id).rating == previous


def test_csv_export_is_complete_and_agrees_with_the_paged_stream(live_session):
    """The export is the sync path, so completeness is the thing to check.

    Row count against the account's own total, and every rating against the
    paged stream it replaces: this is the only place a change to the CSV
    endpoint — a dropped column, a truncated body, a swapped pair of float
    columns — can be noticed at all.
    """
    expected = live_session.account().num_ratings
    rows = live_session.export_ratings()

    assert len(rows) == expected
    for row in rows:
        assert CANONICAL_IMDB.fullmatch(row.imdb_id or ""), row.movie_id
        assert row.title
        assert row.rating is not None
        assert row.average_rating is not None

    # If both columns were read from the same place this would be all-equal.
    assert any(row.rating != row.average_rating for row in rows)

    streamed = {r.movie_id: r.rating for r in live_session.iter_ratings(page_size=50)}
    assert {row.movie_id: row.rating for row in rows} == streamed
