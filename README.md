# movielens-client

A small, deliberately disposable Python client for movielens.org's **unpublished**
JSON API. It wraps login, paged ratings, paged predictions and rating writes.

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

    session.rate(movie_id=2571, rating=4.5)
```

`iter_ratings()` and `iter_predictions()` are generators, not lists. A heavy
account has thousands of ratings and the prediction feed is tens of thousands of
titles long; nothing is materialised.

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
