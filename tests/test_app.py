from __future__ import annotations

import asyncio
import io
import json
import re
import sqlite3
import zipfile
from pathlib import Path

import pytest
from docx import Document
from fastapi.testclient import TestClient

from app.ai import _normalise_evaluation, evaluate_resume
from app.auth import hash_password, verify_password
from app.config import load_settings
from app.database import Database, SCHEMA, SCHEMA_VERSION
from app.main import create_app


ADMIN_PASSWORD = "Correct-Horse-92!"


def make_config(tmp_path: Path, *, ai_enabled: bool = False) -> Path:
    ai_config_path = tmp_path / "deepseek.json"
    ai_config_path.write_text(
        json.dumps(
            {
                "provider": "deepseek",
                "base_url": "https://api.deepseek.com",
                "endpoint_path": "/chat/completions",
                "api_key": "test-deepseek-key",
                "model": "deepseek-v4-pro",
                "max_output_tokens": 6000,
            }
        ),
        encoding="utf-8",
    )
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
            "enabled": ai_enabled,
            "config_path": str(ai_config_path),
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
            "review_score": "86",
        },
        follow_redirects=False,
    )
    assert updated.status_code == 303
    refreshed = client.get(f"/admin/applications/{application_id}")
    assert "Schedule a systems interview." in refreshed.text
    assert "Interview" in refreshed.text
    assert "Team 86/100" in refreshed.text


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


def test_deepseek_config_is_loaded_from_private_json(tmp_path: Path):
    settings = load_settings(make_config(tmp_path, ai_enabled=True))
    assert settings.ai.ready
    assert settings.ai.base_url == "https://api.deepseek.com"
    assert settings.ai.model == "deepseek-v4-pro"
    assert settings.ai.api_key == "test-deepseek-key"
    assert "test-deepseek-key" not in repr(settings.ai)


def test_ai_scorecard_is_normalised_and_weighted():
    result = _normalise_evaluation(
        {
            "summary": "Strong ranking experience with one production detail to verify.",
            "dimension_scores": {
                "role_requirements": {"score": 90, "rationale": "Matches core work."},
                "relevant_experience": {"score": 80, "rationale": "Relevant projects."},
                "skills_and_tools": {"score": 70, "rationale": "Most tools are present."},
                "evidence_of_impact": {"score": 60, "rationale": "Some metrics are missing."},
            },
            "strengths": ["Search and ranking experience"],
            "gaps": ["No online experiment details"],
            "evidence": [
                {
                    "criterion": "Ranking systems",
                    "resume_evidence": "Built a hybrid search service.",
                    "assessment": "meets",
                }
            ],
            "interview_questions": [
                {
                    "question": "How did you evaluate ranking quality?",
                    "reason": "Verify measurement depth.",
                    "strong_answer_signals": ["Offline metrics", "A/B test"],
                    "score_guide": {
                        "1": "No concrete metric.",
                        "3": "Names a relevant metric.",
                        "5": "Connects offline and online results.",
                    },
                }
            ],
            "limitations": ["Resume claims were not independently verified."],
        }
    )
    assert result["score"] == 78
    assert result["recommendation"] == "potential_fit"
    assert result["dimension_scores"][0]["weight"] == 35
    assert result["interview_questions"][0]["score_guide"]["5"].startswith("Connects")


def test_deepseek_openai_compatible_request(tmp_path: Path, monkeypatch):
    settings = load_settings(make_config(tmp_path, ai_enabled=True))
    resume_path = tmp_path / "resume.docx"
    document = Document()
    document.add_paragraph("Built a hybrid search service in Python and improved NDCG by 12%.")
    document.save(resume_path)
    captured: dict[str, object] = {}

    raw_result = {
        "summary": "Relevant search engineering experience.",
        "dimension_scores": {
            "role_requirements": {"score": 90, "rationale": "Direct search work."},
            "relevant_experience": {"score": 85, "rationale": "Relevant delivery."},
            "skills_and_tools": {"score": 80, "rationale": "Python is shown."},
            "evidence_of_impact": {"score": 75, "rationale": "Includes NDCG impact."},
        },
        "strengths": ["Hybrid search"],
        "gaps": ["Recommendation work is not shown"],
        "evidence": [],
        "interview_questions": [],
        "limitations": ["Resume evidence is self-reported."],
    }

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"message": {"content": json.dumps(raw_result)}}]
            }

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured["client"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, path, *, headers, json):
            captured["path"] = path
            captured["headers"] = headers
            captured["payload"] = json
            return FakeResponse()

    monkeypatch.setattr("app.ai.httpx.AsyncClient", FakeAsyncClient)
    result = asyncio.run(
        evaluate_resume(
            settings.ai,
            {
                "position": "AI Search & Recommendation Engineer",
                "cover_note": "I build ranking systems.",
            },
            resume_path,
            {
                "type": "Full-time",
                "location": "Remote / Hybrid",
                "tasks": ["Build intelligent search."],
                "skills": "Python, SQL, ranking, and evaluation.",
            },
        )
    )

    assert captured["client"]["base_url"] == "https://api.deepseek.com"
    assert captured["path"] == "/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer test-deepseek-key"
    assert captured["payload"]["model"] == "deepseek-v4-pro"
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert result["score"] == 84


