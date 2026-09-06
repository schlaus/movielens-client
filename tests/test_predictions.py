"""The prediction lives at movieUserData.prediction, nowhere else."""

import pytest

from movielens_client import login
from movielens_client.models import prediction_from_result
from conftest import FakeResponse, paged_route, response_from_fixture

EXPLORE = ("GET", "/api/movies/explore")


@pytest.fixture
def predicting_http(logged_in_http):
    logged_in_http.routes[EXPLORE] = paged_route("predictions_")
    return logged_in_http


def test_prediction_is_read_from_movie_user_data(predicting_http):
    session = login("u", "p", http=predicting_http)

    first = next(session.iter_predictions(page_size=3))

    assert first.movie.title == "Spider-Man: Into the Spider-Verse"
    assert first.imdb_id == "tt4633694"
    assert first.prediction == pytest.approx(4.118813907812573)


def test_a_top_level_prediction_key_never_shadows_the_real_one():
    """The trap that cost an earlier investigation 17 ratings.

    MovieLens exposes a top-level ``prediction`` on some responses and it is
    always null. Read it instead of ``movieUserData.prediction`` and you
    conclude predictions are unavailable. This result carries both, with the
    top-level one null, so reading the wrong path yields None and fails here.
    """
    result = {
        "movieId": 2571,
        "prediction": None,
        "predictionDetails": None,
        "movie": {"movieId": 2571, "imdbMovieId": "0133093", "title": "The Matrix"},
        "movieUserData": {"rating": None, "prediction": 4.5},
    }

    assert prediction_from_result(result).prediction == 4.5


def test_predictions_ask_the_api_to_sort_by_prediction(predicting_http):
    session = login("u", "p", http=predicting_http)

    next(session.iter_predictions(page_size=3))

    call = [c for c in predicting_http.calls if c["path"].endswith("explore")][0]
    assert call["params"]["hasRated"] == "no"
    assert call["params"]["sortBy"] == "prediction"
    assert call["params"]["page"] == 1


def test_predictions_stream_lazily_across_pages(predicting_http):
    """The live feed reports tens of thousands of items; never materialise it."""
    session = login("u", "p", http=predicting_http)
    before = len(predicting_http.calls)

    stream = session.iter_predictions(page_size=3)
    assert len(predicting_http.calls) == before
    [next(stream) for _ in range(4)]
    assert len(predicting_http.calls) - before == 2


def test_a_cold_start_account_yields_null_predictions_rather_than_raising(logged_in_http):
    """Predictions are null until enough films are rated. That is not an error."""
    cold = response_from_fixture("predictions_page1")
    for result in cold._payload["data"]["searchResults"]:
        result["movieUserData"]["prediction"] = None
        result["movieUserData"]["predictionDetails"] = None
    cold._payload["data"]["pager"]["totalItems"] = 3

    logged_in_http.routes[EXPLORE] = lambda params, payload: (
        cold if int(params["page"]) == 1 else response_from_fixture("ratings_page7")
    )
    session = login("u", "p", http=logged_in_http)

    predictions = list(session.iter_predictions(page_size=3))

    assert len(predictions) == 3
    assert all(p.prediction is None for p in predictions)
    assert all(p.imdb_id.startswith("tt") for p in predictions)


def test_an_empty_prediction_set_yields_nothing_and_does_not_raise(logged_in_http):
    logged_in_http.routes[EXPLORE] = lambda params, payload: response_from_fixture("ratings_page7")
    session = login("u", "p", http=logged_in_http)

    assert list(session.iter_predictions(page_size=3)) == []
