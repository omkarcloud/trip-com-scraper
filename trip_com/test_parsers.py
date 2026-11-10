"""Offline tests for the Trip.com parsers and reference resolvers.

    python -m pytest trip_com/test_parsers.py -q

Fixtures are real www.trip.com payloads captured 2026-11-07 (see the
parsers.py docstring): a happy page, a sparse page and a not-found page per
surface. No network.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trip_com import parsers as P, refs  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fixture(name):
    with open(os.path.join(FX, name), encoding="utf-8") as f:
        return json.load(f) if name.endswith(".json") else f.read()


# ---- refs ---------------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("346403", 346403),
    ("https://www.trip.com/hotels/shanghai-hotel-detail-346403/grand-hyatt-shanghai/?checkin=2026-12-10", 346403),
    ("https://us.trip.com/hotels/detail/?hotelId=346403&checkin=2026-12-10", 346403),
    ("//www.trip.com/hotels/v2/shanghai-hotel-detail-346403/grand-hyatt-shanghai/", 346403),
])
def test_resolve_hotel(value, expected):
    assert refs.resolve_hotel(value) == expected


@pytest.mark.parametrize("value", ["", "grand hyatt", "https://www.booking.com/hotel/346403", "https://www.trip.com/hotels/"])
def test_resolve_hotel_rejects(value):
    with pytest.raises(ValueError):
        refs.resolve_hotel(value)


def test_resolve_destination():
    assert refs.resolve_destination("228") == {"city_id": 228}
    assert refs.resolve_destination("https://www.trip.com/hotels/shanghai-hotels-list-2/") == {"city_id": 2}
    assert refs.resolve_destination("https://www.trip.com/hotels/list?city=2&cityName=shanghai") == {"city_id": 2}
    assert refs.resolve_destination(" Tokyo ") == {"name": "Tokyo"}
    with pytest.raises(ValueError):
        refs.resolve_destination("https://www.trip.com/flights/")
    with pytest.raises(ValueError):
        refs.resolve_destination("https://www.airbnb.com/s/Tokyo")


def test_resolve_attraction_and_experience():
    assert refs.resolve_attraction("88070") == 88070
    assert refs.resolve_attraction("https://www.trip.com/travel-guide/attraction/mitaka/ghibli-museum-88070/?curr=USD") == 88070
    assert refs.resolve_attraction("https://www.trip.com/travel-guide/attraction/urayasu/tokyo-disneysea-10521877") == 10521877
    with pytest.raises(ValueError):
        refs.resolve_attraction("https://www.trip.com/travel-guide/destination/tokyo-294/")
    assert refs.resolve_experience("100444859") == 100444859
    assert refs.resolve_experience("https://www.trip.com/things-to-do/detail/67937814?language=EN") == 67937814
    with pytest.raises(ValueError):
        refs.resolve_experience("capybara")
    assert refs.resolve_experiences_destination("Tokyo") == "tokyo"
    assert refs.resolve_experiences_destination("New York") == "newyork"
    assert refs.resolve_experiences_destination("https://www.trip.com/things-to-do/experiences/paris/") == "paris"
    with pytest.raises(ValueError):
        refs.resolve_experiences_destination("https://www.trip.com/things-to-do/detail/1")


def test_resolve_place_code_and_children():
    assert refs.resolve_place_code(" nrt ") == "NRT"
    with pytest.raises(ValueError):
        refs.resolve_place_code("Tokyo")
    assert refs.resolve_children_ages("3, 7") == [3, 7]
    assert refs.resolve_children_ages("") == []
    with pytest.raises(ValueError):
        refs.resolve_children_ages("3,18")
    assert refs.normalize_locale("en") == "en-US"
    assert refs.normalize_locale("ja-jp") == "ja-JP"


def test_hotel_filters():
    assert refs.hotel_filter(refs.HOTEL_SORTS["price_low_to_high"]) == {"type": "17", "value": "3", "filterId": "17|3"}
    assert refs.hotel_filter(refs.MIN_SCORES["9"]) == {"type": "6", "value": "10", "filterId": "6|10"}
    assert refs.star_filter("5") == {"type": "16", "value": "5", "filterId": "16|5"}
    assert refs.price_filter(100, 250) == {"type": "15", "value": "100|250", "filterId": "15|Range"}
    assert refs.price_filter(None, 250)["value"] == "0|250"


# ---- destinations ---------------------------------------------------------------------------

def test_parse_destinations_city():
    rows = P.parse_destinations(fixture("destinations_tokyo.json"))
    assert rows[0] == {"id": 228, "type": "city", "name": "Tokyo", "country": "Japan", "country_id": 78,
                       "timezone_offset_seconds": 32400, "keyword": None,
                       "link": "https://www.trip.com/hotels/list?city=228"}


def test_parse_destinations_hotel_name_carries_keyword():
    rows = P.parse_destinations(fixture("destinations_grand_hyatt.json"))
    assert rows[0]["id"] == 2 and rows[0]["name"] == "Shanghai"
    assert rows[0]["keyword"] == "grand hyatt shanghai"
    assert rows[0]["timezone_offset_seconds"] is None     # -1 upstream
    assert P.parse_destinations({}) == []


def test_parse_destinations_drops_default_suggestions():
    # a term with no match answers Singapore / New York / Bangkok ... without a timeZone key
    assert P.parse_destinations(fixture("destinations_nomatch.json")) == []


# ---- hotel search -----------------------------------------------------------------------------

@pytest.fixture(scope="module")
def shanghai():
    return P.parse_hotel_search(fixture("hotels_search_shanghai.json"))


def test_hotel_search_totals(shanghai):
    assert shanghai["total_count"] == 9352
    assert shanghai["is_last_page"] is False
    assert len(shanghai["results"]) == 13           # 10 organic + ad / recommend slots
    assert sum(1 for r in shanghai["results"] if r["is_ad"]) >= 1
    assert len({r["id"] for r in shanghai["results"]}) == 13


def test_hotel_search_card(shanghai):
    card = shanghai["results"][0]
    assert card["id"] == 427026 and card["name"] == "Renaissance Shanghai Pudong Hotel"
    assert card["link"] == "https://www.trip.com/hotels/detail/?hotelId=427026"
    assert card["star"] == 5 and card["category"] == "Hotel" and card["is_ad"] is True
    assert card["rating"]["score"] == 9.4 and card["rating"]["review_count"] == 3411
    assert card["rating"]["label"] == "Outstanding" and card["rating"]["max"] == 10.0
    assert card["rating"]["subscores"]["cleanliness"] == 9.4
    assert card["rating"]["highlights"] == ["Delicious breakfast", "Friendly front desk staff"]
    assert card["location"]["city_id"] == 2 and card["location"]["city"] == "Shanghai"
    assert card["location"]["zone"] == "Pudong New International Expo Center/World Expo Area"
    assert card["location"]["coordinate"]["latitude"] > 31
    assert card["location"]["coordinate"]["system"] == "WGS84"
    assert "address" not in card["location"]        # the list's "address" only repeats the zone
    assert card["images"] and card["images"][0].startswith("https://")
    assert not any(i.endswith(".webp") for i in card["images"]) and len(card["images"]) == len(set(card["images"]))
    assert card["video"].endswith(".mp4")
    assert "Scenic indoor pool" in card["highlights"]
    assert card["tags"] == ["Hi China!"]


def test_hotel_search_room_price(shanghai):
    room = shanghai["results"][0]["room"]
    assert room["name"] == "Deluxe King Room" and room["beds"] == ["1 queen bed"]
    assert room["max_adults"] == 2
    assert room["price"]["amount"] == 86.0 and room["price"]["currency"] == "USD"
    assert room["price"]["original_amount"] == 170.0
    assert room["price"]["total"] == 182.52 and room["price"]["taxes_and_fees"] == 25.92
    assert room["price"]["discounts"] == -168.57
    assert room["price"]["discount_items"][0] == {"name": "China travel deal", "amount": -105.34}
    assert room["payment_type"] == "prepay"
    assert room["cancellation"] == "30-minute Cancellation Window"
    assert room["tags"]["discounts"] == ["49% off"]


def test_hotel_search_star_types():
    kw = P.parse_hotel_search(fixture("hotels_search_keyword.json"))
    # starType 1 = star (Grand Hyatt), 0 = Trip.com's diamond rating (Hyatt on the Bund)
    assert [(r["id"], r["star_type"]) for r in kw["results"]] == [(346403, "star"), (425378, "diamond")]


def test_hotel_search_resort_fee_due_at_hotel():
    lv = P.parse_hotel_search(fixture("hotels_search_las_vegas.json"))
    card = next(r for r in lv["results"] if r["id"] == 733789)
    assert card["name"] == "Santa Fe Station Hotel & Casino"
    price = card["room"]["price"]
    assert price["amount"] == 139.0 and price["original_amount"] == 145.0
    assert price["pay_now"] == 224.39 and price["due_at_hotel"] == 79.1 and price["total"] == 303.49
    assert price["due_at_hotel_items"] == [{"name": "Resort fee", "amount": 79.1}]
    assert price["room_subtotal"] == 211.23 and price["taxes_and_fees"] == 25.82 and price["discounts"] == -12.66
    assert price["explanation"] == "Total price: $303 · 1 room × 2 nights incl. taxes & fees"
    # zoneNames = "Near Santa Fe Station Bingo Room|Chrome Showroom": landmarks, not a zone
    assert card["location"]["zone"] is None
    assert card["location"]["description"] == "Near Santa Fe Station Bingo Room | Chrome Showroom"
    assert {r["location"]["zone"] for r in lv["results"]} == {
        None, "Las Vegas Strip", "East of the Strip", "West of The Strip", "Downtown - Fremont Street"}
    assert all(r["star_type"] in ("star", "diamond", "homes_and_apartments") for r in lv["results"])
    assert all(i.endswith(".jpg") for r in lv["results"] for i in r["images"])


def test_hotel_search_keyword_and_empty():
    kw = P.parse_hotel_search(fixture("hotels_search_keyword.json"))
    assert [r["id"] for r in kw["results"]] == [346403, 425378]
    assert kw["total_count"] == 2 and kw["is_last_page"] is True
    empty = P.parse_hotel_search(fixture("hotels_search_empty.json"))
    assert empty["results"] == [] and empty["is_last_page"] is True
    assert P.parse_hotel_search({})["results"] == []


# ---- hotel page -------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def grand_hyatt():
    props = P.extract_rsc_props(fixture("hotel_page_grand_hyatt.html"))
    assert props is not None
    return P.parse_hotel_details(props, fixture("hotel_seo_grand_hyatt.json"))


def test_hotel_details_identity(grand_hyatt):
    h = grand_hyatt
    assert h["id"] == 346403 and h["name"] == "Grand Hyatt Shanghai"
    assert h["local_name"] == "上海金茂君悦大酒店"
    assert h["link"] == "https://www.trip.com/hotels/detail/?hotelId=346403"
    assert h["star"] == 5 and h["star_type"] == "star"
    assert h["brand"] == "Hyatt" and h["opened_year"] == 1999 and h["renovated_year"] == 2014
    assert h["phone"] == "+86-21-80241234" and h["email"] is None
    assert h["description"].startswith("The crowning glory")
    assert h["award"]["rank"] == 3 and "Premium Hotels in Shanghai" in h["award"]["title"]
    assert h["highlights"][0]["title"] == "Picture-perfect" and h["highlights"][0]["description"]
    assert h["is_closed"] is False


def test_hotel_details_rating_location(grand_hyatt):
    h = grand_hyatt
    assert h["rating"]["score"] == 9.4 and h["rating"]["review_count"] == 18928
    assert h["rating"]["subscores"]["cleanliness"] == 9.5
    assert h["rating"]["recommend_rate"] == "93% Recommended"
    assert h["location"]["address"].startswith("No. 88 Shiji Avenue")
    assert h["location"]["zone"] == "Lujiazui Area" and h["location"]["city_id"] == 2
    assert h["location"]["country"] == "China"
    assert h["location"]["coordinate"] == {"latitude": 31.235151, "longitude": 121.506082, "system": "WGS84"}
    assert h["location"]["transit"].startswith("440m from metro station")
    nearby = h["location"]["nearby"]
    assert nearby[0]["name"] == "The Bund" and nearby[0]["distance_meters"] == 4160 and nearby[0]["mode"] == "drive"
    assert nearby[0]["type"] == "Attraction"


def test_hotel_details_policies_amenities(grand_hyatt):
    h = grand_hyatt
    assert h["check_in"] == "After 15:00" and h["check_out"] == "Before 12:00"
    names = {p["name"] for p in h["policies"]}
    assert {"check_in_and_out", "deposit", "pets", "breakfast", "children"} <= names
    deposit = next(p for p in h["policies"] if p["name"] == "deposit")
    assert deposit["items"][0] == {"title": "Deposit", "description": "Deposit required"}
    pop = h["amenities"]["popular"][0]
    assert pop["name"] == "Indoor swimming pool" and pop["is_free"] is True
    # an item with no fee note is unknown, not "not free"
    every = [i for g in h["amenities"]["groups"] for i in g["items"]] + h["amenities"]["popular"]
    assert all(i["is_free"] is None for i in every if i["fee_note"] is None and i["is_free"] is not True)
    assert any(i["is_free"] is None for i in every)
    assert all("_" in p["name"] or p["name"].islower() for p in h["policies"])   # snake_case names
    assert pop["details"]["Features"] == ["Pool with view", "Heated pool"]
    assert h["amenities"]["groups"][0]["name"] == "Internet"
    assert h["amenities"]["groups"][0]["items"][0]["name"] == "Wi-Fi in public areas"
    assert h["amenities"]["total_count"] == 68 and len(h["amenities"]["all"]) == 43
    assert len(h["photos"]) == 11 and h["photo_count"] == 11


def test_hotel_details_seo_extras(grand_hyatt):
    h = grand_hyatt
    assert h["about"][0] == {"question": "Nearest Airport", "answer": "Shanghai Hongqiao Airport"}
    assert len(h["faq"]) == 15 and "{1}" not in h["faq"][0]["answer"]
    assert len(h["rooms"]) == 26
    room = next(r for r in h["rooms"] if r["id"] == 479857)
    assert room["name"] == "Diplomatic Suite" and room["area"] == "110m²" and room["floor"] == "78-85"
    assert room["bed"] == "1 Queen bed" and room["views"] == ["City view"] and len(room["photos"]) == 5
    assert room["max_adults"] == 2 and room["max_children"] == 1
    assert h["breadcrumbs"][0] == {"name": "Trip.com", "link": "https://us.trip.com"}


def test_hotel_details_sparse_page_and_unknown_seo():
    props = P.extract_rsc_props(fixture("hotel_page_hostel.html"))
    h = P.parse_hotel_details(props, fixture("hotel_seo_hostel.json"))
    assert h["id"] == 126577790 and h["name"] == "Shanghai Hostel"
    assert h["location"]["city_id"] == 2
    assert isinstance(h["policies"], list) and isinstance(h["amenities"]["groups"], list)
    # the SEO op of an unknown id carries data.errors and nothing else
    h2 = P.parse_hotel_details(props, fixture("hotel_seo_unknown.json"))
    assert h2["id"] == 126577790 and h2["about"] == [] and h2["rooms"] == []
    h3 = P.parse_hotel_details(props, None)
    assert h3["name"] == "Shanghai Hostel"


def test_hotel_page_unknown_is_a_redirect_shell():
    html = fixture("hotel_page_unknown.html")
    assert P.extract_rsc_props(html) is None
    assert "/hotels/pages/404" in html
    assert P.extract_rsc_props("<html></html>") is None
    assert P.parse_hotel_details({}, None)["id"] is None


# ---- hotel photos ---------------------------------------------------------------------------------

def test_hotel_photos():
    album = P.parse_hotel_photos(fixture("hotel_album_grand_hyatt.json"))
    assert album["hotel_photo_count"] == 210 and album["guest_photo_count"] == 232
    assert len(album["featured"]) == 7
    first = album["hotel_photos"][0]
    assert first["category"] == "Featured" and first["count"] == 11 and len(first["photos"]) == 11
    assert first["photos"][0]["id"] == 1717686493 and first["photos"][0]["link"].startswith("https://")
    guest = album["guest_photos"][0]
    assert guest["category"] == "Exterior" and guest["count"] == 20
    assert guest["photos"][0]["rating"] == 10.0 and guest["photos"][0]["author"] == "xy940811"
    sparse = P.parse_hotel_photos(fixture("hotel_album_hostel.json"))
    assert isinstance(sparse["hotel_photos"], list)


def test_hotel_photos_unknown_is_empty():
    album = P.parse_hotel_photos(fixture("hotel_album_unknown.json"))
    assert album["hotel_photo_count"] == 0 and album["guest_photo_count"] == 0
    assert album["featured"] == [] and album["hotel_photos"] == []
    assert P.parse_hotel_photos({})["featured"] == []


# ---- reviews ----------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def reviews():
    return P.parse_hotel_reviews(fixture("hotel_reviews_grand_hyatt.json"))


def test_reviews_rows(reviews):
    assert len(reviews["results"]) == 10
    r = reviews["results"][0]
    assert r["id"] == 2134272597 and r["rating"] == 10.0 and r["rating_max"] == 10.0
    assert r["rating_label"] == "Outstanding" and r["subscores"]["cleanliness"] == 10.0
    assert r["language"] == "zh" and r["translated_text"].startswith("Although the Grand Hyatt")
    assert r["translated_language"] == "en" and r["is_recommended"] is True
    assert r["traveler_type"] == "Couple" and r["room"] == "Club Room Queen Bed" and r["room_id"] == 301750431
    assert r["stay_date"] == "2026-10-01" and r["date"] == "2026-10-09T16:42:41"
    assert len(r["images"]) == 18 and r["images"][0].startswith("https://")
    assert r["source"] == "ctrip" and r["is_trip_review"] is False
    assert r["author"]["name"] == "YPluotuo" and r["author"]["country_code"] == "CN"
    assert r["author"]["review_count"] == 31 and r["author"]["level"] == "Review Expert"
    assert r["hotel_reply"]["date"] == "2026-10-09" and r["hotel_reply"]["translated_text"].startswith("Dear Guest")


def test_reviews_summary(reviews):
    s = reviews["summary"]
    assert s["score"] == 9.4 and s["review_count"] == 18928 and s["label"] == "Outstanding"
    assert s["subscores"] == {"location": 9.5, "amenities": 9.2, "service": 9.3, "cleanliness": 9.5}
    assert reviews["total_count"] == 18928 and reviews["filtered_count"] == 14599
    assert reviews["tags"][0] == {"id": 70, "name": "Delicious breakfast", "count": 1129}
    # the "All Travelers" / "All Languages" / "All Room Types" no-filter rows are left out
    assert reviews["traveler_types"][0] == {"id": 10, "name": "Business traveler", "count": 5141}
    assert len(reviews["languages"]) == 18 and len(reviews["rooms"]) == 23
    assert all(row["count"] is not None for row in reviews["traveler_types"] + reviews["languages"] + reviews["rooms"])
    assert reviews["ai_summary"][0]["topic"] == "Location" and reviews["ai_summary"][0]["is_positive"] is True


def test_review_videos():
    row = P._review({"id": 2167257249, "videoList": [
        {"cover": "https://ak-d.tripcdn.com/images/0203s12000udwlxch5273_W_350_350_R5_Q70.jpg",
         "url": "https://video.c-ctrip.com/videos/230ecn14x9b7qaw3r1672.mp4", "duration": "12.0", "videoId": 10442757}]})
    assert row["videos"] == ["https://video.c-ctrip.com/videos/230ecn14x9b7qaw3r1672.mp4"]


def test_review_hidden_region_is_null():
    row = P._review({"id": 1, "userInfo": {"nickName": "Guest User", "regionCode": "XX", "regionName": ""}})
    assert row["author"]["country_code"] is None and row["author"]["country"] is None
    assert P._review({"userInfo": {"regionCode": "US"}})["author"]["country_code"] == "US"


def test_reviews_sparse_and_unknown():
    sparse = P.parse_hotel_reviews(fixture("hotel_reviews_hostel.json"))
    assert sparse["total_count"] == 51 and len(sparse["results"]) == 10
    unknown = P.parse_hotel_reviews(fixture("hotel_reviews_unknown.json"))
    assert unknown["results"] == [] and unknown["total_count"] == 0 and unknown["summary"] is None
    assert P.parse_hotel_reviews({})["results"] == []


# ---- attractions ------------------------------------------------------------------------------------

def test_attractions_page_tokyo():
    page = P.parse_attractions_page(fixture("attractions_tokyo.json"))
    assert page["destination"] == {"id": 294, "name": "Tokyo", "product_count": 126172, "is_in_china": False}
    assert page["total_count"] == 11413 and len(page["results"]) == 12
    kinds = [r["kind"] for r in page["results"]]
    assert kinds.count("attraction") == 7 and kinds.count("experience") == 5
    poi = page["results"][0]
    assert poi["id"] == 10521877 and poi["name"] == "Tokyo DisneySea" and poi["local_name"] == "東京ディズニーシー"
    assert poi["link"] == "https://www.trip.com/travel-guide/attraction/urayasu/tokyo-disneysea-10521877"
    assert poi["rating"] == {"score": 4.7, "review_count": 11625, "tripadvisor_review_count": 6751}
    assert poi["coordinate"]["system"] == "WGS84" and poi["coordinate"]["latitude"] == 35.626711
    assert poi["price"] == {"amount": 57.0, "currency": "USD", "bookings": 228000}
    assert poi["opening_hours"] == "Open: 9:00 AM–9:00 PM" and poi["duration"] == "1 day"
    assert poi["parent"] == {"id": 153337667, "name": "Tokyo Disney Resort"}
    assert poi["rank"].startswith("2026") and poi["district"] == "Urayasu"
    product = next(r for r in page["results"] if r["kind"] == "experience")
    assert product["id"] == 92795279 and product["rating"]["review_count"] == 792 and product["bookings"] == 9900
    assert product["link"] == "https://www.trip.com/things-to-do/detail/92795279"


def test_attractions_page_new_york_hours_and_currency():
    page = P.parse_attractions_page(fixture("attractions_new_york.json"), currency="USD")
    hours = [r["opening_hours"] for r in page["results"] if r["kind"] == "attraction" and r["opening_hours"]]
    assert hours and not any(h.startswith("Today") for h in hours)
    assert "Open today 8:30 AM–6:30 PM(Admission ends at 5:00 PM)" in hours      # was "TodayOpen : 8:30 AM…"
    # product cards carry no currency of their own: the requested one labels them
    product = next(r for r in page["results"] if r["kind"] == "experience")
    assert product["id"] == 44268761 and product["price"]["currency"] == "USD" and product["price"]["amount"] == 80.0
    assert P.parse_attractions_page(fixture("attractions_new_york.json"))["results"][4]["price"]["currency"] is None


def test_attractions_page_phrase_and_unknown():
    museum = P.parse_attractions_page(fixture("attractions_museum.json"))
    assert len(museum["results"]) == 12 and museum["total_count"] == 19564
    unknown = P.parse_attractions_page(fixture("attractions_unknown.json"))
    assert unknown == {"destination": None, "results": [], "total_count": 0}
    assert P.parse_attractions_page({})["results"] == []


def test_attraction_page():
    a = P.parse_attraction_page(fixture("attraction_ghibli_museum.json"))
    assert a["id"] == 88070 and a["name"] == "Ghibli Museum" and a["local_name"] == "三鷹の森ジブリ美術館"
    assert a["link"] == "https://www.trip.com/travel-guide/attraction/mitaka/ghibli-museum-88070"
    assert a["type"] == "sight" and a["categories"] == ["Themed exhibition halls"]
    assert a["address"].startswith("1 Chome-1-83") and a["duration"] == "2–3 hours" and a["is_free"] is False
    assert "<p>" not in a["introduction"] and a["introduction"].startswith("Ghibli Museum")
    assert a["phones"] == [{"number": "+81-570-055777", "purpose": "Ticket inquiry"}]
    assert a["coordinate"] == {"latitude": 35.696238, "longitude": 139.570432, "system": "GCJ02"}
    assert a["district"] == {"id": 56806, "name": "Mitaka", "is_in_china": False}
    assert a["city"] == {"id": 294, "name": "Tokyo"}
    assert a["rating"] == {"score": 4.6, "review_count": 1466, "tripadvisor_review_count": 1996}
    assert a["favorite_count"] == 12635 and a["rank"]["position"] == 84
    assert a["opening_hours"]["summary"].startswith("Open from 10:00am")
    assert a["opening_hours"]["rules"][0] == {"dates": "Nov 7–Nov 18", "description": "Closed all day"}
    assert a["entry_policies"][0]["name"] == "No pets allowed"
    assert len(a["images"]) == 5 and a["image_count"] == 24
    assert a["breadcrumbs"][0] == {"name": "Asia", "type": "continent"}
    assert P.parse_attraction_page({}) is None


# ---- experiences --------------------------------------------------------------------------------------

def test_experiences_page_tokyo():
    page = P.parse_experiences_page(fixture("experiences_tokyo.json"), slug="tokyo")
    d = page["destination"]
    assert d["slug"] == "tokyo" and d["name"] == "Tokyo" and d["district_id"] == 294 and d["hotel_city_id"] == 228
    assert d["country"] == "Japan" and d["link"] == "https://www.trip.com/things-to-do/experiences/tokyo/"
    assert len(page["results"]) == 40
    p = page["results"][0]
    assert p["id"] == 67937814 and p["title"].startswith("Mount Fuji Hakone")
    assert p["rating"] == {"score": 4.9, "label": "Perfect", "review_count": 1850}
    assert p["bookings"] == 7300
    assert p["price"] == {"amount": 53.34, "currency": "USD", "prefix": "From", "original_amount": 57.98, "discount": "8% off"}
    assert p["badges"] == ["Free cancellation"] and p["tags"][0] == "Duration: 10 hrs"
    assert p["departure"] == "Departs from Tokyo" and p["is_trip_select"] is True
    assert page["categories"][0]["slug"] == "day-tour" and page["categories"][0]["link"].endswith("tokyo-day-tour")
    assert len(page["reviews"]) == 6 and page["reviews"][0]["product_id"] == 67937814 and page["reviews"][0]["original_text"]


def test_experiences_page_mixes_attractions_on_us_pages():
    page = P.parse_experiences_page(fixture("experiences_newyork.json"), slug="newyork")
    assert page["destination"]["hotel_city_id"] == 633 and len(page["results"]) == 20
    poi = page["results"][0]
    assert poi["kind"] == "attraction" and poi["id"] == 134445986 and poi["title"] == "SUMMIT One Vanderbilt"
    assert poi["link"] == "https://www.trip.com/travel-guide/attraction/new-york/summit-one-vanderbilt-134445986"
    assert poi["type"] is None and poi["popularity_score"] == 9.2 and poi["rank"].startswith("2026")
    assert poi["price"]["amount"] == 47.91 and poi["price"]["currency"] == "USD"
    product = next(r for r in page["results"] if r["kind"] == "experience")
    assert product["id"] == 44268761 and product["link"] == "https://www.trip.com/things-to-do/detail/44268761"
    assert sum(1 for r in page["results"] if r["kind"] == "attraction") == 10


def test_experiences_page_paris_and_unknown():
    paris = P.parse_experiences_page(fixture("experiences_paris.json"), slug="paris")
    assert paris["destination"]["hotel_city_id"] == 192 and len(paris["results"]) == 37
    assert P.parse_experiences_page(fixture("experiences_unknown.json"), slug="nosuchcityxyz") is None
    assert P.parse_experiences_page({}) is None


def test_experience_page():
    e = P.parse_experience_page(fixture("experience_capybara_cafe.json"))
    assert e["id"] == 100444859 and e["name"].startswith("Capybara Cafe Moffu Tokyo")
    assert e["link"] == "https://www.trip.com/things-to-do/detail/100444859"
    assert e["category"] == "Meals" and e["categories"] == ["Experiences", "Local cuisine"] or e["categories"][0] == "Experiences"
    assert e["description"].startswith("Store introduction")
    assert e["rating"] == {"score": 3.7, "review_count": 7, "max": 5.0}
    assert e["sales"] == {"monthly": 25, "yearly": 402}
    assert e["price"] == {"from": 25.28, "to": 25.28, "currency": "USD", "offer_count": 8, "availability": "InStock"}
    assert e["city"]["district_id"] == 294 and e["city"]["hotel_city_id"] == 228 and e["city"]["name"] == "Tokyo"
    assert e["vendor"]["id"] == 1258435
    assert len(e["images"]) == 15 and len(e["packages"]) == 8 and len(e["tickets"]) == 8
    assert e["packages"][0]["id"] == 100447373 and e["packages"][0]["max_quantity"] == 8
    assert e["tickets"][0] == {"id": 100447393, "name": "Quantity", "description": "1 years old (inclusive)-99 years old (inclusive)",
                               "currency": "USD", "min_quantity": 1, "max_quantity": 8, "age_min": 1, "age_max": 99}
    assert e["refund_policy"]["label"] == "Conditional cancellation" and len(e["refund_policy"]["rules"]) == 5
    assert {"name": "service language", "values": ["English"]} in e["options"]
    assert e["properties"][0] == {"name": "Voucher", "values": ["e-voucher"]}
    assert e["booking"]["has_inventory"] is True and e["booking"]["advance_cutoff_time"] == "23:59"
    assert e["booking_notes"][0]["name"] == "Book now for today"
    assert e["breadcrumbs"][1] == {"name": "Tokyo local experience", "link": "/things-to-do/?districtId=294"}
    assert e["sections"][0]["title"] == "Highlights"


def test_experience_page_options_merged():
    la = P.parse_experience_page(fixture("experience_la_tour.json"))
    assert la["id"] == 106382468 and la["name"] == "Full-Day City Tour of LA, Hollywood, Beverly Hills & Beaches"
    options = {o["name"]: o["values"] for o in la["options"]}
    assert list(options) == ["combo", "Time slot", "Vehicle type"]            # once each, Chinese label translated
    assert options["Time slot"] == ["09:45", "08:00", "07:30", "07:00"]
    assert options["Vehicle type"] == ["Transportation not included", "car (type to be confirmed)"]
    assert "Itinerary" not in options                                          # "Itinerary: itinerary" placeholder
    assert len(options["combo"]) == 5 and len(la["packages"]) == 5


def test_experience_page_tour_and_missing():
    tour = P.parse_experience_page(fixture("experience_fuji_tour.json"))
    assert tour["id"] == 67937814 and tour["packages"] and tour["city"]["name"] == "Tokyo"
    assert P.parse_experience_page({}) is None


# ---- flights ----------------------------------------------------------------------------------------------

def test_airports():
    rows = P.parse_airports(fixture("airports_tokyo.json"))
    tokyo = rows[0]
    assert tokyo["id"] == 228 and tokyo["kind"] == "city" and tokyo["code"] == "TYO" and tokyo["country"] == "Japan"
    assert tokyo["country_code"] == "JP" and tokyo["timezone_offset_hours"] == 9.0 and tokyo["airport_code"] is None
    assert [a["code"] for a in tokyo["airports"]] == ["NRT", "HND"]
    assert tokyo["airports"][0]["name"] == "Narita International Airport" and tokyo["airports"][0]["city_code"] == "TYO"
    london = P.parse_airports(fixture("airports_london.json"))
    assert london[0]["code"] == "LON" and len(london[0]["airports"]) == 6
    assert london[1]["kind"] == "city_group"
    assert P.parse_airports(fixture("airports_none.json")) == []


def test_airports_region_and_city_points():
    rows = P.parse_airports(fixture("airports_new_york.json"))
    city, region = rows[0], rows[1]
    assert city["kind"] == "city" and city["code"] == "NYC" and [a["code"] for a in city["airports"]] == ["JFK", "LGA", "EWR", "SWF"]
    # PoiSearch gives these airports midtown Manhattan's point: nulled, not passed off as the airport's
    assert all(a["latitude"] is None and a["longitude"] is None for a in city["airports"])
    assert city["latitude"] == 40.751667 and city["cities"] == []
    assert region["kind"] == "region" and region["name"] == "New York State" and region["timezone_offset_hours"] is None
    assert region["airports"] == [] and [c["code"] for c in region["cities"][:4]] == ["NYC", "ALB", "BUF", "SYR"]


def test_fare_day():
    payload = fixture("fare_calendar_sha_tyo.json")
    priced = next(r for r in payload["lowPriceInCalenderDtoInfoList"] if r["currencyPrice"] > 0)
    day = P._epoch_day(priced["dDate"])
    fare = P.parse_fare_day(payload, day)
    assert fare["date"] == day and fare["price"] == float(priced["currencyPrice"]) and fare["currency"] == "USD"
    other = P.parse_fare_day(payload, "2026-11-07")
    assert other["price"] is None and other["currency"] == "USD"
    unknown = P.parse_fare_day(fixture("fare_calendar_unknown.json"), "2026-12-10")
    assert unknown == {"date": "2026-12-10", "price": None, "currency": None, "is_direct": None, "note": None}


# ---- value helpers -----------------------------------------------------------------------------------------

def test_value_helpers():
    assert P._count("3,411 reviews") == 3411 and P._count("1.8k reviews") == 1800 and P._count("228k+ booked") == 228000
    assert P._count(None) is None and P._count(12) == 12
    assert P._amount("-$168.57") == -168.57 and P._amount("US$53.34") == 53.34 and P._amount("") is None
    assert P._money("US$53.34") == (53.34, "USD") and P._money("€331") == (331.0, "EUR")
    assert P._date8("20261210") == "2026-12-10"
    assert P._iso("2026-10-09 16:42:41") == "2026-10-09T16:42:41" and P._iso("2026-10-01 00:00:00") == "2026-10-01"
    assert P._strip_html("<p>One</p><p>Two &amp; three</p>") == "One\nTwo & three"
    assert P._markup("Prices at {1}Grand Hyatt{/1} vary") == "Prices at Grand Hyatt vary"
    assert P._epoch_day(1794009600) == "2026-11-07"
