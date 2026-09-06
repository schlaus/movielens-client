"""Live smoke test against the real service.

Not part of the suite: excluded by ``addopts = -m 'not live'`` in pyproject and
skipped outright when credentials are absent. Run it deliberately:

    pytest -m live

It rates a film on the test account — that account exists for this purpose.
The write is restored to the value it had beforehand.
"""

import pytest

from movielens_client import AuthenticationError, login
from movielens_client.models import Prediction, Rating

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def live_session(live_credentials):
    username, password = live_credentials
    session = login(username, password)
    yield session
    session.close()


def test_login_and_account(live_session):
    account = live_session.account()
    assert account.user_name
    assert account.num_ratings >= 0


def test_bad_password_is_rejected_as_an_auth_error(live_credentials):
    username, _ = live_credentials
    with pytest.raises(AuthenticationError):
        login(username, "definitely-not-the-password-zzz")


def test_paged_ratings_stream(live_session):
    expected = live_session.account().num_ratings
    seen = []
    for rating in live_session.iter_ratings(page_size=3):
        assert isinstance(rating, Rating)
        assert rating.imdb_id is None or rating.imdb_id.startswith("tt")
        seen.append(rating.movie_id)
    assert len(seen) == expected
    assert len(set(seen)) == len(seen)


def test_predictions_stream(live_session):
    # Deliberately the default page size: that is the code path the consumer
    # takes, and the prediction feed is the only one long enough to fill it.
    stream = live_session.iter_predictions()
    batch = [next(stream) for _ in range(5)]
    assert all(isinstance(p, Prediction) for p in batch)
    assert all(p.imdb_id is None or p.imdb_id.startswith("tt") for p in batch)
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
