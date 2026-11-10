"""Trip.com transport: plain curl_cffi with browser-impersonated TLS. No
browser, no login, no cookies. Egress: one sticky exit per thread session
(config.trip_com_proxy(), knob TRIP_COM_EXIT); a retryable failure drops the
session, so the retry rides a fresh exit.

Two TLS profiles, chosen per surface (2026-11-07, from a home connection and
from datacenter and residential proxy exits alike):

  * "hotel" sessions impersonate Chrome: every SOA JSON call below and the
    hotel / things-to-do pages pass with it. (edge101 draws HTTP 430 on
    every hotel call, so it is never used.)
  * "guide" sessions impersonate Safari 17: the /travel-guide/* pages
    answer Chrome-TLS clients with a 17-byte HTTP 432 and Safari with the
    full page, on every exit tried.

Upstream surfaces (all cookie-free; the JSON ones are
POST https://www.trip.com/restapi/soa2/<service>/<operation>):

  * DESTINATIONS 18416/getTripGlobalHotelSearch {keyword} -> cities with
    their hotel city id (+ a filtername when the term is a hotel/landmark).
  * HOTEL LIST   34951/fetchMetaList — the meta-search twin of the page's
    own fetchHotelList (which needs the sdt phantom-token and answers
    an empty 200 to curl). pageSize <= 20 (30+ is an HTTP 400 404-page);
    an unknown city or a page past the end is an empty hotelList.
  * HOTEL PAGE   GET /hotels/detail/?hotelId=<id> — Next.js RSC stream
    (self.__next_f.push chunks); the row holding `hotelDetailResponse`
    carries the property content. An unknown id is a 200 whose RSC only
    holds a `location.href = ".../hotels/pages/404"` script.
  * HOTEL EXTRAS 28820/ctgetHotelDetailSEO (about / FAQ / room types) and
    28820/ctgethotelalbum (every photo by category). The SEO op answers an
    unknown id with data.errors[]; the album answers an empty album.
  * REVIEWS      34308/getHotelCommentInfo, pageSize up to 100, four sorts,
    traveller-type filter. An unknown id is an empty list with a zeroed
    commentRating, indistinguishable from a hotel without reviews — api.py
    confirms with the SEO op before answering 404.
  * ATTRACTIONS  GET /things-to-do/list?keyword=<text> — server-rendered
    __NEXT_DATA__ with 12 mixed cards (POIs + bookable experiences) for a
    destination or search phrase; the URL has no paging. (The travel-guide
    JSON 19974/getTripAttractionList served curl on 2026-11-07 morning and
    answered "whaleguard block" / empty 200s on every exit and TLS profile
    by the afternoon, so the SSR page is the surface.)
  * ATTRACTION   GET /travel-guide/attraction/x/x-<poiId>/ (Safari TLS) —
    redirects to the canonical slug; __NEXT_DATA__ appData.overviewData is
    the POI record. An unknown id is a real HTTP 404.
  * EXPERIENCES  GET /things-to-do/experiences/<city-slug>/ — __NEXT_DATA__
    with up to 40 products; an unknown slug is a 200 with an empty pageInfo.
  * EXPERIENCE   GET /things-to-do/detail/<productId> — __NEXT_DATA__
    PageModel; an unknown id redirects to an /experiences/ page.
  * AIRPORTS     14427/PoiSearch {key} -> cities with their airports.
  * FARE         14427/GetLowPriceInCalender {DDate, DCity, ACity} -> the
    lowest fare for THAT departure day (the other 359 rows of the calendar
    come back unpriced for curl). An unknown city code is errorCode
    10000001.

Failure taxonomy (scraper_errors, mapped to HTTP by route_glue):
  TripComUpstreamError  transport failure / 5xx                  — retryable
  TripComBlocked        403 / 429 / 430 / 432 / non-JSON body     — retryable
  TripComBadRequest     upstream 400 / a rejected body            — never retried
  TripComNotFound       unknown hotel / POI / product / city code — never retried
"""
import os
import sys
import threading
import time
import uuid
from urllib.parse import quote, urlencode

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import egress
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError
from trip_com.refs import SITE

PROFILES = {"hotel": "chrome", "guide": "safari17_0"}
JSON_TIMEOUT = 30
PAGE_TIMEOUT = 60       # things-to-do pages are up to ~3.5 MB

HOTEL_LIST_PAGE_ID = "10320668148"
HOTEL_DETAIL_PAGE_ID = "10320668147"
FLIGHT_CLIENT_ID = "09034065311050863213"   # the web client's own constant


class TripComUpstreamError(UpstreamError):
    """Transport failure or 5xx — retryable."""


class TripComBlocked(TripComUpstreamError, Blocked):
    """403 / 429 / 430 / 432 or a non-JSON body where JSON was expected — retryable."""


