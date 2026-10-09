from config import create_app


def test_health_endpoint():
    client = create_app({"TESTING": True}).test_client()
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json == {"status": "ok", "message": "NearShare API is running"}
