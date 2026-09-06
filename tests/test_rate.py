"""Writing a rating. ``predictedRating`` is required by the API."""

import pytest

from movielens_client import AuthenticationError, MovieLensAPIError, login
from conftest import FakeHTTP, response_from_fixture

RATINGS = ("POST", "/api/users/me/ratings")
DETAIL = ("GET", "/api/movies/2571")


@pytest.fixture
def writable_http(logged_in_http):
    logged_in_http.routes[RATINGS] = response_from_fixture("rate_success")
    return logged_in_http


def test_rate_posts_movie_rating_and_prediction_and_returns_the_new_count(writable_http):
    session = login("u", "p", http=writable_http)

    total = session.rate(2571, 4.5, predicted_rating=3.9)

    call = writable_http.calls[-1]
    assert (call["method"], call["path"]) == RATINGS
    assert call["json"] == {"movieId": 2571, "rating": 4.5, "predictedRating": 3.9}
    assert total == 17


def test_omitting_the_prediction_fetches_it_from_the_movie_first(writable_http):
    """``predictedRating`` is mandatory: omit it and the API answers
    ``400 json is missing required double: predictedRating``. When the caller
    has none, look it up on the movie rather than sending a made-up number."""
    writable_http.routes[DETAIL] = response_from_fixture("movie_detail")
    session = login("u", "p", http=writable_http)

    session.rate(2571, 4.5)

    paths = [c["path"] for c in writable_http.calls]
    assert paths.index("/api/movies/2571") < paths.index("/api/users/me/ratings")
    assert writable_http.calls[-1]["json"]["predictedRating"] == pytest.approx(
        3.5245337713991387  # movie_detail fixture's movieUserData.prediction
    )


def test_a_supplied_prediction_skips_the_lookup(writable_http):
    session = login("u", "p", http=writable_http)

    session.rate(2571, 4.5, predicted_rating=3.9)

    assert not any(c["path"] == "/api/movies/2571" for c in writable_http.calls)


def test_a_cold_start_movie_with_no_prediction_still_writes(writable_http):
    """Cold-start accounts have null predictions. Documented behaviour: fall
    back to 0.0, which MovieLens accepts, rather than crash or skip the write."""
    writable_http.routes[DETAIL] = response_from_fixture("movie_detail_no_prediction")
    session = login("u", "p", http=writable_http)

    session.rate(2571, 4.5)

    assert writable_http.calls[-1]["json"]["predictedRating"] == 0.0


def test_a_rejected_rating_is_an_api_error_not_an_auth_error(writable_http):
    writable_http.routes[RATINGS] = response_from_fixture("rate_invalid_value")
    session = login("u", "p", http=writable_http)

    with pytest.raises(MovieLensAPIError) as excinfo:
        session.rate(2571, 9.0, predicted_rating=4.0)

    assert not isinstance(excinfo.value, AuthenticationError)
    assert excinfo.value.status_code == 400
    assert "invalid rating" in str(excinfo.value)


def test_a_missing_predicted_rating_rejection_surfaces_verbatim(writable_http):
    writable_http.routes[RATINGS] = response_from_fixture("rate_missing_predicted")
    session = login("u", "p", http=writable_http)

    with pytest.raises(MovieLensAPIError) as excinfo:
        session.rate(2571, 4.5, predicted_rating=4.0)

    assert "predictedRating" in str(excinfo.value)


def test_movie_detail_exposes_a_canonical_imdb_id_and_the_prediction(writable_http):
    writable_http.routes[("GET", "/api/movies/1")] = response_from_fixture("movie_detail")
    session = login("u", "p", http=writable_http)

    detail = session.movie(1)

    assert detail.movie_id == 1
    assert detail.imdb_id == "tt0114709"
    assert detail.movie.title == "Toy Story"
    assert detail.prediction == pytest.approx(3.5245337713991387)
    assert detail.rating is None


def test_a_missing_movie_is_an_api_error_not_an_auth_error(writable_http):
    writable_http.routes[("GET", "/api/movies/999999999")] = response_from_fixture(
        "movie_detail_404"
    )
    session = login("u", "p", http=writable_http)

    with pytest.raises(MovieLensAPIError) as excinfo:
        session.movie(999999999)

    assert not isinstance(excinfo.value, AuthenticationError)
    assert excinfo.value.status_code == 404
