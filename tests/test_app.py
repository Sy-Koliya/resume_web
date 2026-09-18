from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth import hash_password, verify_password
from app.main import create_app


ADMIN_PASSWORD = "Correct-Horse-92!"


def make_config(tmp_path: Path) -> Path:
    config = {
        "app": {
            "name": "Bite Hunt Test",
            "environment": "test",
            "base_url": "http://testserver",
            "session_secret": "test-secret-that-is-definitely-longer-than-thirty-two-characters",
            "cookie_secure": False,
            "trusted_hosts": ["testserver"],
        },
        "storage": {
            "database_path": str(tmp_path / "data" / "test.db"),
            "resume_dir": str(tmp_path / "uploads"),
            "max_upload_mb": 1,
            "allowed_extensions": ["pdf", "doc", "docx"],
        },
        "admin": {"username": "admin", "password_hash": hash_password(ADMIN_PASSWORD)},
        "security": {
            "application_limit_per_hour": 20,
            "login_limit_per_15_minutes": 20,
        },
        "ai": {
            "enabled": False,
            "provider": "openai_compatible",
            "base_url": "https://example.invalid/v1",
            "endpoint_path": "/chat/completions",
            "api_key_env": "BITE_HUNT_TEST_API_KEY",
            "model": "test-model",
            "timeout_seconds": 10,
            "max_resume_characters": 5000,
        },
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    app = create_app(make_config(tmp_path))
    with TestClient(app) as test_client:
        yield test_client


def csrf_from(response) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match, response.text
    return match.group(1)


def submit_application(client: TestClient, filename: str = "alex-resume.pdf"):
    home = client.get("/")
    token = csrf_from(home)
    return client.post(
        "/apply",
        data={
            "csrf_token": token,
            "full_name": "Alex Chen",
            "email": "alex@example.com",
            "phone": "+1 555 0100",
            "position": "AI Search & Recommendation Engineer",
            "cover_note": "I build ranking systems.",
            "consent": "yes",
            "website": "",
        },
        files={"resume": (filename, b"%PDF-1.4\n% test resume\n", "application/pdf")},
        follow_redirects=False,
    )


def login(client: TestClient):
    page = client.get("/admin/login")
    token = csrf_from(page)
    return client.post(
        "/admin/login",
        data={"csrf_token": token, "username": "admin", "password": ADMIN_PASSWORD},
        follow_redirects=False,
    )


def test_password_hash_round_trip():
    encoded = hash_password(ADMIN_PASSWORD)
    assert verify_password(ADMIN_PASSWORD, encoded)
    assert not verify_password("not-the-password", encoded)


def test_home_and_health(client: TestClient):
    home = client.get("/")
    assert home.status_code == 200
    assert "What should I eat today?" in home.text
    assert "Send application" in home.text
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"


def test_application_to_admin_workflow(client: TestClient):
    submitted = submit_application(client)
    assert submitted.status_code == 303
    assert submitted.headers["location"].startswith("/application/success?ref=BH-")

    assert login(client).status_code == 303
    dashboard = client.get("/admin")
    assert dashboard.status_code == 200
    assert "Alex Chen" in dashboard.text
    assert "alex@example.com" in dashboard.text

    database = client.app.state.database
    records, total = database.list_applications()
    assert total == 1
    application_id = records[0]["id"]

    detail = client.get(f"/admin/applications/{application_id}")
    assert detail.status_code == 200
    assert "I build ranking systems." in detail.text
    token = csrf_from(detail)

    downloaded = client.get(f"/admin/applications/{application_id}/resume")
    assert downloaded.status_code == 200
    assert downloaded.content.startswith(b"%PDF-")

    updated = client.post(
        f"/admin/applications/{application_id}/update",
        data={
            "csrf_token": token,
            "status": "interview",
            "admin_notes": "Schedule a systems interview.",
        },
        follow_redirects=False,
    )
    assert updated.status_code == 303
    refreshed = client.get(f"/admin/applications/{application_id}")
    assert "Schedule a systems interview." in refreshed.text
    assert "Interview" in refreshed.text


def test_admin_requires_login(client: TestClient):
    response = client.get("/admin", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


def test_csrf_rejected(client: TestClient):
    response = client.post(
        "/admin/login",
        data={"csrf_token": "wrong", "username": "admin", "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 403


def test_rejects_mislabelled_resume(client: TestClient):
    home = client.get("/")
    response = client.post(
        "/apply",
        data={
            "csrf_token": csrf_from(home),
            "full_name": "Alex Chen",
            "email": "alex@example.com",
            "phone": "",
            "position": "Campus Cafeteria Data Collector",
            "cover_note": "",
            "consent": "yes",
            "website": "",
        },
        files={"resume": ("resume.pdf", b"not actually a pdf", "application/pdf")},
    )
    assert response.status_code == 422
    assert "not a valid PDF" in response.text


def test_accepts_real_docx_container(client: TestClient):
    document = io.BytesIO()
    with zipfile.ZipFile(document, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", "<document />")
    home = client.get("/")
    response = client.post(
        "/apply",
        data={
            "csrf_token": csrf_from(home),
            "full_name": "Taylor Lee",
            "email": "taylor@example.com",
            "phone": "",
            "position": "Campus Cafeteria Data Collector",
            "cover_note": "",
            "consent": "yes",
            "website": "",
        },
        files={
            "resume": (
                "resume.docx",
                document.getvalue(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

