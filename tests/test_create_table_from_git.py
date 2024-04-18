from csvbase.web.func import set_current_user
from csvbase.web.main.create_table import parse_github_url

import pytest

from .utils import parse_form, random_string


@pytest.mark.parametrize(
    "inp, expected_output",
    [("https://github.com/calpaterson/csvbase", ("calpaterson", "csvbase"))],
)
def test_parse_github_url(inp, expected_output):
    assert expected_output == parse_github_url(inp)


def test_get_form_blank(client, test_user):
    set_current_user(test_user)
    resp = client.get("/new-table/git")
    assert resp.status_code == 200


@pytest.mark.xfail(reason="not implemented")
def test_post_initial_form__happy(client, test_user):
    set_current_user(test_user)
    initial_form = {
        "table-name": random_string(),
        "repo": "https://csvbase.com/calpaterson/csvbase",
        "branch": "main",
        "path": "/examples/moocows.csv",
    }
    resp = client.post("/new-table/git", data=initial_form)
    assert resp.status_code == 202
    assert False
    form = parse_form(resp.data)
