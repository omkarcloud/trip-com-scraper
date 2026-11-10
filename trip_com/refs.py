"""Reference resolvers + the filter vocabularies for /trip-com/*.

ONE param per input (tripadvisor QueryOrIdField convention): every id
param also accepts the trip.com link of the same record. Resolvers raise
ValueError with a user-facing message (RefField turns it into a 400).

Record identities on the site (2026-11-07):
  * hotel        hotelId   /hotels/<city>-hotel-detail-<id>/<slug>/
                           /hotels/detail/?hotelId=<id>
  * hotel city   cityId    /hotels/<slug>-hotels-list-<id>/  or  /hotels/list?city=<id>
                           (the id 18416/getTripGlobalHotelSearch hands out)
  * attraction   poiId     /travel-guide/attraction/<district>/<slug>-<id>/
  * experience   productId /things-to-do/detail/<id>
  * experiences  city slug /things-to-do/experiences/<slug>/  (tokyo, paris, newyork, hongkong ...)
  * flight place 3-letter city code (TYO) or airport code (NRT)

The filter tokens are the hotel list's own vocabulary
(34951/getHotelCommonFilter, captured 2026-11-07): "<type>|<value>".
"""
import re
from urllib.parse import parse_qs, urlparse

SITE = "https://www.trip.com"

_HOTEL_DETAIL_RE = re.compile(r"hotel-detail-(\d+)")
_HOTEL_LIST_RE = re.compile(r"hotels-list-(\d+)")
_ATTRACTION_RE = re.compile(r"/travel-guide/attraction/[^/?#]+/[^/?#]*?-(\d+)/?(?:[?#]|$)")
_EXPERIENCE_RE = re.compile(r"/things-to-do/detail/(\d+)")
_EXPERIENCES_RE = re.compile(r"/things-to-do/experiences/([A-Za-z0-9-]+)")
_SLUG_RE = re.compile(r"^[a-z0-9-]{2,60}$")
_CODE_RE = re.compile(r"^[A-Z]{3}$")


def _is_link(value):
    return value.startswith(("http://", "https://", "//"))


def _parsed(value):
    url = value if not value.startswith("//") else "https:" + value
    parts = urlparse(url)
    host = (parts.hostname or "").lower()
    if not (host == "trip.com" or host.endswith(".trip.com")):
        raise ValueError("Must be a trip.com link.")
    return parts


def _query_int(parts, *names):
    query = parse_qs(parts.query)
    for name in names:
        for key, values in query.items():
            if key.lower() == name.lower() and values and values[0].isdigit():
                return int(values[0])
    return None


# ---- resolvers -------------------------------------------------------------------

def resolve_hotel(value):
    """Bare hotel id or a trip.com hotel link -> int hotel id."""
    value = (value or "").strip()
    if value.isdigit():
        return int(value)
    if _is_link(value):
        parts = _parsed(value)
        match = _HOTEL_DETAIL_RE.search(parts.path)
        if match:
            return int(match.group(1))
        hotel_id = _query_int(parts, "hotelId", "hotelid")
        if hotel_id:
            return hotel_id
        raise ValueError("Must be a trip.com hotel link "
                         "(https://www.trip.com/hotels/<city>-hotel-detail-<id>/<name>/).")
    raise ValueError("Must be a Trip.com hotel id (346403) or a trip.com hotel link.")


def resolve_destination(value):
    """A hotel city id, a trip.com hotel list link, or a place name ->
    {"city_id": int} | {"name": str}. A name is resolved through the
    autocomplete at request time (api.py), so this stays offline."""
    value = (value or "").strip()
    if value.isdigit():
        return {"city_id": int(value)}
    if _is_link(value):
        parts = _parsed(value)
        match = _HOTEL_LIST_RE.search(parts.path)
        if match:
            return {"city_id": int(match.group(1))}
        city_id = _query_int(parts, "city", "cityId")
        if city_id:
            return {"city_id": city_id}
        raise ValueError("Must be a trip.com hotel list link "
                         "(https://www.trip.com/hotels/<city>-hotels-list-<id>/).")
    if len(value) > 100:
        raise ValueError("Place name too long (100 characters max).")
    return {"name": value}


def resolve_attraction(value):
    """Bare POI id or a trip.com travel-guide attraction link -> int POI id."""
    value = (value or "").strip()
    if value.isdigit():
        return int(value)
    if _is_link(value):
        parts = _parsed(value)
        match = _ATTRACTION_RE.search(parts.path)
        if match:
            return int(match.group(1))
        raise ValueError("Must be a trip.com attraction link "
                         "(https://www.trip.com/travel-guide/attraction/<city>/<name>-<id>/).")
    raise ValueError("Must be a Trip.com attraction id (88070) or a trip.com attraction link.")


def resolve_experience(value):
    """Bare product id or a trip.com things-to-do product link -> int id."""
    value = (value or "").strip()
    if value.isdigit():
        return int(value)
    if _is_link(value):
        parts = _parsed(value)
        match = _EXPERIENCE_RE.search(parts.path)
        if match:
            return int(match.group(1))
        raise ValueError("Must be a trip.com experience link "
                         "(https://www.trip.com/things-to-do/detail/<id>).")
    raise ValueError("Must be a Trip.com experience id (100444859) or a trip.com things-to-do link.")


