from movielens_client import login
from conftest import response_from_fixture


def test_account_exposes_username_and_rating_count(logged_in_http):
    logged_in_http.routes[("GET", "/api/users/me")] = response_from_fixture("users_me")
    session = login("u", "p", http=logged_in_http)

    account = session.account()

    assert account.user_name == "fixture-user"
    assert account.num_ratings == 17
