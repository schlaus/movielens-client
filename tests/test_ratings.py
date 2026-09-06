"""Ratings are a lazy, paged stream — a heavy account has thousands."""

import inspect

import pytest

from movielens_client import login
from conftest import FakeHTTP, FakeResponse, paged_route, response_from_fixture

EXPLORE = ("GET", "/api/movies/explore")


@pytest.fixture
def rated_http(logged_in_http):
    """Six recorded pages of three, 17 ratings in total, then an empty page 7."""
    logged_in_http.routes[EXPLORE] = paged_route("ratings_")
    return logged_in_http


def test_iter_ratings_is_a_generator_and_fetches_nothing_until_iterated(rated_http):
    session = login("u", "p", http=rated_http)
    before = len(rated_http.calls)

    stream = session.iter_ratings(page_size=3)

    assert inspect.isgenerator(stream)
    assert len(rated_http.calls) == before, "iter_ratings() eagerly fetched"


def test_streaming_four_ratings_costs_exactly_two_pages(rated_http):
    """The laziness test with teeth.

    Pages hold three. Four items must cost two requests — no more. If
    iter_ratings built a list first it would issue seven, and an
    ``isinstance(x, Iterator)`` check would not have noticed.
    """
    session = login("u", "p", http=rated_http)
    before = len(rated_http.calls)

    stream = session.iter_ratings(page_size=3)
    first_three = [next(stream) for _ in range(3)]
    assert len(rated_http.calls) - before == 1
    next(stream)
    assert len(rated_http.calls) - before == 2

    assert len({r.movie_id for r in first_three}) == 3


def test_paging_covers_the_whole_account_without_duplicates(rated_http):
    session = login("u", "p", http=rated_http)

    ratings = list(session.iter_ratings(page_size=3))

    assert len(ratings) == 17  # pager.totalItems in the recorded fixtures
    assert len({r.movie_id for r in ratings}) == 17
    pages_requested = [c["params"]["page"] for c in rated_http.calls if c["path"].endswith("explore")]
    assert pages_requested == sorted(pages_requested)


def test_paging_starts_at_page_one(rated_http):
    """MovieLens answers ``page=0`` with a 500; the API is one-indexed."""
    session = login("u", "p", http=rated_http)

    next(session.iter_ratings(page_size=3))

    explore_calls = [c for c in rated_http.calls if c["path"].endswith("explore")]
    assert explore_calls[0]["params"]["page"] == 1
    assert explore_calls[0]["params"]["hasRated"] == "yes"
    assert explore_calls[0]["params"]["pageSize"] == 3


def test_ratings_carry_canonical_imdb_ids_and_rating_data(rated_http):
    session = login("u", "p", http=rated_http)

    first = next(session.iter_ratings(page_size=3))

    assert first.movie.title == "The Shawshank Redemption"
    assert first.imdb_id == "tt0111161"
    assert first.movie_id == 318
    assert first.movie_id != first.imdb_id
    assert first.rating == 4.0
    assert first.rated_at is not None
    assert first.rated_at.year == 2026
    assert first.prediction == pytest.approx(4.024140523632488)


def test_every_streamed_rating_has_a_tt_prefixed_imdb_id(rated_http):
    session = login("u", "p", http=rated_http)

    for rating in session.iter_ratings(page_size=3):
        assert rating.imdb_id is not None
        assert rating.imdb_id.startswith("tt")
        assert len(rating.imdb_id) >= 9
        assert str(rating.movie_id) not in (rating.imdb_id, rating.imdb_id[2:].lstrip("0"))


def test_iteration_stops_when_a_page_comes_back_empty_even_if_the_pager_lies(logged_in_http):
    """Termination must not depend on ``pager.totalItems`` being truthful."""
    lying = response_from_fixture("ratings_page1")
    lying._payload["data"]["pager"]["totalItems"] = 10_000

    def route(params, _payload):
        return lying if int(params["page"]) == 1 else response_from_fixture("ratings_page7")

    logged_in_http.routes[EXPLORE] = route
    session = login("u", "p", http=logged_in_http)

    assert len(list(session.iter_ratings(page_size=3))) == 3


def test_a_server_that_ignores_the_page_param_stops_instead_of_looping_forever(logged_in_http):
    """Guard against an infinite stream if paging silently stops advancing."""
    logged_in_http.routes[EXPLORE] = lambda params, payload: response_from_fixture("ratings_page1")
    session = login("u", "p", http=logged_in_http)

    ratings = list(session.iter_ratings(page_size=3))

    assert len(ratings) == 3


def test_an_account_with_no_ratings_yields_nothing_and_does_not_raise(logged_in_http):
    logged_in_http.routes[EXPLORE] = lambda params, payload: response_from_fixture("ratings_page7")
    session = login("u", "p", http=logged_in_http)

    assert list(session.iter_ratings(page_size=3)) == []