class TripComBadRequest(BadRequest):
    """Upstream 400 or a body the service rejected — never retried."""


class TripComNotFound(NotFound):
    """Hotel / POI / product / city code does not exist — never retried."""


# One session per (thread, profile): a curl handle must not be shared across
# threads, and the two TLS profiles need two handles.
_local = threading.local()


def _sessions():
    sessions = getattr(_local, "sessions", None)
    if sessions is None:
        sessions = _local.sessions = {}
    return sessions


def _session(kind="hotel"):
    sess = _sessions().get(kind)
    if sess is None:
        from curl_cffi import requests as curl_requests
        sess = curl_requests.Session(impersonate=PROFILES[kind])
        proxy = config.trip_com_proxy()
        if proxy:
            sess.proxies = {"http": proxy, "https": proxy}
        sess.headers.update({"accept-language": egress.accept_language(proxy)})
        _sessions()[kind] = sess
    return sess


def _drop_session(kind):
    """Close the thread's session so the retry rides a fresh exit."""
    sess = _sessions().pop(kind, None)
    if sess is not None:
        try:
            sess.close()
        except Exception:
            pass


def _retrying(fn, kind="hotel"):
    """Run fn() under the shared retry policy (TripComUpstreamError only;
    NotFound / BadRequest surface immediately)."""
    last = None
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            return fn()
        except TripComUpstreamError as e:
            last = e
            _drop_session(kind)
            if attempt < config.MAX_RETRIES:
                time.sleep(config.RETRY_BACKOFF * attempt)
    raise last


def _classify(resp, label):
    """Map an HTTP status to the error taxonomy; return the body text."""
    status = resp.status_code
    if status == 404:
        raise TripComNotFound(f"{label} not found")
    if status == 400:
        raise TripComBadRequest(f"Trip.com rejected {label}: {resp.text[:160]}")
    if status in (401, 403, 429, 430, 432):
        raise TripComBlocked(f"HTTP {status} for {label}: {resp.text[:80]}")
    if status != 200:
        raise TripComUpstreamError(f"HTTP {status} for {label}")
    return resp.text


# ---- the SOA JSON transport --------------------------------------------------------

def _head(locale, currency, page_id):
    """The `head` block every IBU hotel service expects (anonymous web client)."""
    return {"platform": "PC", "cver": "0", "cid": "", "bu": "IBU", "group": "trip", "aid": "", "sid": "",
            "ouid": "", "locale": locale, "timezone": "0", "currency": currency, "pageId": page_id,
            "vid": "", "guid": "", "isSSR": False}


def _json_headers(locale, currency):
    return {"content-type": "application/json", "accept": "application/json",
            "locale": locale, "currency": currency,
            "x-ctx-locale": locale, "x-ctx-currency": currency}


def soa_post(service, operation, body, *, locale="en-US", currency="USD", kind="hotel"):
    """POST one SOA operation; returns the parsed JSON document."""
    url = f"{SITE}/restapi/soa2/{service}/{operation}"
    label = f"{service}/{operation}"

    def once():
        try:
            resp = _session(kind).post(url, json=body, headers=_json_headers(locale, currency),
                                       timeout=JSON_TIMEOUT)
        except Exception as e:
            raise TripComUpstreamError(f"request failed: {type(e).__name__}: {e}")
        text = _classify(resp, label)
        try:
            return resp.json()
        except Exception:
            raise TripComBlocked(f"non-JSON body for {label}: {text[:80]}")
    return _retrying(once, kind)


def _get_page(path, params, label, kind="hotel"):
    """GET one HTML page; returns (text, final_url)."""
    url = SITE + path + ("?" + urlencode(params) if params else "")

    def once():
        try:
            resp = _session(kind).get(url, timeout=PAGE_TIMEOUT, allow_redirects=True)
        except Exception as e:
            raise TripComUpstreamError(f"request failed: {type(e).__name__}: {e}")
        text = _classify(resp, label)
        if len(text) < 1000:
            raise TripComBlocked(f"stub body ({len(text)} bytes) for {label}")
        return text, str(resp.url)
    return _retrying(once, kind)


def _compact(date_iso):
    """2026-12-10 -> 20261210 (the hotel services' date form)."""
    return date_iso.replace("-", "")


# ---- destinations ------------------------------------------------------------------

def fetch_destinations(keyword, locale="en-US"):
    return soa_post(18416, "getTripGlobalHotelSearch",
                    {"keyword": keyword, "locale": locale,
                     "head": _head(locale, "USD", HOTEL_LIST_PAGE_ID)}, locale=locale)


# ---- hotel list --------------------------------------------------------------------

