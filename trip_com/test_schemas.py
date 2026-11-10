"""Offline tests for the /trip-com/* request schemas.

    python -m pytest trip_com/test_schemas.py -q
"""
import os
import sys
from datetime import date, timedelta

import pytest
from marshmallow import ValidationError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trip_com import schemas  # noqa: E402


def _day(offset):
    return (date.today() + timedelta(days=offset)).isoformat()


def _errors(schema, data):
    with pytest.raises(ValidationError) as info:
        schema().load(data)
    return info.value.messages


# ---- destinations ------------------------------------------------------------------------

def test_destinations_schema():
    assert schemas.DestinationsSearchSchema().load({"query": " Tokyo "}) == {"query": "Tokyo", "locale": "en-US"}
    assert schemas.DestinationsSearchSchema().load({"query": "Tokyo", "locale": "ja"})["locale"] == "ja-JP"
    assert "query" in _errors(schemas.DestinationsSearchSchema, {})
    assert "locale" in _errors(schemas.DestinationsSearchSchema, {"query": "x", "locale": "xx-YY"})
    assert "bogus" in _errors(schemas.DestinationsSearchSchema, {"query": "x", "bogus": "1"})


# ---- hotels/search -----------------------------------------------------------------------

def test_hotels_search_defaults():
    loaded = schemas.HotelsSearchSchema().load({"destination": "228"})
    assert loaded["destination"] == {"city_id": 228}
    assert loaded["check_in"] == _day(schemas.DEFAULT_CHECKIN_LEAD_DAYS) and loaded["check_out"] is None
    assert loaded["adults"] == 2 and loaded["rooms"] == 1 and loaded["children_ages"] == []
    assert loaded["sort"] == "recommended" and loaded["page"] == 1
    assert loaded["currency"] == "USD" and loaded["locale"] == "en-US"
    assert loaded["query"] is None and loaded["stars"] is None and loaded["free_cancellation"] is None


def test_hotels_search_accepts_every_form():
    loaded = schemas.HotelsSearchSchema().load({
        "destination": "https://www.trip.com/hotels/shanghai-hotels-list-2/", "query": "Grand Hyatt",
        "check_in": _day(10), "check_out": _day(12), "adults": "3", "children_ages": "4, 9", "rooms": "2",
        "sort": "Price_Low_To_High", "stars": "4,5", "min_price": "50", "max_price": "300", "min_score": "8",
        "free_cancellation": "yes", "breakfast": "0", "payment": "prepay", "bed_type": "king",
        "currency": "eur", "locale": "de-de", "page": "3"})
    assert loaded["destination"] == {"city_id": 2} and loaded["query"] == "Grand Hyatt"
    assert loaded["children_ages"] == [4, 9] and loaded["adults"] == 3 and loaded["rooms"] == 2
    assert loaded["sort"] == "price_low_to_high" and loaded["stars"] == ["4", "5"]
    assert loaded["min_price"] == 50.0 and loaded["max_price"] == 300.0 and loaded["min_score"] == "8"
    assert loaded["free_cancellation"] is True and loaded["breakfast"] is False
    assert loaded["payment"] == "prepay" and loaded["bed_type"] == "king"
    assert loaded["currency"] == "EUR" and loaded["locale"] == "de-DE" and loaded["page"] == 3
    assert schemas.HotelsSearchSchema().load({"destination": "Tokyo"})["destination"] == {"name": "Tokyo"}


@pytest.mark.parametrize("data,field", [
    ({}, "destination"),
    ({"destination": "https://www.booking.com/city/2"}, "destination"),
    ({"destination": "228", "check_in": _day(5), "check_out": _day(5)}, "check_out"),
    ({"destination": "228", "check_in": _day(5), "check_out": _day(70)}, "check_out"),
    ({"destination": "228", "check_in": "2020-01-01"}, "check_in"),
    ({"destination": "228", "check_in": "12/10/2026"}, "check_in"),
    ({"destination": "228", "children_ages": "3,19"}, "children_ages"),
    ({"destination": "228", "adults": "0"}, "adults"),
    ({"destination": "228", "rooms": "9"}, "rooms"),
    ({"destination": "228", "sort": "cheapest"}, "sort"),
    ({"destination": "228", "stars": "1"}, "stars"),
    ({"destination": "228", "min_price": "300", "max_price": "50"}, "max_price"),
    ({"destination": "228", "min_score": "9.5"}, "min_score"),
    ({"destination": "228", "free_cancellation": "maybe"}, "free_cancellation"),
    ({"destination": "228", "page": "0"}, "page"),
    ({"destination": "228", "page": str(schemas.MAX_HOTEL_PAGES + 1)}, "page"),
    ({"destination": "228", "currency": "dollars"}, "currency"),
    ({"destination": "228", "url": "x"}, "url"),
    ({"destination": "228", "query": "x" * 101}, "query"),
])
def test_hotels_search_rejects(data, field):
    assert field in _errors(schemas.HotelsSearchSchema, data)


def test_hotel_ref_error_message():
    messages = _errors(schemas.HotelSchema, {"hotel": "grand hyatt"})
    assert "Trip.com hotel id" in messages["hotel"][0]


# ---- hotels/details / photos / reviews ------------------------------------------------------

