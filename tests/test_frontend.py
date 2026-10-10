from pathlib import Path

from config import create_app


APP_JS = Path(__file__).resolve().parents[1] / "static" / "js" / "app.js"


def test_home_page_serves_nearshare_frontend_shell():
    client = create_app({"TESTING": True}).test_client()
    response = client.get("/")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Why Buy When You Can Borrow?" in page
    assert "static/js/api.js" in page
    assert "static/js/app.js" in page
    assert 'id="app"' in page


def test_request_form_uses_lookup_labels_validates_selections_and_submits_numeric_ids():
    source = APP_JS.read_text(encoding="utf-8")

    assert "formField('Category','category_id'" in source
    assert "formField('Locality','locality_id'" in source
    assert "Loading categories…" in source
    assert "Loading localities…" in source
    assert "status.textContent='Could not load '+noun.toLowerCase()+'. Please try again.'" in source
    assert "No '+noun.toLowerCase()+' are available yet." in source
    assert "submit.disabled=!(categoryIds?.has(Number(category.value))&&localityIds?.has(Number(locality.value)))" in source
    assert "if(!categoryIds?.has(Number(category.value))||!localityIds?.has(Number(locality.value)))" in source
    assert "d.category_id=Number(d.category_id);d.locality_id=Number(d.locality_id)" in source
    assert "data.locality_id=Number(data.locality_id)" in source
    assert "category_id:Number(fd.get('category_id'))" in source
    assert "categoryNames.get(r.category_id)" in source
    assert "localityNames.get(r.locality_id)" in source
    assert "Category lookup API is not available" not in source
    assert "Enter the locality ID configured for your area." not in source