def resolve_experiences_destination(value):
    """A things-to-do city slug ("tokyo", "newyork") or the experiences
    page link -> the slug."""
    value = (value or "").strip()
    if _is_link(value):
        parts = _parsed(value)
        match = _EXPERIENCES_RE.search(parts.path)
        if match:
            return match.group(1).lower()
        raise ValueError("Must be a trip.com experiences link "
                         "(https://www.trip.com/things-to-do/experiences/<city>/).")
    slug = value.lower().replace(" ", "")
    if not _SLUG_RE.match(slug):
        raise ValueError("Must be a Trip.com city slug (tokyo, paris, newyork) or an experiences link.")
    return slug


def resolve_place_code(value):
    """A 3-letter IATA city code (TYO) or airport code (NRT), upper-cased."""
    value = (value or "").strip().upper()
    if not _CODE_RE.match(value):
        raise ValueError("Must be a 3-letter IATA city or airport code (TYO, NRT, LON).")
    return value


def resolve_children_ages(value):
    """'3,7' -> [3, 7]; ages 0-17, at most 6 children."""
    ages = []
    for raw in (value or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        if not raw.isdigit() or not 0 <= int(raw) <= 17:
            raise ValueError("Children ages must be whole numbers 0-17, comma separated (e.g. 3,7).")
        ages.append(int(raw))
    if len(ages) > 6:
        raise ValueError("At most 6 children.")
    return ages


# Trip.com locales carry a region (en-US, ja-JP); a bare language maps to its
# usual storefront region.
DEFAULT_REGION = {
    "en": "US", "ja": "JP", "ko": "KR", "zh": "CN", "de": "DE", "fr": "FR", "es": "ES", "it": "IT",
    "pt": "BR", "ru": "RU", "th": "TH", "id": "ID", "ms": "MY", "vi": "VN", "nl": "NL", "tr": "TR",
    "pl": "PL", "ar": "AE", "el": "GR", "da": "DK", "sv": "SE", "fi": "FI", "no": "NO", "uk": "UA",
    "kk": "KZ", "tl": "PH",
}


def normalize_locale(tag):
    """'en' -> 'en-US', 'ja-jp' -> 'ja-JP'."""
    lang, _, region = (tag or "en").replace("_", "-").partition("-")
    lang = lang.lower()
    region = (region or DEFAULT_REGION.get(lang, lang)).upper()
    return f"{lang}-{region}"


# ---- links -------------------------------------------------------------------------

def hotel_link(hotel_id):
    return f"{SITE}/hotels/detail/?hotelId={hotel_id}"


def hotel_list_link(city_id):
    return f"{SITE}/hotels/list?city={city_id}"


def attraction_link(poi_id, url=None):
    """The canonical guide link when the payload carries one (query stripped)."""
    if url:
        return url.split("?")[0]
    return f"{SITE}/travel-guide/attraction/x/x-{poi_id}/"


def experience_link(product_id):
    return f"{SITE}/things-to-do/detail/{product_id}"


def experiences_link(slug):
    return f"{SITE}/things-to-do/experiences/{slug}/"


# ---- hotel list vocabulary (34951/getHotelCommonFilter, 2026-11-07) -------------------

# Public value -> the site's "<type>|<value>" filter token. Plain literals on
# purpose: the publishing pipeline reads these tables as the params' enums.
HOTEL_SORTS = {
    "recommended": "17|1",
    "price_low_to_high": "17|3",
    "price_high_to_low": "17|4",
    "top_reviewed": "17|6",
    "stars_high_to_low": "17|14",
    "distance": "17|5",
    "distance_by_road": "17|12",
}
STAR_CHOICES = ("2", "3", "4", "5")          # "2" = 2 stars and below (the site's own bucket)
MIN_SCORES = {"6": "6|7", "7": "6|8", "8": "6|9", "9": "6|10"}
PAYMENTS = {"pay_at_hotel": "7|1", "prepay": "7|2", "no_card_needed": "7|6"}
BED_TYPES = {
    "double": "4|1", "two_beds": "4|2", "king": "4|3", "single": "4|4",
    "four_or_more_beds": "4|5", "three_beds": "4|6",
}
FREE_CANCELLATION = "23|10"
BREAKFAST_INCLUDED = "5|1"


def hotel_filter(token):
    """'17|3' -> the fetchMetaList filter item {type, value, filterId}."""
    type_, _, value = token.partition("|")
    return {"type": type_, "value": value, "filterId": token}


def star_filter(star):
    return hotel_filter(f"16|{star}")


def price_filter(min_price, max_price):
    low = int(min_price or 0)
    high = int(max_price) if max_price else ""
    return {"type": "15", "value": f"{low}|{high}", "filterId": "15|Range"}


# ---- reviews -------------------------------------------------------------------------

REVIEW_SORTS = {"relevance": "0", "recent": "1", "highest_rating": "2", "lowest_rating": "3"}
TRAVELER_TYPES = {"business": 10, "family": 30, "friends": 40, "solo": 50, "booked_for_others": 60, "couple": 70}
TRAVELER_TYPE_NAMES = {v: k for k, v in TRAVELER_TYPES.items()}

# ---- flights -------------------------------------------------------------------------

# PoiSearch dataType: 2 = state / province (children are its cities), 3 = city,
# 4 = a place without an airport (children are the nearest airports),
# 5 = airport, 7 = "<city> and nearby cities"
PLACE_KINDS = {2: "region", 3: "city", 5: "airport", 7: "city_group"}
MAX_CALENDAR_DAYS = 31
