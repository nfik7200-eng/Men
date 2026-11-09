"""The admin page offers the source-switch island beside the server table. The server table is kept: it holds the
collection actions and run history, which the island does not replace."""
import pytest
from fastapi.testclient import TestClient

from shopping.web.app import create_app
from watchtower import config

TOKEN = "unit-test-token-0123456789abcdef"


@pytest.fixture
def signed_in(db, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", TOKEN)
    c = TestClient(create_app(db), base_url="http://testserver")
    assert c.post("/admin/login", data={"token": TOKEN}, follow_redirects=False).status_code == 303
    return c


def test_the_admin_page_offers_the_island_once(signed_in):
    html = signed_in.get("/admin").text
    assert html.count('data-island="admin-settings"') == 1
    assert 'hidden><p class="state">Loading source settings' in html


def test_the_server_table_with_its_collection_actions_is_kept(signed_in):
    html = signed_in.get("/admin").text
    assert "<th>Terms / robots note</th>" in html
    assert "<th>Actions</th>" in html
