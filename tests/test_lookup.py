"""Resolving an IMDb id to a MovieLens movie id.

Writes need a ``movieId`` and MovieLens offers no direct lookup, so the only
route is a *title* search matched on ``movie.imdbMovieId``. Two documented
traps make this worth pinning hard: ``explore?imdbMovieId=…`` silently ignores
the parameter and answers an unrelated default page, and ``explore?q=tt…``
answers nothing. Neither fails loudly, so a lookup built on either would rate
the wrong film — or nothing — without a word.

The recorded ``Parasite`` search is the fixture throughout: nine films across
two pages, three of them titled exactly "Parasite", and the one a consumer
usually wants sits fourth on page one.
"""

import copy

import pytest

from movielens_client import AuthenticationError, MovieLensAPIError, login
from movielens_client.models import Movie
from conftest import fixture, paged_route, response_from_fixture

EXPLORE = ("GET", "/api/movies/explore")

# Recorded from the live "Parasite" search on 2026-09-06.
PARASITE_2019 = ("tt6751668", 202439)  # fourth result on page one
LES_PARASITES = ("tt0182357", 99243)  # third on page one, title is not "Parasite"
PARASITE_EVE = ("tt0119860", 171853)  # third on page two
THE_MATRIX = "tt0133093"  # in MovieLens, but not in this search


@pytest.fixture
def search_http(logged_in_http):
    """Two recorded pages of five, nine films in total, then an empty page."""
    logged_in_http.routes[EXPLORE] = paged_route("search_parasite_")
    return logged_in_http


def explore_calls(http):
    return [c for c in http.calls if c["path"].endswith("/explore")]


def test_the_fixture_would_defeat_a_lookup_that_took_the_first_result():
    """Guards the guard: if the recording ever changes, the tests below stop
    proving what they claim, so assert the shape they depend on."""
    page1 = fixture("search_parasite_page1")["json"]["data"]["searchResults"]
    ids = [r["movie"]["imdbMovieId"] for r in page1]

    assert len(ids) == 5
    assert ids[0] != PARASITE_2019[0][2:]
    titles = [r["movie"]["title"] for r in page1]
    assert titles.count("Parasite") == 3, "three same-titled films, three ids"


def test_finds_the_right_film_among_several_with_the_same_title(search_http):
    session = login("u", "p", http=search_http)

    found = session.find_movie_by_imdb_id(PARASITE_2019[0], "Parasite")

    assert isinstance(found, Movie)
    assert found.movie_id == PARASITE_2019[1]
    assert found.imdb_id == PARASITE_2019[0]
    assert found.year == 2019


def test_the_match_is_not_the_first_result(search_http):
    """Position means nothing: the wanted film is fourth in the recording."""
    session = login("u", "p", http=search_http)

    found = session.find_movie_by_imdb_id(PARASITE_2019[0], "Parasite")

    first = fixture("search_parasite_page1")["json"]["data"]["searchResults"][0]
    assert found.movie_id != first["movieId"]


def test_the_match_does_not_have_to_equal_the_search_title(search_http):
    """Titles differ by language, subtitle and punctuation. Ids do not."""
    session = login("u", "p", http=search_http)

    found = session.find_movie_by_imdb_id(LES_PARASITES[0], "Parasite")

    assert found.movie_id == LES_PARASITES[1]
    assert found.title == "Les Parasites"


def test_the_match_may_be_on_a_later_page(search_http):
    session = login("u", "p", http=search_http)

    found = session.find_movie_by_imdb_id(PARASITE_EVE[0], "Parasite")

    assert found.movie_id == PARASITE_EVE[1]
    assert [c["params"]["page"] for c in explore_calls(search_http)] == [1, 2]


def test_searching_stops_at_the_page_holding_the_match(search_http):
    """A match on page one must not cost page two as well."""
    session = login("u", "p", http=search_http)

    session.find_movie_by_imdb_id(PARASITE_2019[0], "Parasite")

    assert len(explore_calls(search_http)) == 1


