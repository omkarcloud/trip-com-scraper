"""The /trip-com/* endpoint functions (route_glue's route_table calls them
with the schema's loaded kwargs). Every endpoint caches its own response
through cache.cached_call keyed on the validated params (tofler-style).
"""
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from cache import cached_call
from trip_com import cache_config as ttl
from trip_com import parsers, refs
from trip_com.fetch import (TripComNotFound, fetch_airports, fetch_attraction_page, fetch_attractions_page,
                            fetch_destinations, fetch_experience_page, fetch_experiences_page, fetch_fare_day,
                            fetch_hotel_album, fetch_hotel_list, fetch_hotel_page, fetch_hotel_reviews,
                            fetch_hotel_seo)

HOTELS_PAGE_SIZE = 20          # fetchMetaList's ceiling (30+ is an upstream 400)
DEFAULT_STAY_NIGHTS = 2
DEFAULT_CALENDAR_DAYS = 7
FARE_WORKERS = 8

_fare_pool = None


def _stay(data):
    """Fill the default check-out so the cache key and the upstream body are
    complete (the schema only defaults check-in)."""
    data = dict(data)
    if not data.get("check_out"):
        check_in = date.fromisoformat(data["check_in"])
        data["check_out"] = (check_in + timedelta(days=DEFAULT_STAY_NIGHTS)).isoformat()
    return data


def _default_stay():
    check_in = date.today() + timedelta(days=14)
    return check_in.isoformat(), (check_in + timedelta(days=DEFAULT_STAY_NIGHTS)).isoformat()


# ---- destinations/search ------------------------------------------------------------

def _destinations_search(query, locale):
    results = parsers.parse_destinations(fetch_destinations(query, locale=locale))
    return {"query": query, "count": len(results), "results": results}


def destinations_search(**data):
    """Look up a city, landmark or hotel name and get the Trip.com city id that hotels/search takes."""
    return cached_call("trip_com.destinations_search", data, _destinations_search, ttl.DESTINATIONS_CACHE)


# ---- hotels/search ------------------------------------------------------------------

def _resolve_city(destination, locale):
    """{"city_id"} | {"name"} -> (city_id, implied_keyword, place). A name
    goes through the autocomplete; a hotel / landmark name resolves to its
    city plus the keyword filter Trip.com itself would apply."""
    if destination.get("city_id"):
        return destination["city_id"], None, None
    name = destination["name"]
    places = cached_call("trip_com.destinations_search", {"query": name, "locale": locale},
                         _destinations_search, ttl.DESTINATIONS_CACHE)["results"]
    place = next((p for p in places if p.get("type") == "city" and p.get("id")), None) or \
        next((p for p in places if p.get("id")), None)
    if not place:
        raise TripComNotFound(f"no Trip.com destination matches {name!r}")
    return place["id"], place.get("keyword"), place


def _hotel_filters(data):
    tokens = [refs.HOTEL_SORTS[data["sort"]]]
    if data.get("min_score"):
        tokens.append(refs.MIN_SCORES[data["min_score"]])
    if data.get("free_cancellation"):
        tokens.append(refs.FREE_CANCELLATION)
    if data.get("breakfast"):
        tokens.append(refs.BREAKFAST_INCLUDED)
    if data.get("payment"):
        tokens.append(refs.PAYMENTS[data["payment"]])
    if data.get("bed_type"):
        tokens.append(refs.BED_TYPES[data["bed_type"]])
    filters = [refs.hotel_filter(token) for token in tokens]
    for star in data.get("stars") or []:
        filters.append(refs.star_filter(star))
    if data.get("min_price") is not None or data.get("max_price") is not None:
        filters.append(refs.price_filter(data.get("min_price"), data.get("max_price")))
    return filters


