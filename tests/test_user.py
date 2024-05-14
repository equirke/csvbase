from csvbase.web.func import set_current_user
from .utils import make_user
from bs4 import BeautifulSoup


def test_user_view__self(client, test_user, ten_rows, private_table):
    set_current_user(test_user)
    resp = client.get(f"/{test_user.username}")

    page = BeautifulSoup(resp.text, "html.parser")
    ten_rows_display_name = test_user.username + "/" + ten_rows.table_name
    private_table_display_name = test_user.username + "/" + private_table

    assert resp.status_code == 200
    assert page.find("h5", class_="card-title", string=ten_rows_display_name)
    assert page.find("h5", class_="card-title", string=private_table_display_name)


def test_user_view__while_anon(client, test_user, ten_rows, private_table):
    resp = client.get(f"/{test_user.username}")

    page = BeautifulSoup(resp.text, "html.parser")
    ten_rows_display_name = test_user.username + "/" + ten_rows.table_name
    private_table_display_name = test_user.username + "/" + private_table

    assert resp.status_code == 200
    assert page.find("h5", class_="card-title", string=ten_rows_display_name)
    assert not page.find("h5", class_="card-title", string=private_table_display_name)


def test_user_view__other(app, sesh, client, test_user, ten_rows):
    set_current_user(make_user(sesh, app.config["CRYPT_CONTEXT"]))
    resp = client.get(f"/{test_user.username}")

    page = BeautifulSoup(resp.text, "html.parser")
    ten_rows_display_name = test_user.username + "/" + ten_rows.table_name

    assert resp.status_code == 200
    assert page.find("h5", class_="card-title", string=ten_rows_display_name)