def test_ai_evaluation_route_stores_and_renders_scorecard(tmp_path: Path, monkeypatch):
    expected = _normalise_evaluation(
        {
            "summary": "The resume aligns with the search role.",
            "dimension_scores": {
                "role_requirements": {"score": 92, "rationale": "Direct match."},
                "relevant_experience": {"score": 88, "rationale": "Three relevant years."},
                "skills_and_tools": {"score": 84, "rationale": "Python and SQL shown."},
                "evidence_of_impact": {"score": 76, "rationale": "One measured result."},
            },
            "strengths": ["Hybrid search"],
            "gaps": ["Review integrity work is unclear"],
            "evidence": [
                {
                    "criterion": "Search ranking",
                    "resume_evidence": "Improved NDCG by 12%.",
                    "assessment": "meets",
                }
            ],
            "interview_questions": [
                {
                    "question": "How would you detect discount-biased reviews?",
                    "reason": "Probe a role-specific gap.",
                    "strong_answer_signals": ["Counterfactual evaluation"],
                    "score_guide": {"1": "Vague", "3": "Plausible", "5": "Rigorous"},
                }
            ],
            "limitations": ["AI analysis requires human verification."],
        }
    )

    async def fake_evaluate(settings, application, resume_path, role):
        assert settings.model == "deepseek-v4-pro"
        assert application["position"] == "AI Search & Recommendation Engineer"
        assert resume_path.is_file()
        assert "Build intelligent search" in role["tasks"][0]
        return expected

    monkeypatch.setattr("app.main.evaluate_resume", fake_evaluate)
    app = create_app(make_config(tmp_path, ai_enabled=True))
    with TestClient(app) as test_client:
        assert submit_application(test_client).status_code == 303
        assert login(test_client).status_code == 303
        applications, _ = app.state.database.list_applications()
        application_id = applications[0]["id"]
        detail = test_client.get(f"/admin/applications/{application_id}")
        response = test_client.post(
            f"/admin/applications/{application_id}/evaluate",
            data={"csrf_token": csrf_from(detail)},
            follow_redirects=False,
        )
        assert response.status_code == 303
        rendered = test_client.get(response.headers["location"])
        assert "The resume aligns with the search role." in rendered.text
        assert "How would you detect discount-biased reviews?" in rendered.text
        assert "Rigorous" in rendered.text
        evaluations = app.state.database.list_ai_evaluations(application_id)
        assert evaluations[0]["result"]["score"] == expected["score"]


def test_delete_application_removes_record_evaluations_and_resume(client: TestClient):
    assert submit_application(client).status_code == 303
    assert login(client).status_code == 303
    database = client.app.state.database
    applications, _ = database.list_applications()
    application = applications[0]
    resume_path = (
        client.app.state.settings.storage.resume_dir / application["resume_stored_path"]
    )
    database.add_ai_evaluation(
        application["id"], "deepseek", "deepseek-v4-pro", {"score": 75}
    )
    detail = client.get(f"/admin/applications/{application['id']}")
    token = csrf_from(detail)

    rejected = client.post(
        f"/admin/applications/{application['id']}/delete",
        data={"csrf_token": token, "confirm_reference": "WRONG"},
        follow_redirects=False,
    )
    assert rejected.status_code == 303
    assert database.get_application(application["id"]) is not None
    assert resume_path.is_file()

    deleted = client.post(
        f"/admin/applications/{application['id']}/delete",
        data={
            "csrf_token": token,
            "confirm_reference": application["reference_code"],
        },
        follow_redirects=False,
    )
    assert deleted.status_code == 303
    assert deleted.headers["location"] == "/admin"
    assert database.get_application(application["id"]) is None
    assert not resume_path.exists()
    assert database.list_ai_evaluations(application["id"]) == []
    dashboard = client.get("/admin")
    assert "permanently deleted" in dashboard.text


def test_database_migrates_version_one_review_score(tmp_path: Path):
    path = tmp_path / "legacy.db"
    legacy_schema = SCHEMA.replace(
        "    review_score INTEGER\n"
        "        CHECK (review_score IS NULL OR (review_score >= 0 AND review_score <= 100)),\n",
        "",
    )
    with sqlite3.connect(path) as connection:
        connection.executescript(legacy_schema)
        connection.execute("PRAGMA user_version = 1")

    database = Database(path)
    database.initialize()
    with database.connect() as connection:
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(applications)").fetchall()
        }
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    assert "review_score" in columns
    assert version == SCHEMA_VERSION