def _hotels_search(**data):
    city_id, implied_keyword, place = _resolve_city(data["destination"], data["locale"])
    keyword = data.get("query") or implied_keyword or ""
    payload = fetch_hotel_list(
        city_id, data["check_in"], data["check_out"], adults=data["adults"], children_ages=data["children_ages"],
        rooms=data["rooms"], filters=_hotel_filters(data), keyword=keyword, page=data["page"],
        page_size=HOTELS_PAGE_SIZE, locale=data["locale"], currency=data["currency"])
    parsed = parsers.parse_hotel_search(payload)
    total = parsed["total_count"]
    first = (parsed["results"] or [{}])[0].get("location") or {}
    return {
        "destination": {"city_id": city_id, "name": (place or {}).get("name") or first.get("city"),
                        "country": (place or {}).get("country"), "link": refs.hotel_list_link(city_id)},
        "query": keyword or None,
        "check_in": data["check_in"],
        "check_out": data["check_out"],
        "nights": (date.fromisoformat(data["check_out"]) - date.fromisoformat(data["check_in"])).days,
        "adults": data["adults"],
        "children_ages": data["children_ages"],
        "rooms": data["rooms"],
        "currency": data["currency"],
        "results": parsed["results"],
        "pagination": {
            "page": data["page"],
            "items_per_page": HOTELS_PAGE_SIZE,
            "has_next": not parsed["is_last_page"],
            "total_pages": math.ceil(total / HOTELS_PAGE_SIZE) if total else None,
            "total_count": total,
        },
    }


def hotels_search(**data):
    """Hotels in a city for your dates: stars, guest score, location, photos and the cheapest room's
    nightly price, total, taxes and discounts (20 per page), with sort, star, price, score,
    free-cancellation, breakfast, payment and bed-type filters."""
    return cached_call("trip_com.hotels_search", _stay(data), _hotels_search, ttl.HOTELS_SEARCH_CACHE)


# ---- hotels/details -----------------------------------------------------------------

def _hotel_details(**data):
    html = fetch_hotel_page(data["hotel"], data["check_in"], data["check_out"], adults=data["adults"],
                            children_ages=data["children_ages"], rooms=data["rooms"],
                            currency=data["currency"], locale=data["locale"])
    props = parsers.extract_rsc_props(html)
    if props is None:
        raise TripComNotFound(f"hotel {data['hotel']} not found")
    try:
        seo = fetch_hotel_seo(data["hotel"], data["check_in"], data["check_out"], adults=data["adults"],
                              children=len(data["children_ages"]), rooms=data["rooms"],
                              locale=data["locale"], currency=data["currency"])
    except TripComNotFound:
        seo = None     # the page rendered, so the hotel exists; the extras are optional
    return parsers.parse_hotel_details(props, seo)


def hotel_details(**data):
    """A full hotel profile: names, stars, brand, description, guest rating with subscores, address,
    coordinates, nearby places, policies, amenities, room types, FAQ and photos."""
    return cached_call("trip_com.hotel_details", _stay(data), _hotel_details, ttl.HOTEL_DETAILS_CACHE)


# ---- hotels/photos ------------------------------------------------------------------

def _hotel_photos(hotel, locale):
    check_in, check_out = _default_stay()
    album = parsers.parse_hotel_photos(fetch_hotel_album(hotel, check_in, check_out, locale=locale))
    if not album["hotel_photo_count"] and not album["guest_photo_count"] and not album["featured"]:
        # an unknown id answers an empty album; the SEO op tells it apart
        fetch_hotel_seo(hotel, check_in, check_out, locale=locale)
    return {"id": hotel, "link": refs.hotel_link(hotel), **album}


def hotel_photos(**data):
    """Every photo of a hotel: the hotel's own album by category plus guest photos with their ratings."""
    return cached_call("trip_com.hotel_photos", data, _hotel_photos, ttl.HOTEL_PHOTOS_CACHE)


# ---- hotels/reviews -----------------------------------------------------------------

