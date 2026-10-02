from fastapi.testclient import TestClient

from app.main import app


def test_health():
    response = TestClient(app).get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_answers_uptime_monitors_head_check():
    response = TestClient(app).head("/api/v1/health")
    assert response.status_code == 200 and response.content == b""