def test_an_id_none_of_the_results_carry_is_none_not_a_wrong_film(search_http):
    """The results are not empty — nine films come back, none of them this one.

    An implementation that always returned None would pass against an empty
    search; it cannot pass against this one, because the tests above need real
    matches out of the same fixture.
    """
    session = login("u", "p", http=search_http)

    assert session.find_movie_by_imdb_id(THE_MATRIX, "Parasite") is None
    assert len(explore_calls(search_http)) == 2  # the whole result set was read


def test_a_film_with_no_imdb_id_never_stands_in_for_one(logged_in_http):
    """A result MovieLens carries no IMDb id for must never be the answer.

    ``None == None`` is how a lookup quietly rates an unrelated film, and the
    client blocks it in two places: it refuses to search at all for a wanted id
    that canonicalises to nothing (pinned by
    ``test_an_unusable_imdb_id_matches_nothing_and_asks_nothing``, which is
    where that half is proved), and it skips results with no id of their own.
    The second guard is unreachable while the first stands, so this test pins
    the reachable behaviour: a film with a stripped id, sitting first, is
    returned neither for an id nothing carries nor in place of the real match
    further down.
    """
    page1 = response_from_fixture("search_parasite_page1")
    results = page1._payload["data"]["searchResults"]
    results[0]["movie"]["imdbMovieId"] = None

    def route(params, _payload):
        page = int(params["page"])
        return page1 if page == 1 else response_from_fixture(f"search_parasite_page{page}")

    logged_in_http.routes[EXPLORE] = route
    session = login("u", "p", http=logged_in_http)

    # Nothing carries this id now, and the id-less film must not stand in.
    assert session.find_movie_by_imdb_id(THE_MATRIX, "Parasite") is None
    # And the film that does carry the wanted id is still found.
    assert (
        session.find_movie_by_imdb_id(PARASITE_2019[0], "Parasite").movie_id
        == PARASITE_2019[1]
    )


@pytest.mark.parametrize("unusable", ["", "   ", None, "tt", "not-an-id"])
def test_an_unusable_imdb_id_matches_nothing_and_asks_nothing(
    search_http, unusable
):
    """An id that canonicalises to nothing cannot match, so do not search."""
    session = login("u", "p", http=search_http)

    assert session.find_movie_by_imdb_id(unusable, "Parasite") is None
    assert explore_calls(search_http) == []


def test_a_blank_title_asks_nothing(search_http):
    """There is nothing to search with; MovieLens is not asked to guess."""
    session = login("u", "p", http=search_http)

    assert session.find_movie_by_imdb_id(PARASITE_2019[0], "   ") is None
    assert explore_calls(search_http) == []


@pytest.mark.parametrize("form", ["tt0119860", "0119860", "119860", "TT0119860"])
def test_the_wanted_id_is_accepted_in_any_of_its_forms(search_http, form):
    """MovieLens' own zero-padded form included — the consumer has both."""
    session = login("u", "p", http=search_http)

    found = session.find_movie_by_imdb_id(form, "Parasite")

    assert found.movie_id == PARASITE_EVE[1]


def test_a_search_that_finds_nothing_is_none_and_not_an_error(logged_in_http):
    """A film MovieLens does not carry is a normal answer, not a failure."""
    logged_in_http.routes[EXPLORE] = lambda params, payload: response_from_fixture(
        "search_imdb_id_as_query"
    )
    session = login("u", "p", http=logged_in_http)

    assert session.find_movie_by_imdb_id(THE_MATRIX, "Some Obscure Film") is None


def test_the_search_is_by_title_and_never_by_the_id(search_http):
    """The two traps, in the negative: what the request must not contain."""
    session = login("u", "p", http=search_http)

    session.find_movie_by_imdb_id(THE_MATRIX, "Parasite")

    calls = explore_calls(search_http)
    assert calls, "nothing was searched at all"
    for call in calls:
        assert call["params"]["q"] == "Parasite"
        assert "imdbMovieId" not in call["params"]