def _hotel_reviews(**data):
    check_in, check_out = _default_stay()
    travel_types = [refs.TRAVELER_TYPES[data["traveler_type"]]] if data.get("traveler_type") else []
    payload = fetch_hotel_reviews(
        data["hotel"], page=data["page"], page_size=data["per_page"], order_by=refs.REVIEW_SORTS[data["sort"]],
        travel_types=travel_types, repeat_comment=0 if data.get("include_duplicates") else 1,
        check_in=check_in, check_out=check_out, locale=data["locale"])
    parsed = parsers.parse_hotel_reviews(payload)
    if data["page"] == 1 and not parsed["results"] and not parsed["total_count"]:
        # no reviews at all: an unknown hotel answers exactly this, so confirm
        fetch_hotel_seo(data["hotel"], check_in, check_out, locale=data["locale"])
    total = parsed["filtered_count"] if parsed["filtered_count"] is not None else parsed["total_count"]
    return {
        "id": data["hotel"],
        "link": refs.hotel_link(data["hotel"]),
        "sort": data["sort"],
        "traveler_type": data.get("traveler_type"),
        "summary": parsed["summary"],
        "ai_summary": parsed["ai_summary"],
        "tags": parsed["tags"],
        "traveler_types": parsed["traveler_types"],
        "languages": parsed["languages"],
        "rooms": parsed["rooms"],
        "results": parsed["results"],
        "pagination": {
            "page": data["page"],
            "items_per_page": data["per_page"],
            "total_pages": math.ceil(total / data["per_page"]) if total else 0,
            "total_count": total or 0,
        },
    }


def hotel_reviews(**data):
    """Guest reviews of a hotel (up to 100 per page): rating, subscores, text with translation, stay date,
    room, traveler type, photos and the hotel's reply, plus the score summary and AI summary."""
    return cached_call("trip_com.hotel_reviews", data, _hotel_reviews, ttl.HOTEL_REVIEWS_CACHE)


# ---- attractions/search -------------------------------------------------------------

def _attractions_search(query, currency, locale):
    next_data = parsers.extract_next_data(fetch_attractions_page(query, currency=currency, locale=locale))
    parsed = parsers.parse_attractions_page(next_data or {}, currency=currency)
    return {"query": query, "currency": currency, "destination": parsed["destination"],
            "count": len(parsed["results"]), "total_count": parsed["total_count"], "results": parsed["results"]}


def attractions_search(**data):
    """Top attractions and bookable tours for a destination or phrase: rating, reviews, price, hours,
    coordinates and ranking."""
    return cached_call("trip_com.attractions_search", data, _attractions_search, ttl.ATTRACTIONS_SEARCH_CACHE)


# ---- attractions/details ------------------------------------------------------------

def _attraction_details(attraction, currency, locale):
    next_data = parsers.extract_next_data(fetch_attraction_page(attraction, currency=currency, locale=locale))
    record = parsers.parse_attraction_page(next_data or {})
    if record is None:
        raise TripComNotFound(f"attraction {attraction} not found")
    return record


def attraction_details(**data):
    """A full attraction profile: local name, address, introduction, opening hours, phone, rating,
    ranking, entry policies, coordinates and photos."""
    return cached_call("trip_com.attraction_details", data, _attraction_details, ttl.ATTRACTION_DETAILS_CACHE)


# ---- experiences/search -------------------------------------------------------------

def _experiences_search(destination, currency, locale):
    next_data = parsers.extract_next_data(fetch_experiences_page(destination, currency=currency, locale=locale))
    parsed = parsers.parse_experiences_page(next_data or {}, slug=destination, currency=currency)
    if parsed is None:
        raise TripComNotFound(f"no Trip.com experiences page for {destination!r} (use the city slug of a "
                              "trip.com/things-to-do/experiences/<city>/ link)")
    return {"currency": currency, "count": len(parsed["results"]), **parsed}


def experiences_search(**data):
    """Bookable tours, tickets and activities in a city: price, discount, rating, bookings and tags,
    plus the city's categories and recent reviews."""
    return cached_call("trip_com.experiences_search", data, _experiences_search, ttl.EXPERIENCES_CACHE)


