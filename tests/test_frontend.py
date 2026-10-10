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
    assert "formField('City','city'" in source
    assert "setupCityLocality(city,locality" in source
    assert "'/api/cities'" in source
    assert "'/api/localities?city='+encodeURIComponent(selected)" in source
    assert "Retry city lookup" not in source
    assert "Retry locality lookup" not in source
    assert "city-retry" not in source
    assert "locality-retry" not in source
    assert "Could not load '+noun.toLowerCase()+'. Please try again.'" in source
    assert "Could not load localities. Please try again." in source
    assert "delete data.city" in source
    assert "Loading categories…" in source
    assert "Loading '+noun.toLowerCase()+'…'" in source
    assert "status.textContent='Could not load '+noun.toLowerCase()+'. Please try again.'" in source
    assert "No '+noun.toLowerCase()+' are available yet." in source
    assert "submit.disabled=!(categoryIds?.has(Number(category.value))&&localitySelection?.valid())" in source
    assert "if(!categoryIds?.has(Number(category.value))||!localitySelection?.valid())" in source
    assert "d.category_id=Number(d.category_id);d.locality_id=Number(d.locality_id)" in source
    assert "data.locality_id=Number(data.locality_id)" in source
    assert "category_id:Number(fd.get('category_id'))" in source
    assert "categoryNames.get(r.category_id)" in source
    assert "localityNames.get(r.locality_id)" in source
    assert "Category lookup API is not available" not in source
    assert "Enter the locality ID configured for your area." not in source


def test_city_lookup_string_response_drives_city_filtered_locality_dropdown():
    source = APP_JS.read_text(encoding="utf-8")
    helper = source.split("async function setupCityLocality", 1)[1].split("function empty(", 1)[0]

    # /api/cities returns strings, so city option values and membership checks
    # must both use each string directly rather than object properties.
    assert "const result=await API.get('/api/cities')" in helper
    assert "rows.map(name=>new Option(name,name))" in helper
    assert "cityIds=new Set(rows)" in helper
    assert "city.onchange=()=>{localityIds=null;loadLocalities();}" in helper

    # Each selection clears and disables the previous locality options while
    # fetching, then uses only the selected city's API response.
    assert "locality.replaceChildren(new Option('Loading…',''))" in helper
    assert "locality.disabled=true" in helper
    assert "'/api/localities?city='+encodeURIComponent(selected)" in helper
    assert "const rows=result.localities||[]" in helper
    assert "locality.replaceChildren(new Option('Select locality','',true,true),...rows.map(row=>new Option(row.locality_name,String(row.locality_id))))" in helper
    assert "localityIds=new Set(rows.map(row=>Number(row.locality_id)))" in helper


def test_request_list_uses_city_filtered_locality_lookups():
    source = APP_JS.read_text(encoding="utf-8")
    assert "API.get('/api/localities')" not in source
    assert "cityData.cities||[]).map(city=>API.get('/api/localities?city='+encodeURIComponent(city)))" in source


def test_item_borrow_action_uses_a_server_resolved_matching_request():
    source = APP_JS.read_text(encoding="utf-8")

    assert "API.get(`/api/requests/matching-item/${id}`)" in source
    assert "href=\"#matches/${info.request_id}\"" in source
    assert "You own this item." in source
    assert "Could not check your requests. Please reload and try again." in source


def test_item_owner_can_discover_matching_requests_from_item_detail():
    source = APP_JS.read_text(encoding="utf-8")

    assert "API.get(`/api/items/${id}/matching-requests`)" in source
    assert "${owner?ownerMatchingRequests(ownerRequests):''}" in source
    assert "No matching requests yet." in source
    assert "href=\"#matches/${r.request_id}\">View match" in source


def test_owner_request_feed_navigation_privacy_and_notification_link():
    source = APP_JS.read_text(encoding="utf-8")

    assert "['requests-nearby','Requests Near You']" in source
    assert "API.get('/api/requests/nearby')" in source
    assert "No matching requests near you yet." in source
    assert "Exact private addresses and requester contact details are never shown." in source
    assert "whether or not you have a matching item listed" in source
    assert 'href="#nearby-request/${r.request_id}">Respond' in source
    assert 'href="#nearby-request/${n.request_id}">View request' in source
    assert 'href="#my-items">View My Items' not in source
    assert "async function nearbyRequestDetail(id)" in source
    assert "response_status:'INTERESTED'" in source
    assert "response_status:'IGNORED'" in source
    assert "`item-new?respond_to=${id}`" in source
    assert "['REQUEST_NEARBY','MATCHING_REQUEST'].includes(n.notification_type)" in source
    assert "My open requests" in source
    assert "state.requests.filter(x=>['OPEN','MATCHED'].includes(x.status)).length" in source
    assert "label:'Rent'" in source and "label:'Lend'" in source
    assert "form.elements.rental_price.value=responseRequest.max_budget" in source
    assert "form.elements.security_deposit.value='0'" in source


def test_borrower_and_lender_views_have_distinct_offer_actions():
    source = APP_JS.read_text(encoding="utf-8")

    assert "Waiting for the item owner to make an offer." in source
    assert "item.is_item_owner" in source
    assert "Only the item owner can make an offer." in source
    assert "(requestId?data.can_decide:o.can_decide)&&o.status==='PENDING'" in source
    assert "o.can_withdraw&&o.status==='PENDING'" in source
    assert "API.get('/api/offers')" in source
    assert "View matching items" in source
    assert "View offer" in source


def test_contact_details_are_loaded_from_participant_scoped_booking_endpoint():
    source = APP_JS.read_text(encoding="utf-8")

    assert "contactAvailable=states.includes(b.status)" in source
    assert ">View Contact Details</button>" in source
    assert "Booking confirmed" in source
    assert "data-contacts=\"${b.booking_id}\"" in source
    assert "API.get(`/api/bookings/${id}/contacts`)" in source
    assert "esc(contacts.borrower.email)" in source
    assert "esc(contacts.owner.email)" in source
    assert "Contact details are shared only with the borrower and item owner for this confirmed booking." in source