def test_the_lookup_survives_a_service_that_springs_both_traps(logged_in_http):
    """Both traps, in the positive: served the recorded trap responses.

    ``imdbMovieId`` answers an unrelated default page with a 10,000-item pager
    — a lookup built on it would return whatever film happened to be first —
    and an id as the query answers nothing at all. A lookup that touched
    either would fail here: the first with the wrong film, the second with
    None.
    """

    def route(params, _payload):
        if "imdbMovieId" in (params or {}):
            return response_from_fixture("explore_imdb_param_trap")
        if str(params.get("q", "")).lower().startswith("tt"):
            return response_from_fixture("search_imdb_id_as_query")
        return response_from_fixture(f"search_parasite_page{int(params['page'])}")

    logged_in_http.routes[EXPLORE] = route
    session = login("u", "p", http=logged_in_http)

    found = session.find_movie_by_imdb_id(PARASITE_2019[0], "Parasite")

    assert found is not None and found.movie_id == PARASITE_2019[1]


#: How far the huge_search route below will go before it runs dry. Deliberately
#: finite: an unbounded lookup should fail these tests on the request count,
#: not hang, because a hanging test reports nothing useful.
HUGE_SEARCH_PAGES = 60


def huge_search(params, _payload):
    """A result set far larger than any lookup should be willing to walk.

    Every page holds different films — a page of repeats would stop the walk
    on the non-advancing-page guard rather than on the bound under test — and
    the pager claims 10,000 items, as the recorded trap page really does.
    """
    page = int(params["page"])
    response = response_from_fixture("search_parasite_page1")
    data = response._payload["data"]
    data["pager"].update({"totalItems": 10_000, "currentPage": page})
    if page > HUGE_SEARCH_PAGES:
        data["searchResults"] = []
        return response
    for offset, result in enumerate(data["searchResults"]):
        unique = page * 1000 + offset
        result["movieId"] = unique
        result["movie"]["movieId"] = unique
        result["movie"]["imdbMovieId"] = f"{unique:07d}"
    return response


#: The candidate ceiling the default bounds are meant to buy: pages times page
#: size. It is the number that decides when a None stops meaning "MovieLens
#: does not carry it" and starts meaning "we did not look far enough", so it is
#: asserted through behaviour below rather than left to two constants that
#: could be halved without a test noticing.
DEFAULT_CANDIDATE_CEILING = 200


def deep_search(params, _payload):
    """A search that answers with as many distinct films as are asked for.

    Every candidate carries a unique IMDb id derived from its position in the
    whole result set, so a test can ask for the Nth candidate by name and find
    out exactly how deep the lookup is willing to go.
    """
    page = int(params["page"])
    page_size = int(params["pageSize"])
    response = response_from_fixture("search_parasite_page1")
    data = response._payload["data"]
    data["pager"].update(
        {"totalItems": 10_000, "currentPage": page, "itemsPerPage": page_size}
    )
    template = data["searchResults"][0]
    results = []
    for offset in range(page_size):
        index = (page - 1) * page_size + offset
        result = copy.deepcopy(template)
        result["movieId"] = 900000 + index
        result["movie"]["movieId"] = 900000 + index
        result["movie"]["imdbMovieId"] = f"{9000000 + index}"
        results.append(result)
    data["searchResults"] = results
    return response


def candidate_id(position: int) -> str:
    """The canonical IMDb id of the ``position``-th candidate, 1-based."""
    return f"tt{9000000 + position - 1}"


def test_the_default_bounds_reach_two_hundred_candidates(logged_in_http):
    """The last candidate inside the ceiling is still found.

    Counting requests is not enough: four pages of five would pass a
    request-count test while quietly cutting the search to twenty candidates,
    and a caller cannot tell a too-shallow search from a film MovieLens does
    not have.
    """
    logged_in_http.routes[EXPLORE] = deep_search
    session = login("u", "p", http=logged_in_http)

    last_inside = session.find_movie_by_imdb_id(
        candidate_id(DEFAULT_CANDIDATE_CEILING), "Parasite"
    )

    assert last_inside is not None
    assert last_inside.imdb_id == candidate_id(DEFAULT_CANDIDATE_CEILING)


