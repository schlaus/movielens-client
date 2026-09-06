# movielens-client

A small, deliberately disposable Python client for movielens.org's **unpublished**
JSON API. It wraps login, paged ratings, paged predictions, a bulk CSV export of
the whole rating history, an IMDb-id lookup, and rating writes.

It stores nothing: no database, no config file, no credential cache, no logging.
The caller owns all persistence. If MovieLens changes or disappears, pin, patch
or delete this package without touching the caller's storage.

## Install

```bash
pip install /path/to/movielens-client
pip install git+https://github.com/…/movielens-client
```

## Use

```python
from movielens_client import login, AuthenticationError, MovieLensAPIError

try:
    session = login("username", "password")
except AuthenticationError:
    ...   # bad credentials — do not retry, ask the user
except MovieLensAPIError:
    ...   # MovieLens is unreachable or misbehaving — defer and retry later

with session:
    print(session.account().num_ratings)

    for rating in session.iter_ratings():          # lazy, one HTTP call per page
        print(rating.imdb_id, rating.rating, rating.rated_at)

    for prediction in session.iter_predictions():  # also lazy, effectively endless
        print(prediction.imdb_id, prediction.prediction)

    for row in session.export_ratings():           # one request, the whole history
        print(row.imdb_id, row.rating, row.title)

    session.rate(movie_id=2571, rating=4.5)

    # A film MovieLens has never shown us: search by title, match on the id
    movie = session.find_movie_by_imdb_id("tt0133093", "The Matrix")
    if movie is not None:
        session.rate(movie.movie_id, 4.5)
```

`iter_ratings()` and `iter_predictions()` are generators, not lists. A heavy
account has thousands of ratings and the prediction feed is tens of thousands of
titles long; nothing is materialised. `export_ratings()` is the exception and
deliberately so: it is one request whose whole body arrives at once.

## Syncing: `export_ratings()` versus `iter_ratings()`

`export_ratings()` is `GET /api/users/me/movielens-ratings.csv`: every rating on
the account in a single request, returned as a list of `ExportedRating`. For a
full sync that replaces walking dozens of pages, and takes that path's paging
failure modes with it.

```python
row.movie_id        # MovieLens id
row.imdb_id         # canonical, "tt0114814"
row.tmdb_id
row.rating          # what this account gave the film
row.average_rating  # the community mean — not the user's opinion of anything
row.title           # "The Usual Suspects (1995)", year included, as exported
```

`rating` and `average_rating` are adjacent columns of plausible-looking floats;
they are not interchangeable. The title carries its year in parentheses and is
passed through as-is — the export has no year column, and this package does not
invent one by parsing the string.

`iter_ratings()` is still there and still lazy. The CSV carries no `prediction`
and no `rated_at`, so anything needing those keeps using the stream.

## Rating a film MovieLens has not shown you

A write needs MovieLens' own `movieId`, and there is no direct id lookup. The
route that works is a **title** search matched on the IMDb id:

```python
movie = session.find_movie_by_imdb_id("tt6751668", "Parasite")
if movie is None:
    ...   # MovieLens does not carry it — a normal answer, not an error
else:
    session.rate(movie.movie_id, 4.5)
```

`None` means "no match"; an outage still raises, so the two stay distinguishable.
The search is bounded (4 pages of 50 by default, both adjustable).

Two things that look like they would work do not, and neither of them errors:

* `explore?imdbMovieId=…` **ignores** the parameter and returns an unrelated
  default page of 10,000 items.
* `explore?q=tt0133093` returns nothing at all.

Matching is therefore on `movie.imdbMovieId` inside the results of a title
search — never on position (the wanted film is routinely not first) and never on
title equality (titles differ by language and subtitle; ids do not). There is no
`year` argument: a year in the query returns zero results, and filtering
candidates by year can only lose the right film, since MovieLens' release year
and IMDb's disagree for festival releases.

## IMDb ids

MovieLens returns `imdbMovieId` zero-padded and without a prefix (`"0133093"`).
Every result this package hands back exposes `imdb_id` in canonical
`tt0133093` form. A MovieLens `movie_id` is always a separate attribute and is
never presented as an IMDb id.

## Errors

* `AuthenticationError` — bad credentials, or a session that is no longer
  accepted. The caller must **not** treat this as transient.
* `MovieLensAPIError` — connection failure, timeout, 5xx, non-JSON body, or a
  4xx the API rejected the request with. The caller should defer and retry.

Both subclass `MovieLensError`.

## Tests

```bash
pytest                      # unit suite, recorded fixtures, no network
pytest -m live              # live smoke test; needs .env credentials
```
