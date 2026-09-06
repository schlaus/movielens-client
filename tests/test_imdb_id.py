"""The consumer joins on IMDb ids. A MovieLens id must never reach it as one."""

import json
import pathlib

import pytest

from movielens_client.models import canonical_imdb_id

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("0133093", "tt0133093"),   # as MovieLens actually returns it
        ("4633694", "tt4633694"),   # already seven digits
        ("133093", "tt0133093"),    # short: would fail without zero-padding
        ("42", "tt0000042"),        # very short
        ("12345678", "tt12345678"), # eight digits: must not be truncated
        ("tt0133093", "tt0133093"), # never double-prefix
        (133093, "tt0133093"),      # tolerate an int
        (None, None),
        ("", None),
        ("   ", None),
        ("not-an-id", None),
    ],
)
def test_canonical_imdb_id(raw, expected):
    assert canonical_imdb_id(raw) == expected


def test_recorded_fixtures_are_not_already_canonical():
    """Guard the guard: if MovieLens ever returned tt-prefixed ids, the padding
    test above would pass vacuously. Assert the recorded shape is the raw one."""
    raw_ids = []
    for name in ("ratings_page1", "predictions_page1"):
        body = json.loads((FIXTURES / f"{name}.json").read_text())["json"]
        for result in body["data"]["searchResults"]:
            raw_ids.append(result["movie"]["imdbMovieId"])
    assert raw_ids, "fixtures carry no imdbMovieId to check"
    assert all(not str(i).startswith("tt") for i in raw_ids)
    assert any(str(i).startswith("0") for i in raw_ids), "no zero-padded id recorded"
