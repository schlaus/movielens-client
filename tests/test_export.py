"""The bulk CSV export: the whole rating history in one request.

``GET /api/users/me/movielens-ratings.csv`` replaces walking dozens of pages
for a full sync. These tests pin the two ways it can go quietly wrong: reading
``average_rating`` (the community mean) as the user's rating, and parsing the
body with anything less careful than the ``csv`` module.
"""

import csv
import io

import pytest
import requests

from movielens_client import AuthenticationError, MovieLensAPIError, login
from conftest import FakeHTTP, FakeResponse, fixture, response_from_fixture

EXPORT = ("GET", "/api/users/me/movielens-ratings.csv")
EXPLORE = ("GET", "/api/movies/explore")


def csv_response(text: str) -> FakeResponse:
    """A 200 whose body is CSV — ``json()`` raises, as it does on the wire."""
    return FakeResponse(200, None, text)


@pytest.fixture
def export_http(logged_in_http):
    """The account's real 17-row export, recorded from the live service."""
    logged_in_http.routes[EXPORT] = response_from_fixture("ratings_export")
    return logged_in_http


@pytest.fixture
def awkward_http(logged_in_http):
    """The constructed export: commas, quotes, a newline, an 8-digit id."""
    logged_in_http.routes[EXPORT] = response_from_fixture("ratings_export_awkward")
    return logged_in_http


def test_the_whole_history_arrives_in_a_single_request(export_http):
    """One request, not a page walk — that is the entire point of the export."""
    session = login("u", "p", http=export_http)
    before = len(export_http.calls)

    rows = session.export_ratings()

    assert len(rows) == 17  # the account reports numRatings: 17
    requests_made = export_http.calls[before:]
    assert len(requests_made) == 1, "the export cost more than one request"
    assert requests_made[0]["path"] == "/api/users/me/movielens-ratings.csv"
    assert requests_made[0]["method"] == "GET"


def test_the_export_is_a_materialised_list_not_a_lazy_stream(export_http):
    """A single-request whole-body read has nothing to be lazy about.

    Returning a generator would defer shape errors to mid-iteration, where a
    consumer half way through writing rows to its own store would meet them.
    """
    session = login("u", "p", http=export_http)

    rows = session.export_ratings()

    assert isinstance(rows, list)


def test_ids_come_back_canonical_and_are_never_the_movielens_id(export_http):
    session = login("u", "p", http=export_http)

    rows = {row.movie_id: row for row in session.export_ratings()}

    # Recorded: 50,0114814,629,4.0,4.25539,The Usual Suspects (1995)
    assert rows[50].imdb_id == "tt0114814"
    assert rows[50].tmdb_id == 629
    for row in rows.values():
        assert row.imdb_id.startswith("tt")
        assert len(row.imdb_id) >= 9
        assert row.imdb_id[2:].lstrip("0") != str(row.movie_id)


def test_rating_is_the_users_and_average_rating_is_the_communitys(export_http):
    """The two float columns are adjacent and easy to swap.

    The Matrix is rated 4.5 by this account against a community mean of
    4.16544. Reading the wrong column would hand the consumer a rating the
    user never gave — and it would look plausible.
    """
    session = login("u", "p", http=export_http)

    matrix = next(r for r in session.export_ratings() if r.movie_id == 2571)

    assert matrix.rating == 4.5
    assert matrix.average_rating == pytest.approx(4.16544)


def test_the_users_rating_survives_even_when_both_columns_look_like_one(awkward_http):
    """4.0 against a community mean of 3.5: both are plausible user ratings."""
    session = login("u", "p", http=awkward_http)

    row = next(r for r in session.export_ratings() if r.movie_id == 50)

    assert row.rating == 4.0
    assert row.average_rating == 3.5


def test_a_title_containing_a_comma_round_trips(awkward_http):
    session = login("u", "p", http=awkward_http)

    row = next(r for r in session.export_ratings() if r.movie_id == 50)

    assert row.title == "The Usual Suspects, Redux (1995)"
    assert row.tmdb_id == 629
    assert row.rating == 4.0


def test_a_title_containing_quotes_and_a_newline_round_trips(awkward_http):
    """Anything splitting on commas or lines mangles these two rows."""
    session = login("u", "p", http=awkward_http)

    rows = {row.movie_id: row for row in session.export_ratings()}

    assert rows[202439].title == 'Parasite "Gisaengchung" (2019)'
    assert rows[777].title == "A Trip to the Moon\r\n(Le Voyage dans la Lune) (1902)"
    assert rows[777].rating == 2.0


def test_the_trailing_newline_does_not_become_an_empty_row(awkward_http):
    """The real export ends with CRLF; a naive splitter invents a row here."""
    session = login("u", "p", http=awkward_http)

    rows = session.export_ratings()

    assert len(rows) == 4
    assert all(row.title for row in rows)


def test_an_imdb_id_longer_than_seven_digits_is_left_alone(awkward_http):
    """Zero-padding is to *at least* seven digits, never truncation to seven."""
    session = login("u", "p", http=awkward_http)

    row = next(r for r in session.export_ratings() if r.movie_id == 306771)

    assert row.imdb_id == "tt25971184"


