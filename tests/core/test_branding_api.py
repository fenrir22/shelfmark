"""Quick smoke test for the branding asset API endpoints."""

import io
from unittest.mock import patch

import pytest
from flask import Flask

from shelfmark.core.user_db import UserDB


@pytest.fixture
def app(tmp_path):
    from shelfmark.core.admin_routes import register_admin_routes

    user_db = UserDB(str(tmp_path / "shelfmark.db"))
    user_db.initialize()

    test_app = Flask(__name__)
    test_app.config["SECRET_KEY"] = "test-secret"
    test_app.config["TESTING"] = True

    with patch("shelfmark.core.branding.CONFIG_DIR", tmp_path):
        register_admin_routes(test_app, user_db)
        yield test_app


@pytest.fixture
def admin_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "admin"
        sess["is_admin"] = True
    return client


def _png_bytes():
    return b"\x89PNG\r\n\x1a\n" + b"0" * 64


def test_upload_logo(admin_client, app):
    res = admin_client.post(
        "/api/admin/branding/asset",
        data={"kind": "logo", "file": (io.BytesIO(_png_bytes()), "logo.png")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 200
    assert res.json["success"] is True

    status = admin_client.get("/api/admin/branding")
    assert status.json == {"logo": True, "favicon": False, "mascot": False}


def test_upload_mascot(admin_client):
    res = admin_client.post(
        "/api/admin/branding/asset",
        data={"kind": "mascot", "file": (io.BytesIO(_png_bytes()), "mascot.png")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 200
    assert res.json["success"] is True

    status = admin_client.get("/api/admin/branding")
    assert status.json == {"logo": False, "favicon": False, "mascot": True}


def test_upload_rejects_svg(admin_client):
    res = admin_client.post(
        "/api/admin/branding/asset",
        data={"kind": "favicon", "file": (io.BytesIO(b"<svg></svg>"), "icon.svg")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 400
    assert "Unsupported" in res.json["message"]


def test_reset_logo(admin_client):
    admin_client.post(
        "/api/admin/branding/asset",
        data={"kind": "logo", "file": (io.BytesIO(_png_bytes()), "logo.png")},
        content_type="multipart/form-data",
    )
    res = admin_client.delete("/api/admin/branding/asset", data={"kind": "logo"})
    assert res.status_code == 200
    status = admin_client.get("/api/admin/branding")
    assert status.json == {"logo": False, "favicon": False, "mascot": False}


def test_requires_admin(app):
    client = app.test_client()
    with patch("shelfmark.core.admin_routes.load_active_auth_mode", return_value="builtin"):
        res = client.get("/api/admin/branding")
    assert res.status_code == 401


def test_unknown_kind(admin_client):
    res = admin_client.post(
        "/api/admin/branding/asset",
        data={"kind": "banana", "file": (io.BytesIO(_png_bytes()), "x.png")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 400
