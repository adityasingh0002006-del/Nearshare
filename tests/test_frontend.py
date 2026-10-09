from config import create_app


def test_home_page_serves_nearshare_frontend_shell():
    client = create_app({"TESTING": True}).test_client()
    response = client.get("/")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Why Buy When You Can Borrow?" in page
    assert "static/js/api.js" in page
    assert "static/js/app.js" in page
    assert 'id="app"' in page