def test_a_short_id_is_padded_to_the_canonical_width(awkward_http):
    session = login("u", "p", http=awkward_http)

    row = next(r for r in session.export_ratings() if r.movie_id == 777)

    assert row.imdb_id == "tt0000012"


def test_the_year_is_left_in_the_title_and_not_invented_as_a_field(export_http):
    """The CSV has no year column. Splitting one out of the title would be a
    guess dressed up as structure — the brief forbids it."""
    session = login("u", "p", http=export_http)

    row = next(r for r in session.export_ratings() if r.movie_id == 2571)

    assert row.title == "The Matrix (1999)"
    assert not hasattr(row, "year")


def test_an_account_with_no_ratings_exports_an_empty_list(logged_in_http):
    """Header only. A cold-start account is not an error."""
    header = fixture("ratings_export")["text"].split("\r\n")[0] + "\r\n"
    logged_in_http.routes[EXPORT] = csv_response(header)
    session = login("u", "p", http=logged_in_http)

    assert session.export_ratings() == []


def test_html_pretending_to_be_csv_raises_instead_of_reporting_no_ratings(
    logged_in_http,
):
    """The failure that would silently wipe a consumer's mirror.

    A session the service no longer likes can answer 200 with a login page.
    Parsed loosely that is zero rows, and a consumer syncing deletions would
    read it as "this account has rated nothing".
    """
    logged_in_http.routes[EXPORT] = response_from_fixture("ratings_export_html")
    session = login("u", "p", http=logged_in_http)

    with pytest.raises(MovieLensAPIError):
        session.export_ratings()


def test_a_renamed_column_is_a_shape_error_not_a_silent_none(logged_in_http):
    """Drift in the header must surface, not turn every rating into None."""
    text = fixture("ratings_export")["text"].replace(
        "movie_id,imdb_id,tmdb_id,rating,average_rating,title",
        "movie_id,imdb_id,tmdb_id,userRating,average_rating,title",
        1,
    )
    logged_in_http.routes[EXPORT] = csv_response(text)
    session = login("u", "p", http=logged_in_http)

    with pytest.raises(MovieLensAPIError):
        session.export_ratings()


def test_a_column_added_in_the_middle_does_not_shift_every_field(logged_in_http):
    """Columns are read by name, so drift that adds one is survivable.

    The new column goes *between* existing ones on purpose: appending it at
    the end is the one position a by-position parser also survives, so a test
    that appends would pass against the bug it is meant to catch.
    """
    original = fixture("ratings_export")["text"]
    reader = csv.reader(io.StringIO(original))
    header = next(reader)
    at = header.index("tmdb_id")
    lines = []
    writer_rows = [header[:at] + ["letterboxd_id"] + header[at:]]
    writer_rows += [row[:at] + ["lb-%s" % row[0]] + row[at:] for row in reader]
    for row in writer_rows:
        buffer = io.StringIO()
        csv.writer(buffer, lineterminator="").writerow(row)
        lines.append(buffer.getvalue())
    logged_in_http.routes[EXPORT] = csv_response("\r\n".join(lines) + "\r\n")
    session = login("u", "p", http=logged_in_http)

    rows = session.export_ratings()

    assert len(rows) == 17
    matrix = next(r for r in rows if r.movie_id == 2571)
    assert matrix.title == "The Matrix (1999)"
    assert matrix.imdb_id == "tt0133093"
    assert matrix.rating == 4.5


def test_a_rejected_session_is_an_auth_error_not_an_empty_export(logged_in_http):
    """Recorded live: an unauthenticated CSV request answers 401 JSON."""
    logged_in_http.routes[EXPORT] = response_from_fixture("unauthenticated_401")
    session = login("u", "p", http=logged_in_http)

    with pytest.raises(AuthenticationError):
        session.export_ratings()


def test_a_server_error_is_an_api_error(logged_in_http):
    logged_in_http.routes[EXPORT] = response_from_fixture("server_error_500")
    session = login("u", "p", http=logged_in_http)

    with pytest.raises(MovieLensAPIError):
        session.export_ratings()


def test_a_transport_failure_is_an_api_error(logged_in_http):
    """A raw requests exception must never reach the consumer."""
    logged_in_http.routes[EXPORT] = requests.ConnectionError("no route to host")
    session = login("u", "p", http=logged_in_http)

    with pytest.raises(MovieLensAPIError):
        session.export_ratings()


def test_the_export_asks_for_csv_rather_than_the_session_wide_json(export_http):
    """The session sends ``Accept: application/json`` for every other call.

    The service ignores Accept here and answers CSV regardless (verified
    2026-09-06), so this asserts the request we make rather than the answer we
    get: if MovieLens ever starts honouring it, asking for JSON on a CSV
    endpoint is how this breaks.
    """
    session = login("u", "p", http=export_http)

    session.export_ratings()

    headers = export_http.calls[-1]["headers"]
    assert headers.get("Accept") == "text/csv"


def test_the_paged_ratings_stream_is_still_available(logged_in_http):
    """The export drops ``prediction`` and ``rated_at``; the stream keeps them.

    Replacing one with the other would cost the consumer both fields.
    """
    from conftest import paged_route

    logged_in_http.routes[EXPLORE] = paged_route("ratings_")
    session = login("u", "p", http=logged_in_http)

    first = next(session.iter_ratings(page_size=3))

    assert first.prediction is not None
    assert first.rated_at is not None
