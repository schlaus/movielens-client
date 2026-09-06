"""Unexpected payload shapes must stay inside the package's error hierarchy.

This is an unpublished API with no contract. A consumer wrapping its mirror
loop in ``except MovieLensError`` should defer when MovieLens starts returning
something unrecognisable, not crash with a TypeError from inside a dataclass
constructor.
"""

import pytest

from movielens_client import MovieLensAPIError, MovieLensError, login
from movielens_client.models import Movie, rating_from_result
from conftest import FakeResponse, response_from_fixture

EXPLORE = ("GET", "/api/movies/explore")


def test_a_movie_payload_without_an_id_raises_a_package_error():
    with pytest.raises(MovieLensAPIError):
        Movie.from_payload({"title": "Nameless", "imdbMovieId": "0133093"})


def test_a_non_numeric_movie_id_raises_a_package_error():
    with pytest.raises(MovieLensAPIError):
        Movie.from_payload({"movieId": "not-a-number"})


def test_a_search_result_missing_its_movie_block_raises_a_package_error():
    with pytest.raises(MovieLensAPIError):
        rating_from_result({"movieId": 2571, "movieUserData": {"rating": 4.0}})


def test_shape_drift_mid_stream_is_catchable_as_a_movielens_error(logged_in_http):
    """The consumer catches MovieLensError and defers; it never sees TypeError."""
    drifted = response_from_fixture("ratings_page1")
    drifted._payload["data"]["searchResults"][1]["movie"] = {"title": "no id here"}
    logged_in_http.routes[EXPLORE] = lambda params, payload: drifted
    session = login("u", "p", http=logged_in_http)

    stream = session.iter_ratings(page_size=3)
    assert next(stream).movie_id == 318

    with pytest.raises(MovieLensError) as excinfo:
        next(stream)
    assert not isinstance(excinfo.value, TypeError)


def test_a_null_movie_user_data_block_is_tolerated(logged_in_http):
    """Absent per-user data is a hole, not drift: yield it with empty fields."""
    sparse = response_from_fixture("ratings_page1")
    sparse._payload["data"]["searchResults"][0]["movieUserData"] = None
    sparse._payload["data"]["pager"]["totalItems"] = 3
    logged_in_http.routes[EXPLORE] = lambda params, payload: sparse
    session = login("u", "p", http=logged_in_http)

    first = next(session.iter_ratings(page_size=3))

    assert first.movie_id == 318
    assert first.imdb_id == "tt0111161"
    assert first.rating is None
    assert first.prediction is None


def test_a_search_results_block_that_is_not_a_list_raises_a_package_error(logged_in_http):
    body = {"status": "success", "data": {"searchResults": {"unexpected": "object"},
                                          "pager": {"totalItems": 1}}}
    logged_in_http.routes[EXPLORE] = FakeResponse(200, body)
    session = login("u", "p", http=logged_in_http)

    with pytest.raises(MovieLensError):
        next(session.iter_ratings(page_size=3))