# ---- experiences/details ------------------------------------------------------------

def _experience_details(experience, currency, locale):
    next_data = parsers.extract_next_data(fetch_experience_page(experience, currency=currency, locale=locale))
    record = parsers.parse_experience_page(next_data or {})
    if record is None:
        raise TripComNotFound(f"experience {experience} not found")
    return record


def experience_details(**data):
    """A full tour or ticket listing: description, packages, ticket types, price range, rating, sales,
    vendor, refund policy and photos."""
    return cached_call("trip_com.experience_details", data, _experience_details, ttl.EXPERIENCES_CACHE)


# ---- flights/airports ---------------------------------------------------------------

def _flights_airports(query, locale):
    results = parsers.parse_airports(fetch_airports(query, locale=locale))
    return {"query": query, "count": len(results), "results": results}


def flights_airports(**data):
    """Find cities and airports by name or code: IATA codes, country, coordinates and time zone."""
    return cached_call("trip_com.flights_airports", data, _flights_airports, ttl.AIRPORTS_CACHE)


# ---- flights/calendar ---------------------------------------------------------------

def _place(code, locale):
    """A city or airport IATA code -> the fare service's city code + names."""
    places = cached_call("trip_com.flights_airports", {"query": code, "locale": locale},
                         _flights_airports, ttl.AIRPORTS_CACHE)["results"]
    for place in places:
        if place.get("airport_code") == code:       # an airport code answers as a top-level row
            return {"code": place.get("code") or code, "name": place.get("city") or place.get("name"),
                    "country": place.get("country"), "airport_code": code}
        if place.get("code") == code and place.get("kind") != "airport":
            return {"code": code, "name": place.get("name"), "country": place.get("country"), "airport_code": None}
        for airport in place.get("airports") or []:
            if airport.get("code") == code:
                return {"code": airport.get("city_code") or place.get("code"), "name": airport.get("city") or place.get("name"),
                        "country": airport.get("country"), "airport_code": code}
    raise TripComNotFound(f"no Trip.com flight city or airport matches {code}")


def _fare_day(**data):
    payload = fetch_fare_day(data["origin"], data["destination"], data["date"], locale=data["locale"],
                             currency=data["currency"])
    return parsers.parse_fare_day(payload, data["date"])


def _pool():
    global _fare_pool
    if _fare_pool is None:
        _fare_pool = ThreadPoolExecutor(max_workers=FARE_WORKERS, thread_name_prefix="trip-com-fares")
    return _fare_pool


def flights_calendar(**data):
    """The lowest one-way Trip.com fare for every departure day in a window of up to 31 days, plus the
    cheapest day."""
    start = date.fromisoformat(data["start_date"])
    end = date.fromisoformat(data["end_date"]) if data.get("end_date") else start + timedelta(days=DEFAULT_CALENDAR_DAYS - 1)
    origin = _place(data["origin"], data["locale"])
    destination = _place(data["destination"], data["locale"])
    days = [(start + timedelta(days=n)).isoformat() for n in range((end - start).days + 1)]
    # the service prices only the requested day, so one cached call per day
    common = {"origin": origin["code"], "destination": destination["code"], "currency": data["currency"],
              "locale": data["locale"]}

    def one(day):
        return cached_call("trip_com.fare_day", {**common, "date": day}, _fare_day, ttl.FARE_DAY_CACHE)

    results = list(_pool().map(one, days)) if len(days) > 1 else [one(days[0])]
    priced = [r for r in results if r.get("price") is not None]
    lowest = min(priced, key=lambda r: r["price"]) if priced else None
    return {
        "origin": {"code": data["origin"], **origin},
        "destination": {"code": data["destination"], **destination},
        "currency": data["currency"],
        "start_date": days[0],
        "end_date": days[-1],
        "lowest": {"date": lowest["date"], "price": lowest["price"]} if lowest else None,
        "count": len(results),
        "results": results,
    }
