import socket
import threading

import pytest
import requests
from werkzeug.serving import make_server

from app import db

TOKENS = {"abebe": "demo-token-abebe", "sara": "demo-token-sara", "daniel": "demo-token-daniel"}


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def db_path(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("bank") / "bank.db")
    db.build(path)
    return path


@pytest.fixture(scope="session")
def base_url(db_path):
    from app import server
    server.DB_PATH = db_path
    srv = make_server("127.0.0.1", _free_port(), server.app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture
def reset(base_url):
    """Fresh seed data before each test that changes balances."""
    requests.post(f"{base_url}/api/_reset").raise_for_status()


@pytest.fixture
def headers():
    def make(who="abebe", **extra):
        return {"Authorization": f"Bearer {TOKENS[who]}", **extra}
    return make


@pytest.fixture
def balance(base_url, headers):
    """Current balance of an account, in cents, as its owner sees it."""
    def get(account_id, who="abebe"):
        accounts = requests.get(f"{base_url}/api/accounts", headers=headers(who)).json()
        return next(a["balance_cents"] for a in accounts if a["id"] == account_id)
    return get


@pytest.fixture
def clean_db(tmp_path):
    path = str(tmp_path / "clean.db")
    db.build(path)
    con = db.connect(path)
    yield con
    con.close()


@pytest.fixture
def dirty_db(tmp_path):
    path = str(tmp_path / "dirty.db")
    db.build(path, dirty=True)
    con = db.connect(path)
    yield con
    con.close()