def hotel_list_body(city_id, check_in, check_out, *, adults=2, children_ages=(), rooms=1,
                    filters=(), keyword="", page=1, page_size=20, locale="en-US", currency="USD"):
    """The fetchMetaList request, as the hotel list page builds it. `filters`
    are extra {type, value, filterId} items (sort / stars / price ...); the
    occupancy (29) and price-mode (80) filters are always present. A
    `keyword` becomes the type-30 filter the autocomplete hands out for a
    hotel / landmark term."""
    occupancy = "|".join(["1", str(adults), *[str(a) for a in children_ages]])
    items = [{"type": "80", "title": "Price per room per night (excl. taxes & fees)", "value": "0",
              "filterId": "80|0|1"},
             {"filterId": "29|1", "type": "29", "value": occupancy}]
    items.extend(filters)
    if keyword:
        items.append({"type": "30", "title": keyword, "value": keyword, "filterId": f"30|{keyword}"})
    return {
        "date": {"dateType": 1, "dateInfo": {"checkInDate": _compact(check_in), "checkOutDate": _compact(check_out)}},
        "extension": [{"name": "nationality", "value": ""}, {"name": "NewLogoTripOnlineFirstImageList", "value": "T"},
                      {"name": "EnableProductId", "value": "T"}],
        "destination": {"type": 1, "geo": {"cityId": int(city_id)}, "keyword": {"word": ""}},
        "extraFilter": {"childInfoItems": [{"age": int(a)} for a in children_ages],
                        "ctripMainLandBDCoordinate": True, "sessionId": uuid.uuid4().hex,
                        "extendableParams": {"tripWalkDriveSwitch": "T"}},
        "filters": items,
        "roomQuantity": int(rooms),
        "marketInfo": {},
        "paging": {"pageIndex": int(page), "pageSize": int(page_size), "pageCode": HOTEL_LIST_PAGE_ID},
        "hotelIdFilter": {"hotelAldyShown": []},
        "head": {**_head(locale, currency, HOTEL_LIST_PAGE_ID),
                 "extension": [{"name": "cityId", "value": str(city_id)},
                               {"name": "checkIn", "value": _compact(check_in)},
                               {"name": "checkOut", "value": _compact(check_out)}]},
    }


def fetch_hotel_list(city_id, check_in, check_out, **kw):
    locale = kw.get("locale", "en-US")
    currency = kw.get("currency", "USD")
    body = hotel_list_body(city_id, check_in, check_out, **kw)
    payload = soa_post(34951, "fetchMetaList", body, locale=locale, currency=currency)
    if not isinstance(payload.get("data"), dict):
        raise TripComUpstreamError("fetchMetaList answered without data")
    return payload


# ---- hotel page + extras -----------------------------------------------------------

def fetch_hotel_page(hotel_id, check_in, check_out, *, adults=2, children_ages=(), rooms=1,
                     currency="USD", locale="en-US"):
    """The server-rendered hotel page (RSC stream). Unknown ids come back as
    a 200 whose only payload is a redirect to /hotels/pages/404."""
    params = {"hotelId": hotel_id, "checkin": check_in, "checkout": check_out, "adult": adults,
              "children": len(children_ages), "crn": rooms, "curr": currency, "locale": locale}
    if children_ages:
        params["ages"] = ",".join(str(a) for a in children_ages)
    text, _ = _get_page("/hotels/detail/", params, f"hotel {hotel_id}")
    if "hotelDetailResponse" not in text:
        if "/hotels/pages/404" in text:
            raise TripComNotFound(f"hotel {hotel_id} not found")
        raise TripComBlocked(f"hotel page {hotel_id} rendered without its data")
    return text


def _hotel_extra_body(hotel_id, check_in, check_out, adults, children, rooms, locale, currency):
    return {"hotelId": int(hotel_id), "checkIn": _compact(check_in), "checkOut": _compact(check_out),
            "adult": int(adults), "child": int(children), "roomQuantity": int(rooms),
            "head": _head(locale, currency, HOTEL_DETAIL_PAGE_ID)}


def fetch_hotel_seo(hotel_id, check_in, check_out, *, adults=2, children=0, rooms=1,
                    locale="en-US", currency="USD"):
    """28820/ctgetHotelDetailSEO: about-this-property Q/A, FAQ, room types."""
    payload = soa_post(28820, "ctgetHotelDetailSEO",
                       _hotel_extra_body(hotel_id, check_in, check_out, adults, children, rooms, locale, currency),
                       locale=locale, currency=currency)
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("errors"):
        raise TripComNotFound(f"hotel {hotel_id} not found")
    return payload


def fetch_hotel_album(hotel_id, check_in, check_out, *, adults=2, children=0, rooms=1,
                      locale="en-US", currency="USD"):
    """28820/ctgethotelalbum: every hotel-provided and guest photo by category."""
    return soa_post(28820, "ctgethotelalbum",
                    _hotel_extra_body(hotel_id, check_in, check_out, adults, children, rooms, locale, currency),
                    locale=locale, currency=currency)