def test_the_default_bounds_stop_at_two_hundred_candidates(logged_in_http):
    """And the first candidate past it is not, so the ceiling is exact."""
    logged_in_http.routes[EXPLORE] = deep_search
    session = login("u", "p", http=logged_in_http)

    assert (
        session.find_movie_by_imdb_id(
            candidate_id(DEFAULT_CANDIDATE_CEILING + 1), "Parasite"
        )
        is None
    )


def test_the_search_is_bounded(logged_in_http):
    """A query matching thousands of films must not walk them all.

    The recorded ``imdbMovieId`` trap page reports 10,000 items; a search that
    kept paging would issue two thousand requests before giving up.
    """
    logged_in_http.routes[EXPLORE] = huge_search
    session = login("u", "p", http=logged_in_http)

    assert session.find_movie_by_imdb_id(THE_MATRIX, "Parasite", max_pages=3) is None
    assert len(explore_calls(logged_in_http)) == 3


def test_the_bound_has_a_default_that_is_not_unlimited(logged_in_http):
    """The caller should not have to remember to pass one."""
    logged_in_http.routes[EXPLORE] = huge_search
    session = login("u", "p", http=logged_in_http)

    assert session.find_movie_by_imdb_id(THE_MATRIX, "Parasite") is None
    assert 0 < len(explore_calls(logged_in_http)) < HUGE_SEARCH_PAGES


def test_the_account_streams_are_not_bounded_by_the_search_limit(logged_in_http):
    """The bound belongs to the search, not to the paged readers.

    A mirror that stopped after four pages of ratings would truncate a heavy
    account silently — the failure the ratings iterator is built to avoid.
    """
    logged_in_http.routes[EXPLORE] = huge_search
    session = login("u", "p", http=logged_in_http)

    seen = 0
    for _ in session.iter_ratings(page_size=5):
        seen += 1

    assert seen == HUGE_SEARCH_PAGES * 5


def test_a_rejected_session_during_a_lookup_is_an_auth_error(logged_in_http):
    logged_in_http.routes[EXPLORE] = lambda params, payload: response_from_fixture(
        "unauthenticated_401"
    )
    session = login("u", "p", http=logged_in_http)

    with pytest.raises(AuthenticationError):
        session.find_movie_by_imdb_id(THE_MATRIX, "Parasite")


def test_an_outage_during_a_lookup_is_an_api_error(logged_in_http):
    """Not None: "MovieLens is down" and "MovieLens does not have it" are
    different answers, and only one of them means stop asking."""
    logged_in_http.routes[EXPLORE] = lambda params, payload: response_from_fixture(
        "server_error_500"
    )
    session = login("u", "p", http=logged_in_http)

    with pytest.raises(MovieLensAPIError):
        session.find_movie_by_imdb_id(THE_MATRIX, "Parasite")


def test_a_found_movie_can_be_rated_without_another_lookup(search_http):
    """The point of the feature: a film MovieLens never showed us is rateable."""
    detail = ("GET", f"/api/movies/{PARASITE_2019[1]}")
    search_http.routes[detail] = response_from_fixture("movie_detail")
    search_http.routes[("POST", "/api/users/me/ratings")] = response_from_fixture(
        "rate_success"
    )
    session = login("u", "p", http=search_http)

    found = session.find_movie_by_imdb_id(PARASITE_2019[0], "Parasite")
    # No predicted_rating: rate() has to look one up for the film just found,
    # which is the whole flow — lookup, detail fetch, write.
    session.rate(found.movie_id, 4.0)

    paths = [c["path"] for c in search_http.calls]
    assert paths[-2] == detail[1]
    write = search_http.calls[-1]
    assert write["path"] == "/api/users/me/ratings"
    assert write["json"]["movieId"] == PARASITE_2019[1]
    assert write["json"]["predictedRating"] == pytest.approx(
        3.5245337713991387  # movie_detail fixture's movieUserData.prediction
    )