def test_hotel_schema():
    loaded = schemas.HotelSchema().load({"hotel": "https://www.trip.com/hotels/detail/?hotelId=346403"})
    assert loaded["hotel"] == 346403 and loaded["adults"] == 2
    assert schemas.HotelSchema().load({"hotel": "346403", "check_in": _day(3), "check_out": _day(4)})["check_out"] == _day(4)
    assert "hotel" in _errors(schemas.HotelSchema, {})
    assert "hotel" in _errors(schemas.HotelSchema, {"hotel": "https://www.trip.com/flights/"})


def test_hotel_photos_schema():
    assert schemas.HotelPhotosSchema().load({"hotel": "346403"}) == {"hotel": 346403, "locale": "en-US"}
    assert "check_in" in _errors(schemas.HotelPhotosSchema, {"hotel": "346403", "check_in": _day(2)})


def test_hotel_reviews_schema():
    loaded = schemas.HotelReviewsSchema().load({"hotel": "346403"})
    assert loaded == {"hotel": 346403, "sort": "relevance", "traveler_type": None, "include_duplicates": False,
                      "page": 1, "per_page": 10, "locale": "en-US"}
    loaded = schemas.HotelReviewsSchema().load({"hotel": "346403", "sort": "Recent", "traveler_type": "couple",
                                                "include_duplicates": "true", "page": "4", "per_page": "100"})
    assert loaded["sort"] == "recent" and loaded["traveler_type"] == "couple" and loaded["include_duplicates"] is True
    assert loaded["page"] == 4 and loaded["per_page"] == 100
    assert "per_page" in _errors(schemas.HotelReviewsSchema, {"hotel": "346403", "per_page": "101"})
    assert "traveler_type" in _errors(schemas.HotelReviewsSchema, {"hotel": "346403", "traveler_type": "pets"})
    assert "sort" in _errors(schemas.HotelReviewsSchema, {"hotel": "346403", "sort": "newest"})


# ---- attractions / experiences --------------------------------------------------------------

def test_attractions_schemas():
    assert schemas.AttractionsSearchSchema().load({"query": "Tokyo"}) == {"query": "Tokyo", "currency": "USD", "locale": "en-US"}
    assert "query" in _errors(schemas.AttractionsSearchSchema, {})
    loaded = schemas.AttractionSchema().load({"attraction": "https://www.trip.com/travel-guide/attraction/mitaka/ghibli-museum-88070/"})
    assert loaded["attraction"] == 88070
    assert schemas.AttractionSchema().load({"attraction": "88070", "currency": "jpy"})["currency"] == "JPY"
    assert "attraction" in _errors(schemas.AttractionSchema, {"attraction": "ghibli"})


def test_experiences_schemas():
    assert schemas.ExperiencesSearchSchema().load({"destination": "Tokyo"})["destination"] == "tokyo"
    assert schemas.ExperiencesSearchSchema().load(
        {"destination": "https://www.trip.com/things-to-do/experiences/newyork/"})["destination"] == "newyork"
    assert "destination" in _errors(schemas.ExperiencesSearchSchema, {"destination": "https://www.trip.com/hotels/"})
    assert "destination" in _errors(schemas.ExperiencesSearchSchema, {})
    assert schemas.ExperienceSchema().load({"experience": "https://www.trip.com/things-to-do/detail/67937814?x=1"})["experience"] == 67937814
    assert "experience" in _errors(schemas.ExperienceSchema, {"experience": "fuji tour"})


# ---- flights --------------------------------------------------------------------------------

def test_airports_schema():
    assert schemas.AirportsSearchSchema().load({"query": "lond"}) == {"query": "lond", "locale": "en-US"}
    assert "query" in _errors(schemas.AirportsSearchSchema, {"query": ""})


def test_flights_calendar_schema():
    loaded = schemas.FlightsCalendarSchema().load({"origin": "sha", "destination": "TYO"})
    assert loaded["origin"] == "SHA" and loaded["destination"] == "TYO"
    assert loaded["start_date"] == _day(1) and loaded["end_date"] is None
    assert loaded["currency"] == "USD" and loaded["locale"] == "en-US"
    loaded = schemas.FlightsCalendarSchema().load({"origin": "LON", "destination": "NYC", "start_date": _day(10),
                                                   "end_date": _day(40), "currency": "gbp", "locale": "ja"})
    assert loaded["end_date"] == _day(40) and loaded["currency"] == "GBP" and loaded["locale"] == "ja-JP"


@pytest.mark.parametrize("data,field", [
    ({"destination": "TYO"}, "origin"),
    ({"origin": "Tokyo", "destination": "TYO"}, "origin"),
    ({"origin": "SHA", "destination": "TYO", "start_date": _day(5), "end_date": _day(4)}, "end_date"),
    ({"origin": "SHA", "destination": "TYO", "start_date": _day(5), "end_date": _day(36)}, "end_date"),
    ({"origin": "SHA", "destination": "TYO", "start_date": "2020-01-01"}, "start_date"),
    # the fare service ignores cabin / passengers / trip type, so the API does not offer them
    ({"origin": "SHA", "destination": "TYO", "cabin": "business"}, "cabin"),
    ({"origin": "SHA", "destination": "TYO", "adults": "2"}, "adults"),
    ({"origin": "SHA", "destination": "TYO", "trip_type": "round_trip"}, "trip_type"),
])
def test_flights_calendar_rejects(data, field):
    assert field in _errors(schemas.FlightsCalendarSchema, data)