# ---- reviews -----------------------------------------------------------------------

def fetch_hotel_reviews(hotel_id, *, page=1, page_size=10, order_by="0", travel_types=(),
                        repeat_comment=1, check_in, check_out, locale="en-US", currency="USD"):
    body = {
        "hotelId": int(hotel_id),
        "commentFilterOptions": {"pageIndex": int(page), "pageSize": int(page_size),
                                 "repeatComment": int(repeat_comment), "orderBy": str(order_by),
                                 "travelTypes": list(travel_types)},
        "sceneTypes": ["CommentList"],
        "sceneParams": {"checkIn": _compact(check_in), "checkOut": _compact(check_out), "adult": 2, "child": 0,
                        "childrenAgeList": [], "roomQuantity": 1, "location": {"geo": {"cityID": 0}},
                        "extra": {"useHotPoiQuery": True}, "filterInfoList": []},
        "head": _head(locale, currency, HOTEL_DETAIL_PAGE_ID),
    }
    payload = soa_post(34308, "getHotelCommentInfo", body, locale=locale, currency=currency)
    if not isinstance(payload.get("data"), dict):
        raise TripComUpstreamError("getHotelCommentInfo answered without data")
    return payload


# ---- things to do -------------------------------------------------------------------

def fetch_attractions_page(keyword, *, currency="USD", locale="en-US"):
    """The things-to-do search page for a destination or phrase (12 cards)."""
    text, _ = _get_page("/things-to-do/list", {"keyword": keyword, "curr": currency, "locale": locale},
                        f"things to do {keyword!r}")
    return text


def fetch_attraction_page(poi_id, *, currency="USD", locale="en-US"):
    """The travel-guide POI page (Safari TLS); the slug is irrelevant, the
    site redirects to the canonical one. Unknown ids are a real 404."""
    text, _ = _get_page(f"/travel-guide/attraction/x/x-{int(poi_id)}/", {"curr": currency, "locale": locale},
                        f"attraction {poi_id}", kind="guide")
    return text


def fetch_experiences_page(city_slug, *, currency="USD", locale="en-US"):
    text, _ = _get_page(f"/things-to-do/experiences/{quote(city_slug)}/", {"curr": currency, "locale": locale},
                        f"experiences {city_slug!r}")
    return text


def fetch_experience_page(product_id, *, currency="USD", locale="en-US"):
    """The product page; an unknown id redirects to a city /experiences/ page."""
    text, final_url = _get_page(f"/things-to-do/detail/{int(product_id)}", {"curr": currency, "locale": locale},
                                f"experience {product_id}")
    if f"/things-to-do/detail/{int(product_id)}" not in final_url:
        raise TripComNotFound(f"experience {product_id} not found")
    return text


# ---- flights -----------------------------------------------------------------------

def _flight_head(locale, currency):
    return {"Locale": locale, "Currency": currency, "Platform": "PC", "ClientID": FLIGHT_CLIENT_ID,
            "Language": locale.split("-")[0], "Source": "ONLINE"}


def fetch_airports(key, *, locale="en-US", currency="USD"):
    return soa_post(14427, "PoiSearch",
                    {"Head": _flight_head(locale, currency), "key": key, "mode": 0, "source": "ONLINE", "searchType": 1},
                    locale=locale, currency=currency)


def fetch_fare_day(origin, destination, date, *, locale="en-US", currency="USD"):
    """The lowest one-way fare for one departure day (14427/GetLowPriceInCalender).
    The service requires FlightWay + searchInfo but answers the same fare
    whatever they say (cabin, passengers, OW/RT all tested 2026-11-10), so the
    body sends the web client's one-way, one-adult, economy defaults."""
    body = {"Head": _flight_head(locale, currency), "DDate": date, "ADate": "",
            "DCity": origin, "ACity": destination, "FlightWay": "OW", "Source": "ONLINE",
            "searchInfo": {"adult": 1, "child": 0, "infant": 0, "cabinClass": "Y"}}
    payload = soa_post(14427, "GetLowPriceInCalender", body, locale=locale, currency=currency)
    code = str((payload.get("responseHead") or {}).get("errorCode") or "0")
    if code == "10000001":
        # "no data": an unknown city code, but also a served pair on a day
        # with no fare — the caller validated the codes, so this is an
        # unpriced day (parse_fare_day returns price None)
        return payload
    if code == "99999999":     # a route the fare service does not cover (mainland-China domestic pairs)
        raise TripComNotFound(f"Trip.com publishes no fare calendar for {origin} -> {destination}")
    if code != "0":
        message = (payload.get("responseHead") or {}).get("errorMessage") or code
        raise TripComBadRequest(f"fare calendar: {message}")
    return payload
