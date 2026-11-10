"""Trip.com payloads -> clean dicts. Pure functions, no I/O.

Fixtures (trip_com/fixtures/, captured 2026-11-07 from www.trip.com with
curl_cffi; en-US / USD):
  destinations_*.json      18416/getTripGlobalHotelSearch (city hit; a hotel-name
                           term that carries a type-30 keyword filter; a no-match
                           term = the default popular-city list)
  hotels_search_*.json     34951/fetchMetaList (Shanghai page 1 with ad rows; a
                           2-row hotel-name search; Las Vegas, where most rooms
                           add a resort fee due at the hotel; an unknown city
                           = empty) — Las Vegas captured 2026-11-10
  hotel_page_*.html        /hotels/detail/ RSC pages (Grand Hyatt Shanghai; a
                           5-dollar hostel; an unknown id = 404 redirect script)
  hotel_seo_*.json         28820/ctgetHotelDetailSEO (same three)
  hotel_album_*.json       28820/ctgethotelalbum (same three)
  hotel_reviews_*.json     34308/getHotelCommentInfo (18,928 reviews; 51; unknown)
  airports_*.json          14427/PoiSearch (tokyo; lond; New York with its state
                           row, 2026-11-10; a miss)
  fare_calendar_*.json     14427/GetLowPriceInCalender (SHA-TYO; an unknown code)
  experiences_*.json       /things-to-do/experiences/<slug>/ __NEXT_DATA__
                           (tokyo 40 products; paris; newyork, where half the
                           cards are attractions; an unknown slug)
  experience_*.json        /things-to-do/detail/<id> __NEXT_DATA__ (a cafe
                           ticket with 8 packages; a day tour; the LA tour whose
                           5 packages repeat every option, 2026-11-10)
  attractions_*.json       /things-to-do/list?keyword= __NEXT_DATA__ (Tokyo;
                           "museum"; New York, 2026-11-10; an unknown
                           destination = empty)
  attraction_*.json        /travel-guide/attraction/.../<id>/ __NEXT_DATA__
"""
import html as htmllib
import json
import re
from datetime import datetime, timezone

from trip_com.refs import (TRAVELER_TYPE_NAMES, PLACE_KINDS, attraction_link, experience_link,
                           experiences_link, hotel_link, hotel_list_link)

_RSC_CHUNK_RE = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')
_RSC_ROW_RE = re.compile(r"^([0-9a-zA-Z]+):(.*)$", re.S)
_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_BREAK_RE = re.compile(r"</p>|<br\s*/?>|</li>|</h\d>", re.I)
_MARKUP_RE = re.compile(r"\{/?\d+\}")          # {1}…{/1} highlight markers
_NUM_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_COUNT_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*([kKmM])?")
_LOCAL_NAME_RE = re.compile(r":\s*(.+)$")
_TODAY_OPEN_RE = re.compile(r"^Today\s*Open\s*:?\s*")


# ---- value helpers -------------------------------------------------------------------

def _g(obj, *keys, default=None):
    """Nested .get() that survives None and lists."""
    cur = obj
    for key in keys:
        if isinstance(cur, dict):
            cur = cur.get(key)
        elif isinstance(cur, list) and isinstance(key, int) and -len(cur) <= key < len(cur):
            cur = cur[key]
        else:
            return default
        if cur is None:
            return default
    return cur


def _text(value):
    if value is None or isinstance(value, (dict, list)):
        return None
    text = " ".join(str(value).split())
    return text or None


def _int(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None


def _float(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _count(value):
    """'3,411 reviews' -> 3411, '1.8k reviews' -> 1800, '228k+ booked' -> 228000."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    match = _COUNT_RE.search(str(value or ""))
    if not match:
        return None
    number = float(match.group(1).replace(",", ""))
    scale = {"k": 1000, "m": 1000000}.get((match.group(2) or "").lower(), 1)
    return int(round(number * scale))


def _amount(value):
    """'-$168.57' -> -168.57, 'US$53.34' -> 53.34, '' -> None."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value or "").strip()
    match = _NUM_RE.search(text)
    if not match:
        return None
    amount = abs(float(match.group(0).replace(",", "")))
    return -amount if text.startswith("-") else amount


def _date8(value):
    """'20261210' -> '2026-12-10'."""
    text = _text(value)
    if text and len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def _iso(value):
    """'2026-10-09 16:42:41' -> '2026-10-09T16:42:41'; a bare date stays a date."""
    text = _text(value)
    if not text:
        return None
    if len(text) >= 19 and text[10] == " ":
        if text[11:19] == "00:00:00":
            return text[:10]
        return text[:10] + "T" + text[11:19]
    return text


def _day(value):
    text = _text(value)
    return text[:10] if text else None


def _epoch_day(seconds):
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
        return None
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%d")


def _strip_html(value):
    text = str(value or "")
    text = _BREAK_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = htmllib.unescape(text)
    lines = [" ".join(line.split()) for line in text.split("\n")]
    text = "\n".join(line for line in lines if line)
    return text or None


def _markup(value):
    text = _text(value)
    return _MARKUP_RE.sub("", text) if text else None


def _clean_link(url):
    text = _text(url)
    if not text:
        return None
    if text.startswith("//"):
        text = "https:" + text
    return text.split("?")[0]


def _titles(items, key="tagTitle"):
    out = []
    for item in items or []:
        title = _text(item.get(key)) if isinstance(item, dict) else _text(item)
        if title and title not in out:
            out.append(title)
    return out


def _coordinate(node):
    """{latitude, longitude, system}. Hotel rows name the datum in
    `gcoordType` (their `coordinateType` is a bare 1/2/3 code); POI rows put
    the name itself in `coordinateType`."""
    lat, lng = _float(_g(node, "latitude")), _float(_g(node, "longitude"))
    if lat is None or lng is None:
        return None
    system = _text(_g(node, "gcoordType"))
    if not system:
        ctype = _g(node, "coordinateType")
        system = _text(ctype) if isinstance(ctype, str) and not ctype.isdigit() else None
    return {"latitude": lat, "longitude": lng, "system": system}


def _best_coordinate(entries):
    """Prefer the WGS84 entry of a hotel's mapCoordinate list (the China
    rows also carry BD09 / GCJ02 copies)."""
    entries = [e for e in entries or [] if isinstance(e, dict)]
    for entry in entries:
        if (entry.get("gcoordType") or "").upper() == "WGS84":
            return _coordinate(entry)
    return _coordinate(entries[0]) if entries else None


def _split_bar(text):
    return [part.strip() for part in str(text or "").split("|") if part.strip()]


def _snake(name):
    """'serviceAnimal' -> 'service_animal'."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", str(name)).lower()


# hotelStar.starType -> the same words hotels/details uses (starInfo.type).
# Checked 2026-11-10: starType 1 hotels (Renaissance Pudong, Bellagio) open
# a detail page with type "star" and a star icon, starType 0 hotels (MaxX by
# Steigenberger, Hyakumangoku Onsen) one with type "diamond" (Trip.com's own
# rating, "not an official ranking"); the site's bundles draw a star only for
# starType 1. starType 2 rows are apartments / homestays, which the list's
# legend calls the "Homes & Apartments Rating". (The legend's own starType
# numbers for star / diamond are swapped relative to hotelStar.)
_STAR_TYPES = {0: "diamond", 1: "star", 2: "homes_and_apartments"}


# ---- destinations ------------------------------------------------------------------

def parse_destinations(payload):
    """18416/getTripGlobalHotelSearch -> places with their hotel city id. A
    hotel-name / landmark term comes back as its city plus the `keyword`
    Trip.com would filter the list with (pass it as hotels/search `query`)."""
    out = []
    for row in payload.get("responseList") or []:
        if not isinstance(row, dict) or _int(row.get("id")) is None:
            continue
        if "timeZone" not in row:
            # a term with no match answers a fixed list of popular cities
            # (Singapore, New York, Bangkok ...) that carry no timeZone key
            continue
        name = _text(row.get("name")) or ""
        country = None
        if "-" in name:
            country, name = (part.strip() for part in name.split("-", 1))
        offset = _int(row.get("timeZone"))
        out.append({
            "id": _int(row.get("id")),
            "type": (_text(row.get("type")) or "").lower() or None,
            "name": name or None,
            "country": country,
            "country_id": _int(row.get("countryId")),
            "timezone_offset_seconds": offset if offset is not None and offset != -1 else None,   # -1 = unknown
            "keyword": _text(row.get("filtername")),
            "link": hotel_list_link(_int(row.get("id"))),
        })
    return out


# ---- hotel list ----------------------------------------------------------------------

def _price_layer(layer):
    """The card's "Price Details" layer. `payInfo.total` is what is paid at
    booking; US resorts add `payAtHotel` (resort fees due at the hotel), and
    then the layer's own `total` is the grand total of both."""
    pay = _g(layer, "payInfo") or {}
    promo = pay.get("promotion") or {}
    at_hotel = layer.get("payAtHotel") or {}
    pay_now = _amount(_g(pay, "total", "content"))
    grand_total = _amount(_g(layer, "total", "content"))
    return {
        "total": grand_total if grand_total is not None else pay_now,
        "pay_now": pay_now,
        "due_at_hotel": _amount(_g(at_hotel, "total", "content")),
        "due_at_hotel_items": [{"name": _text(i.get("title")), "amount": _amount(i.get("content"))}
                               for i in at_hotel.get("items") or [] if isinstance(i, dict) and _text(i.get("title"))],
        "room_subtotal": _amount(_g(pay, "roomQuantityDays", "content")),
        "taxes_and_fees": _amount(_g(pay, "payTax", "content")),
        "discounts": _amount(promo.get("content")),
        "discount_items": [{"name": _text(i.get("title")), "amount": _amount(i.get("content"))}
                           for i in promo.get("items") or [] if isinstance(i, dict)],
    }


def _lines(value):
    """'Total price: $303\\n1 room × 2 nights' -> 'Total price: $303 · 1 room × 2 nights'."""
    parts = [_text(part) for part in str(value or "").split("\n")]
    return " · ".join(part for part in parts if part) or None


def _room_card(room):
    if not isinstance(room, dict):
        return None
    price = room.get("priceInfo") or {}
    tags = room.get("roomTags") or {}
    layer = _price_layer(room.get("priceInfoLayer")) if room.get("priceInfoLayer") else {}
    return {
        "id": _int(_g(room, "summary", "roomId")),
        "name": _text(_g(room, "summary", "physicsName")) or _text(_g(room, "summary", "saleRoomName")),
        "beds": [b for b in (_g(room, "bedInfo", "contentList") or []) if _text(b)],
        "max_adults": _int(_g(room, "guestInfo", "adultCount")),
        "max_children": _int(_g(room, "guestInfo", "childCount")),
        "price": {
            "amount": _float(price.get("price")),
            "currency": _text(price.get("currency")),
            "original_amount": _float(price.get("deletePrice")) or None,
            "display": _text(price.get("displayPrice")),
            "total": layer.get("total"),
            "pay_now": layer.get("pay_now"),
            "due_at_hotel": layer.get("due_at_hotel"),
            "due_at_hotel_items": layer.get("due_at_hotel_items") or [],
            "room_subtotal": layer.get("room_subtotal"),
            "taxes_and_fees": layer.get("taxes_and_fees"),
            "discounts": layer.get("discounts"),
            "discount_items": layer.get("discount_items") or [],
            "explanation": _lines(price.get("priceExplanation")),
        },
        "payment_type": {1: "prepay", 2: "pay_at_hotel"}.get(_g(room, "payInfo", "payType")),
        "cancellation": next(iter(_titles(tags.get("advantageTags"))), None),
        "tags": {
            "advantages": _titles(tags.get("advantageTags")),
            "promotions": _titles(tags.get("promotionTags")),
            "discounts": _titles(tags.get("discountTags")),
            "urgency": _titles(tags.get("encourageTags")),
            "highlights": _titles(tags.get("highlightTags")),
        },
    }


def _hotel_card(row):
    info = row.get("hotelInfo") or {}
    summary = info.get("summary") or {}
    hotel_id = _int(summary.get("hotelId"))
    if hotel_id is None:
        return None
    comment = info.get("commentInfo") or {}
    position = info.get("positionInfo") or {}
    images = []
    # the cover is the first gallery photo again, and the gallery repeats
    # each picture as a "…jpg_.webp" variant: keep one jpg per picture
    for url in [_g(info, "hotelImages", "url")] + [_g(img, "url") for img in _g(info, "hotelImages", "multiImgs") or []]:
        url = _text(url)
        if url and url.endswith("_.webp"):
            url = url[:-len("_.webp")]
        if url and url not in images:
            images.append(url)
    room = _room_card((row.get("roomInfo") or [None])[0])
    highlight = _titles(comment.get("oneSentenceComment"))
    return {
        "id": hotel_id,
        "name": _text(_g(info, "nameInfo", "name")),
        "english_name": _text(_g(info, "nameInfo", "enName")),
        "link": hotel_link(hotel_id),
        "star": _int(_g(info, "hotelStar", "star")),
        "star_type": _STAR_TYPES.get(_int(_g(info, "hotelStar", "starType"))),
        "category": _text(_g(info, "hotelCategory", "categoryName")),
        "rating": {
            "score": _float(comment.get("commentScore")),
            "max": _float(comment.get("fullRating")) or 10.0,
            "label": _text(comment.get("commentDescription")),
            "review_count": _count(comment.get("commenterNumber")),
            "subscores": {(_text(s.get("content")) or "").lower().replace(" ", "_"): _float(s.get("number"))
                          for s in comment.get("subScore") or [] if isinstance(s, dict) and _text(s.get("content"))},
            "summary_sentence": _text(comment.get("ugcSentence")),
            "highlights": [h for title in highlight for h in _split_bar(title)],
        },
        # positionInfo.address is not a street address on the list: it
        # repeats the zone name or is empty (54 + 13 of 67 rows, 2026-11-10),
        # so it is left out; hotels/details carries the real address
        "location": {
            "city_id": _int(position.get("cityId")),
            "city": _text(position.get("cityName")),
            "country_id": _int(position.get("countryId")),
            # a hotel in no named zone gets "Near <landmark>|<landmark>" in
            # zoneNames (3 of 21 Las Vegas cards, 2026-11-10): nearby places,
            # already in `description`, not a zone
            "zone": next((_text(z) for z in position.get("zoneNames") or [] if _text(z) and "|" not in _text(z)), None),
            "description": _text(position.get("positionDesc")),
            "descriptions": [d for d in position.get("positionDescs") or [] if _text(d)],
            "coordinate": _best_coordinate(position.get("mapCoordinate")),
        },
        "images": images,
        "video": _text(_g(info, "hotelVideo", "videoUrl")),
        "highlights": _titles(_g(info, "highlightsModule", "highlightsItemList"), key="title"),
        "tags": _titles(_g(info, "hotelTags", "inboundTags")),
        "is_ad": bool(_g(info, "advertiseInfo", "isAdHotel")),
        "room": room,
    }


def parse_hotel_search(payload):
    """34951/fetchMetaList -> {results, total_count, is_last_page}."""
    data = payload.get("data") or {}
    results = [card for card in (_hotel_card(r) for r in data.get("hotelList") or [] if isinstance(r, dict)) if card]
    extra = data.get("hotelListAddtionInfo") or {}
    return {
        "results": results,
        "total_count": _int(extra.get("hotelTotalCount")),
        "is_last_page": bool(extra.get("isLastPage")) or not results,
    }


# ---- hotel page (RSC) ----------------------------------------------------------------

def extract_rsc_props(html):
    """Decode the Next.js RSC stream of a hotel page and return the props of
    the row that carries `hotelDetailResponse` (None when absent — an
    unknown hotel renders only a redirect to /hotels/pages/404)."""
    chunks = _RSC_CHUNK_RE.findall(html or "")
    if not chunks:
        return None
    try:
        text = "".join(json.loads(chunk) for chunk in chunks)
    except ValueError:
        return None
    for line in text.split("\n"):
        if "hotelDetailResponse" not in line:
            continue
        match = _RSC_ROW_RE.match(line)
        if not match:
            continue
        try:
            payload = json.loads(match.group(2))
        except ValueError:
            continue
        props = _find_key(payload, "hotelDetailResponse")
        if props is not None:
            return props
    return None


def _find_key(node, key):
    """The first dict holding `key` (depth-first)."""
    if isinstance(node, dict):
        if key in node:
            return node
        for value in node.values():
            found = _find_key(value, key)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_key(value, key)
            if found is not None:
                return found
    return None


def _policy_items(block):
    items = []
    for item in _g(block, "content") or []:
        if not isinstance(item, dict):
            continue
        desc = _text(item.get("description"))
        if not desc:
            continue
        items.append({"title": (_text(item.get("title")) or "").rstrip(": ") or None, "description": desc})
    return items


_POLICY_NAMES = {
    "checkInAndOut": "check_in_and_out", "guestLimit": "guests_accepted", "childPolicy": "children",
    "cribAndExtraBed": "cribs_and_extra_beds", "breakfast": "breakfast", "deposit": "deposit", "pet": "pets",
    "ageLimit": "age_requirements", "credit": "paying_at_the_hotel", "serviceAnimal": "service_animals",
}


def _policies(policy_info):
    out = []
    for key, block in (policy_info or {}).items():
        if not isinstance(block, dict) or not _text(block.get("title")):
            continue
        items = _policy_items(block)
        if not items and key != "credit":
            continue
        out.append({"name": _POLICY_NAMES.get(key) or _snake(key), "title": _text(block.get("title")), "items": items})
    return out


def _facility(item):
    fee = _text(item.get("showTitle"))
    details = {}
    for info in item.get("facilityInfo") or []:
        title = _text(_g(info, "title"))
        texts = [t for t in _g(info, "text") or [] if _text(t)]
        if title and texts:
            details[title] = texts
    # showTitle is "Free" or a charge note ("Additional charge"); most items
    # carry neither, and then whether they cost extra is unknown (null)
    is_free = None if not fee else fee.lower() == "free"
    return {"name": _text(item.get("facilityDesc")), "is_free": is_free,
            "fee_note": fee if fee and fee.lower() != "free" else None, "details": details or None}


def _amenities(facility_pop):
    popular = [_facility(i) for i in _g(facility_pop, "hotelPopularFacility", "list") or [] if isinstance(i, dict)]
    groups = []
    for group in facility_pop.get("hotelFacility") or []:
        items = []
        for category in group.get("categoryList") or []:
            items.extend(_facility(i) for i in category.get("list") or [] if isinstance(i, dict))
        if _text(group.get("title")):
            groups.append({"name": _text(group.get("title")), "items": items})
    everything = _titles(facility_pop.get("hotelNormalFacilityList"), key="facilityDesc")
    return {"popular": popular, "groups": groups, "all": everything,
            "total_count": _int(_g(facility_pop, "ubtData", "totalFacilityCount"))}


def _nearby(position):
    out = []
    for poi in _g(position, "placeInfo", "wholePoiInfoList") or []:
        if not isinstance(poi, dict):
            continue
        kind = (_text(poi.get("descWithType")) or "").split(":")[0] if ":" in (poi.get("descWithType") or "") else None
        out.append({"id": _int(poi.get("poiId")), "name": _text(poi.get("poiName")), "type": kind,
                    "distance": _text(poi.get("distance")), "distance_meters": _int(poi.get("walkDriveDistance")),
                    "mode": (_text(poi.get("distType")) or "").lower() or None})
    return out


def _seo_rooms(seo_data):
    rooms = []
    for room in (_g(seo_data, "seoHotelRooms", "physicRoomMap") or {}).values():
        if not isinstance(room, dict):
            continue
        floor = _text(_g(room, "floorInfo", "title"))
        rooms.append({
            "id": _int(room.get("id")),
            "name": _text(room.get("name")),
            "max_adults": _int(room.get("person")),
            "max_children": _int(room.get("children")),
            "bed": _text(_g(room, "bedInfo", "title")),
            "bed_details": [b for b in _g(room, "bedInfo", "hover") or [] if _text(b)],
            "area": _text(_g(room, "areaInfo", "title")),
            "floor": floor.replace("Floor:", "").strip() if floor else None,
            "window": _text(_g(room, "windowInfo", "title")),
            "smoking": _text(_g(room, "smokeInfo", "title")),
            "views": _titles(room.get("encourageTag"), key="title"),
            "amenities": _titles(room.get("baseFacilityInfo"), key="title"),
            "photos": [p.get("url") for p in room.get("pictureInfo") or [] if _text(_g(p, "url"))],
        })
    return rooms


def parse_hotel_details(props, seo_payload=None):
    """The hotel page's hotelDetailResponse (+ the SEO extras op) -> one record.
    Room rates are NOT here: the page's room list is a client-side suspense
    boundary behind the sdt signature (browser-only)."""
    detail = props.get("hotelDetailResponse") or {}
    base = detail.get("hotelBaseInfo") or {}
    position = detail.get("hotelPositionInfo") or {}
    comment = _g(detail, "hotelComment", "comment") or {}
    desc = detail.get("hotelDescriptionInfo") or {}
    policy = detail.get("hotelPolicyInfo") or {}
    seo = (seo_payload or {}).get("data") or props.get("seoSSRData") or {}
    hotel_id = _int(base.get("masterHotelId")) or _int(_g(props, "urlParams", "hotelId"))
    local_match = _LOCAL_NAME_RE.search(_g(base, "nameInfo", "localNameTip") or "")
    check_in = check_out = None
    for item in _policy_items(policy.get("checkInAndOut")):
        if (item["title"] or "").lower().startswith("check-in"):
            check_in = item["description"]
        elif (item["title"] or "").lower().startswith("check-out"):
            check_out = item["description"]
    award = base.get("topAwardInfo") or {}
    return {
        "id": hotel_id,
        "name": _text(_g(base, "nameInfo", "name")) or _text(_g(seo, "seoInfo", "hotelName")),
        "local_name": _text(local_match.group(1)) if local_match else None,
        "english_name": _text(_g(base, "nameInfo", "nameEn")) or _text(_g(seo, "seoInfo", "hotelEnName")),
        "link": hotel_link(hotel_id),
        "star": _int(_g(base, "starInfo", "level")),
        "star_type": _text(_g(base, "starInfo", "type")),
        "star_description": _text(_g(base, "starInfo", "starHover")),
        "brand": _text(base.get("groupNameMulti")),
        "brand_logo": _text(base.get("groupLogo")),
        "opened_year": _int(base.get("openYear")),
        "renovated_year": _int(base.get("fitmentYear")),
        "description": _text(desc.get("description")),
        "labels": [l for l in desc.get("lables") or [] if _text(l)],
        "phone": next((_text(t.get("show")) for t in desc.get("tels") or [] if isinstance(t, dict) and _text(t.get("show"))), None),
        "email": _text(desc.get("email")),
        "video": _text(desc.get("videoUrl")) or _text(_g(detail, "hotelTopImage", "imgUrlList", 0, "videoUrl")),
        "highlights": [{"title": _text(h.get("tagTitle")), "description": _text(h.get("desc"))}
                       for h in _g(base, "newHighlights", "list") or [] if isinstance(h, dict) and _text(h.get("tagTitle"))],
        "award": {"rank": _int(award.get("hotelRank")), "title": _text(award.get("listSubTitle")),
                  "link": _text(award.get("listUrl"))} if _text(award.get("listSubTitle")) else None,
        "last_booked": _text(base.get("lastBooking")),
        "rating": {
            "score": _float(comment.get("score")),
            "max": _float(comment.get("scoreMax")) or 10.0,
            "label": _text(comment.get("scoreDescription")),
            "review_count": _int(comment.get("totalComment")),
            "recommend_rate": _text(comment.get("recommend")),
            "subscores": {(_text(s.get("showName")) or "").lower().replace(" ", "_"): _float(s.get("showScore"))
                          for s in comment.get("scoreDetail") or [] if isinstance(s, dict) and _text(s.get("showName"))},
            "quality_tags": [q for q in comment.get("quality") or [] if _text(q)],
            "top_feature": {"name": _text(_g(comment, "topFacility", "name")), "score": _float(_g(comment, "topFacility", "score"))}
            if _text(_g(comment, "topFacility", "name")) else None,
        },
        "location": {
            "address": _text(position.get("address")),
            "zone": _text(position.get("zoneName")),
            "city": _text(base.get("cityName")),
            "city_id": _int(base.get("cityId")),
            "province": _text(base.get("provinceName")),
            "country": _text(base.get("countryName")),
            "country_id": _int(base.get("countryId")),
            "timezone_offset_seconds": _int(base.get("timeOffset")),
            "coordinate": {"latitude": _float(position.get("lat")), "longitude": _float(position.get("lng")),
                           "system": "WGS84" if position.get("mapType") == "gg" else _text(position.get("mapType"))}
            if _float(position.get("lat")) is not None else None,
            "transit": _text(_g(position, "trafficInfo", "trafficDesc")),
            "nearby": _nearby(position),
        },
        "check_in": check_in,
        "check_out": check_out,
        "policies": _policies(policy),
        "amenities": _amenities(detail.get("hotelFacilityPopV2") or {}),
        "photos": [p.get("imgUrl") for p in _g(detail, "hotelTopImage", "imgUrlList") or [] if _text(_g(p, "imgUrl"))],
        "photo_count": _int(_g(detail, "hotelTopImage", "total")),
        "about": [{"question": _text(q.get("question")), "answer": _text(q.get("answer"))}
                  for q in _g(seo, "seoInfo", "hotelInfo", "list") or [] if isinstance(q, dict) and _text(q.get("answer"))],
        "faq": [{"question": _markup(q.get("question")), "answer": _markup(q.get("answer"))}
                for q in _g(seo, "FAQ", "list") or [] if isinstance(q, dict) and _text(q.get("answer"))],
        "rooms": _seo_rooms(seo),
        "breadcrumbs": [{"name": _text(c.get("text")), "link": _text(c.get("url"))}
                        for c in seo.get("crumbs") or [] if isinstance(c, dict) and _text(c.get("text"))],
        "is_closed": bool(_g(detail, "hotelStatus", "discard")),
    }


# ---- hotel photos ------------------------------------------------------------------

def parse_hotel_photos(payload):
    """28820/ctgethotelalbum -> hotel-provided tabs, guest-provided tabs, the
    featured strip. An unknown hotel is an album with zero photos."""
    pop = _g(payload, "data", "hotelImagePop") or {}
    hotel_tabs = []
    for tab in _g(pop, "hotelProvide", "imgTabs") or []:
        photos = []
        for group in tab.get("imgUrlList") or []:
            for img in group.get("subImgUrlList") or []:
                if not _text(_g(img, "link")):
                    continue
                photos.append({"id": _int(img.get("pictureId")), "link": _text(img.get("link")),
                               "title": _text(img.get("imgTitle")), "room_id": _int(img.get("baseRoomId")) or None})
        hotel_tabs.append({"category": _text(tab.get("categoryName")), "count": _int(tab.get("total")), "photos": photos})
    guest_tabs = []
    for tab in _g(pop, "userProvide", "imgTabs") or []:
        photos = []
        for img in tab.get("subUserAlbumCommentInfo") or []:
            if not _text(_g(img, "picture")):
                continue
            photos.append({"link": _text(img.get("picture")), "caption": _text(img.get("comment")),
                           "rating": _float(img.get("score")), "rating_max": _float(img.get("scoreMax")),
                           "rating_label": _text(img.get("commentDesc")), "author": _text(img.get("userName"))})
        guest_tabs.append({"category": _text(tab.get("categoryName")), "count": _int(tab.get("total")), "photos": photos})
    featured = [p.get("imgUrl") for p in _g(payload, "data", "hotelTopImage", "imgUrlList") or []
                if _text(_g(p, "imgUrl")) and "np-pic" not in p.get("imgUrl")]
    hotel_count = _int(_g(pop, "hotelProvide", "total")) or 0
    guest_count = _int(_g(pop, "userProvide", "total")) or 0
    return {
        "hotel_photo_count": hotel_count,
        "guest_photo_count": guest_count,
        "featured": featured,
        "hotel_photos": hotel_tabs,
        "guest_photos": guest_tabs,
    }


# ---- reviews -------------------------------------------------------------------------

def _review(row):
    user = row.get("userInfo") or {}
    rating = row.get("ratingInfo") or {}
    reply = next((f for f in row.get("feedbackList") or [] if isinstance(f, dict) and _text(f.get("content"))), None)
    images = [_text(i.get("bigImageUrl")) or _text(i.get("mediumImageUrl"))
              for i in row.get("imageCuttingsList") or [] if isinstance(i, dict)]
    return {
        "id": _int(row.get("id")),
        "rating": _float(row.get("rating")),
        "rating_max": _float(rating.get("fullRating")) or 10.0,
        "rating_label": _text(rating.get("commentLevel")),
        "subscores": {"location": _float(rating.get("ratingLocation")), "amenities": _float(rating.get("ratingFacility")),
                      "service": _float(rating.get("ratingService")), "cleanliness": _float(rating.get("ratingRoom"))},
        "text": _text(row.get("content")),
        "language": _text(row.get("language")),
        "translated_text": _text(row.get("translatedContent")),
        "translated_language": _text(row.get("translatedLanguage")),
        "is_recommended": row.get("recommend") if isinstance(row.get("recommend"), bool) else None,
        "traveler_type": _text(row.get("travelTypeText")) or TRAVELER_TYPE_NAMES.get(row.get("travelType")),
        "room": _text(row.get("roomName")),
        "room_id": _int(row.get("roomID")) or None,
        "stay_date": _day(row.get("checkinDate")),
        "date": _iso(row.get("createDate")),
        "helpful_count": _int(row.get("usefulCount")),
        "images": [i for i in images if i],
        # videoList items are {cover, url, duration, videoId} (2026-11-10)
        "videos": [v for v in ((_text(v.get("url")) or _text(v.get("videoUrl"))) if isinstance(v, dict) else _text(v)
                               for v in row.get("videoList") or []) if v],
        # sourceName is the partner site for imported reviews ("ctrip") but a
        # language name ("English") on Trip.com's own reviews
        "source": "trip.com" if row.get("isTripComment") is True else _text(row.get("sourceName")),
        "is_trip_review": row.get("isTripComment") if isinstance(row.get("isTripComment"), bool) else None,
        "author": {
            "name": _text(user.get("nickName")),
            "country": _text(user.get("regionName")),
            # "XX" is Trip.com's placeholder for a reviewer whose region it hides
            "country_code": None if (_text(user.get("regionCode")) or "XX").upper() == "XX" else _text(user.get("regionCode")),
            "avatar": _text(user.get("headPictureUrl")),
            "review_count": _int(user.get("commentCount")),
            "level": _text(_g(user, "grade", "title")),
            "is_anonymous": user.get("isAnonymous") if isinstance(user.get("isAnonymous"), bool) else None,
        },
        "hotel_reply": {"text": _text(reply.get("content")), "translated_text": _text(reply.get("translatedContent")),
                        "language": _text(reply.get("language")), "date": _day(reply.get("createDate"))} if reply else None,
    }


def _summary(rating):
    if not rating or _float(rating.get("ratingAll")) is None or not _int(rating.get("showCommentNum")):
        return None
    return {
        "score": _float(rating.get("ratingAll")),
        "max": _float(rating.get("fullRating")) or 10.0,
        "label": _text(rating.get("commentLevel")),
        "review_count": _int(rating.get("showCommentNum")),
        "subscores": {"location": _float(rating.get("ratingLocation")), "amenities": _float(rating.get("ratingFacility")),
                      "service": _float(rating.get("ratingService")), "cleanliness": _float(rating.get("ratingRoom"))},
    }


def _counted(items, name_key="name"):
    """Filter facets with their review counts. The "All Travelers" / "All
    Languages" / "All Room Types" entry (commentCount -1) is the UI's
    no-filter choice, not a facet, so it is left out."""
    out = []
    for item in items or []:
        if not isinstance(item, dict) or not _text(item.get(name_key)):
            continue
        count = _int(item.get("commentCount"))
        if count is not None and count < 0:
            continue
        out.append({"id": _int(item.get("id")), "name": _text(item.get(name_key)), "count": count})
    return out


def parse_hotel_reviews(payload):
    """34308/getHotelCommentInfo -> {results, summary, filters, totals}."""
    data = payload.get("data") or {}
    reviews = []
    for group in data.get("groupList") or []:
        reviews.extend(_review(r) for r in group.get("commentList") or [] if isinstance(r, dict))
    ai = data.get("aiSummaryEntities") or {}
    return {
        "results": reviews,
        "summary": _summary(data.get("commentRating")),
        "ai_summary": [{"topic": _text(i.get("title")), "conclusion": _text(i.get("conclusion")),
                        "is_positive": i.get("positive") if isinstance(i.get("positive"), bool) else None,
                        "review_count": _int(i.get("commentNum"))}
                       for i in ai.get("summaryItemList") or [] if isinstance(i, dict) and _text(i.get("conclusion"))],
        "tags": _counted(data.get("commentTagList")),
        "traveler_types": _counted(data.get("travelTypeList")),
        "languages": _counted(data.get("languageTypeList")),
        "rooms": _counted(data.get("roomList")),
        "total_count": _int(data.get("totalCount")),
        "filtered_count": _int(data.get("totalCountForPage")),
    }


# ---- things to do (Next.js pages) ----------------------------------------------------

def extract_next_data(html):
    match = _NEXT_DATA_RE.search(html or "")
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except ValueError:
        return None


def _page_model(next_data):
    return _g(next_data, "props", "pageProps", "initialState", "__STATE_MAP__", "PageModel") or {}


def _money(text):
    """'US$53.34' -> (53.34, 'USD'); other symbols keep their code when known."""
    amount = _amount(text)
    if amount is None:
        return None, None
    head = re.sub(r"[\d.,\s-]", "", str(text)).strip()
    currency = {"US$": "USD", "$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "₹": "INR", "HK$": "HKD",
                "S$": "SGD", "A$": "AUD", "C$": "CAD", "₩": "KRW", "฿": "THB", "RM": "MYR", "R$": "BRL"}.get(head)
    if currency is None and re.fullmatch(r"[A-Z]{3}", head or ""):
        currency = head
    return amount, currency


def _product_card(product, currency=None):
    """A things-to-do card. US city pages mix attractions in (isPoi: true,
    the id is a POI id and the url a travel-guide page), so those keep
    kind "attraction" and their guide link."""
    product_id = _int(product.get("id"))
    if product_id is None:
        return None
    is_poi = product.get("isPoi") is True
    price_info = product.get("priceInfo") or {}
    amount, parsed_currency = _money(price_info.get("price"))
    original, _ = _money(price_info.get("deletePrice"))
    return {
        "kind": "attraction" if is_poi else "experience",
        "id": product_id,
        "title": _text(product.get("title")),
        "link": attraction_link(product_id, product.get("url")) if is_poi else experience_link(product_id),
        "type": None if is_poi else _text(product.get("productTypeName")),
        "rank": _text(product.get("rank")),
        "popularity_score": _float(product.get("hot")),
        "open_status": _text(product.get("openStatus")),
        "location": _text(_g(product, "locationInfo", "distanceDesc")),
        "rating": {"score": _float(product.get("scoreValue")) or _float(product.get("score")),
                   "label": _text(product.get("scoreDesc")), "review_count": _int(product.get("reviewsCount")) or _count(product.get("reviews"))},
        "bookings": _count(product.get("sales")),
        "price": {"amount": _float(product.get("priceValue")) or amount, "currency": parsed_currency or currency,
                  "prefix": _text(price_info.get("prefix")), "original_amount": original,
                  "discount": _text(product.get("discountTag"))},
        "tags": [t for t in product.get("tags") or [] if _text(t)],
        "promotion_tags": [t for t in product.get("promotionTags") or [] if _text(t)],
        "badges": _titles(product.get("newTags"), key="text"),
        "highlight": _text(product.get("lightspot")),
        "recommend_note": _text(_g(product, "highLightTag", "value", "text")),
        "departure": _text(_g(product, "departure", "text")),
        "images": [i for i in product.get("images") or [] if _text(i)] or
                  ([_text(_g(product, "imageInfo", "url"))] if _text(_g(product, "imageInfo", "url")) else []),
        "is_trip_select": bool(product.get("tripSelect")),
    }


def _poi_card(data, currency=None):
    poi_id = _int(data.get("poiId"))
    if poi_id is None:
        return None
    comment = data.get("commentInfo") or {}
    price = data.get("priceInfo") or {}
    parent = data.get("parentPoiInfo") or {}
    return {
        "kind": "attraction",
        "id": poi_id,
        "business_id": _int(data.get("businessId")),
        "name": _text(data.get("poiName")),
        "local_name": _text(data.get("poiSubtitleName")),
        "english_name": _text(data.get("poiEName")),
        "link": attraction_link(poi_id, data.get("detailUrl")),
        "categories": [t for t in data.get("tagNameList") or [] if _text(t)],
        "introduction": _text(data.get("introduction")),
        "address": _text(data.get("address")),
        "district": _text(data.get("districtName")),
        "district_id": _int(data.get("districtId")),
        "zone": _text(data.get("zoneName")),
        "coordinate": _coordinate(data.get("coordinate")),
        "distance": _text(_g(data, "distanceInfo", "distanceStr")),
        "rating": {"score": _float(comment.get("commentScore")), "review_count": _int(comment.get("commentCount")),
                   "tripadvisor_review_count": _int(comment.get("taCommentCount"))},
        "popularity_score": _float(data.get("hotScore")),
        "rank": _text(_g(data, "rankingInfo", "rankDesc")),
        "recommend_reason": _text(_g(data, "rankingInfo", "recommendReason")),
        "price": {"amount": _float(price.get("price")) or None, "currency": currency,
                  "bookings": _count(price.get("salesVolumeDesc"))},
        # the card glues its "Today" / "Open" labels: "TodayOpen 8:00 AM–12:00 AM"
        "opening_hours": _TODAY_OPEN_RE.sub("Open today ", _text(data.get("openTimeDesc")) or "") or None,
        "open_status": _text(data.get("openStatus")),
        "duration": (_text(data.get("playSpendTime")) or "").replace("Duration:", "").strip() or None,
        "image": _text(data.get("coverImageUrl")),
        "images": [_text(i.get("coverImageUrl")) for i in data.get("coverImageList") or [] if _text(_g(i, "coverImageUrl"))],
        "tags": _titles(data.get("otherTagList"), key="name"),
        "trend": _text(data.get("explainDesc")),
        "parent": {"id": _int(parent.get("poiId")), "name": _text(parent.get("poiName"))} if _int(parent.get("poiId")) else None,
        "is_ad": bool(data.get("isAdvertisement")),
    }


def parse_attractions_page(next_data, currency=None):
    """/things-to-do/list __NEXT_DATA__ -> {destination, results, total_count}.
    Cards are POIs (jsonStr-wrapped) and bookable products, mixed. Product
    cards carry no currency of their own; the page prices everything in the
    requested `currency`."""
    model = _page_model(next_data)
    mix = model.get("mixList") or {}
    bucket = next((v for v in mix.values() if isinstance(v, dict)), {})
    card_data = bucket.get("cardData") or {}
    page_currency = currency
    results = []
    for card in card_data.get("cardList") or []:
        data = card.get("data") if isinstance(card, dict) else None
        if not isinstance(data, dict):
            continue
        currency = _text(data.get("currency")) or page_currency
        parsed = None
        if data.get("jsonStr"):
            try:
                inner = json.loads(json.loads(data["jsonStr"]).get("data") or "{}")
            except (ValueError, AttributeError, TypeError):
                inner = {}
            parsed = _poi_card(inner, currency) if inner else None
        elif data.get("title"):
            parsed = _product_card(data, currency)
        if parsed:
            results.append(parsed)
    header = model.get("headerCard") or {}
    destination = None
    if _int(header.get("id")) and _text(header.get("name")):
        destination = {"id": _int(header.get("id")), "name": _text(header.get("name")),
                       "product_count": _int(header.get("totalProduct")),
                       "is_in_china": header.get("inChina") if isinstance(header.get("inChina"), bool) else None}
    return {"destination": destination, "results": results, "total_count": _int(card_data.get("total"))}


def parse_attraction_page(next_data):
    """/travel-guide/attraction/... __NEXT_DATA__ (appData.overviewData, the
    same record as 19974/getTripSightOverview) -> one attraction."""
    app = _g(next_data, "props", "pageProps", "initialState", "appData") or {}
    overview = app.get("overviewData") or {}
    basic = overview.get("basicInfo") or {}
    poi_id = _int(basic.get("poiId"))
    if poi_id is None:
        return None
    district = overview.get("districtInfo") or {}
    rank = overview.get("rankInfo") or {}
    open_info = overview.get("openInfo") or {}
    phones = []
    for group in basic.get("formatTelephoneList") or []:
        for phone in group.get("phoneList") or []:
            if _text(_g(phone, "phoneNumber")):
                phones.append({"number": _text(phone.get("phoneNumber")), "purpose": _text(group.get("phoneFunction"))})
    if not phones:
        phones = [{"number": _text(t), "purpose": None} for t in basic.get("telephone") or [] if _text(t)]
    city = next((d for d in _g(overview, "additional", "districtInfo") or [] if d.get("type") == "CITY"), None)
    return {
        "id": poi_id,
        "name": _text(basic.get("poiName")),
        "local_name": _text(basic.get("poiSubtitleName")),
        "english_name": _text(basic.get("poiEnglishName")),
        "link": attraction_link(poi_id, basic.get("detailUrl")),
        "type": (_text(basic.get("poiType")) or "").lower() or None,
        "categories": [t for t in basic.get("tagList") or [] if _text(t)],
        "address": _text(basic.get("address")),
        "introduction": _strip_html(basic.get("introduction")),
        "duration": _text(basic.get("playSpendTime")),
        "is_free": basic.get("isFree") if isinstance(basic.get("isFree"), bool) else None,
        "phones": phones,
        "coordinate": _coordinate(basic.get("coordinate")),
        "district": {"id": _int(district.get("districtId")), "name": _text(district.get("districtName")),
                     "is_in_china": district.get("inChina") if isinstance(district.get("inChina"), bool) else None},
        "city": {"id": _int(city.get("districtId")), "name": _text(city.get("name"))} if city else None,
        "rating": {"score": _float(_g(overview, "comment", "commentScore")),
                   "review_count": _int(_g(overview, "comment", "commentCount")),
                   "tripadvisor_review_count": _int(_g(overview, "taReview", "reviewTaCount"))},
        "popularity_score": _float(basic.get("hotScore")),
        "favorite_count": _int(_g(overview, "favorite", "favoriteCount")),
        "rank": {"position": _int(rank.get("rank")), "title": _text(rank.get("description")),
                 "link": _text(rank.get("jumpUrl"))} if _text(rank.get("description")) else None,
        "opening_hours": {
            "summary": _text(open_info.get("openTimeDesc")),
            # often a copy of the summary; kept only when it says more
            "details": None if _strip_html(open_info.get("openTimeDetailDesc")) == _text(open_info.get("openTimeDesc"))
            else _strip_html(open_info.get("openTimeDetailDesc")),
            "rules": [{"dates": _text(r.get("dateDesc")),
                       "description": "; ".join(_text(x.get("description")) for x in r.get("openTimeRuleInfoType") or []
                                                if isinstance(x, dict) and _text(x.get("description"))) or None}
                      for r in open_info.get("tripOpenTimeRuleInfoList") or [] if isinstance(r, dict)],
        } if open_info else None,
        "entry_policies": [{"name": _text(p.get("name")), "description": _text(p.get("explain"))}
                           for p in _g(overview, "entryPolicyInfo", "entryPolicyItemList") or []
                           if isinstance(p, dict) and _text(p.get("name"))],
        "images": [i for i in _g(overview, "imageInfo", "imageList") or [] if _text(i)],
        "image_count": _int(_g(overview, "imageInfo", "imageCount")),
        "breadcrumbs": [{"name": _text(c.get("content")), "type": (_text(c.get("type")) or "").lower() or None}
                        for c in app.get("breadCrumbList") or [] if isinstance(c, dict) and _text(c.get("content"))],
    }


def parse_experiences_page(next_data, slug=None, currency=None):
    """/things-to-do/experiences/<slug>/ __NEXT_DATA__ -> {destination,
    results, categories, reviews}. None when the slug is unknown (the page
    renders with an empty pageInfo). `currency` (the requested one) labels
    a price whose text carries no symbol."""
    state = _g(next_data, "props", "pageProps", "initialState") or {}
    info = state.get("pageInfo") or {}
    products = _g(state, "productList", "products") or []
    if not _text(info.get("title")) and not products:
        return None
    first_city = next((c for c in _g(info, "cityList", "cList") or [] if isinstance(c, dict)), {})
    categories = []
    for city in _g(info, "cityList", "cList") or []:
        if not isinstance(city, dict) or not _text(city.get("slug")):
            continue
        categories.append({"slug": _text(city.get("slug")), "name": _text(city.get("pshowcodeName")),
                           "title": _text(_g(city, "productList", "title")), "link": _text(_g(city, "productList", "allURL"))})
    reviews = [{"product_id": _int(c.get("id")), "product_name": _text(c.get("productName")),
                "rating": _float(c.get("rating")), "author": _text(c.get("userName")), "date": _text(c.get("date")),
                "text": _text(c.get("comment")), "original_text": _text(c.get("originalContent")) if _text(c.get("originalContent")) != _text(c.get("comment")) else None}
               for c in info.get("comments") or [] if isinstance(c, dict) and _text(c.get("comment"))]
    return {
        "destination": {
            "slug": slug or _text(_g(info, "slugInfo", "city")),
            "name": _text(first_city.get("newCityName")) or _text(info.get("cityName")),
            "title": _text(info.get("title")),
            "district_id": _int(info.get("cityId")),
            "hotel_city_id": _int(info.get("hotelCityId")),
            "country": _text(first_city.get("countryName")),
            "country_id": _int(first_city.get("countryId")),
            # the page's own text carries a stray locale token ("th_THe eastern coast")
            "description": (_text(info.get("description")) or "").replace("th_TH", "th") or None,
            "link": experiences_link(slug) if slug else _text(info.get("cityLink")),
            "image": _text(info.get("bgImageUrl")),
        },
        "results": [card for card in (_product_card(p, currency) for p in products if isinstance(p, dict)) if card],
        "categories": categories,
        "reviews": reviews,
    }


def _kv_contents(items):
    return [_text(i.get("value")) for i in items or [] if isinstance(i, dict) and _text(i.get("value"))]


# sale-property names the site leaves in Chinese on English pages
_OPTION_NAMES = {"车型（结构化）": "Vehicle type"}


def _sale_options(packages):
    """The bookable choices across every package, one entry per option name
    with its distinct values (each package repeats every option; `packages`
    already names the combination each one is). A value that only repeats
    its option name ("Itinerary: itinerary") is a placeholder and dropped."""
    merged = {}
    for pkg in packages or []:
        for prop in (pkg.get("salesProperties") if isinstance(pkg, dict) else None) or []:
            name = _text(_g(prop, "saleProperty", "name")) if isinstance(prop, dict) else None
            if not name:
                continue
            name = _OPTION_NAMES.get(name, name)
            values = merged.setdefault(name, [])
            for value in _titles(prop.get("salePropertyValues"), key="name"):
                if value.lower() != name.lower() and value not in values:
                    values.append(value)
    return [{"name": name, "values": values} for name, values in merged.items() if values]


def parse_experience_page(next_data):
    """/things-to-do/detail/<id> __NEXT_DATA__ (PageModel.ds.getProductInfo)
    -> one bookable product. Prices are client-side (ssrHidePrice), so the
    only price is schema.org's AggregateOffer low/high."""
    model = _page_model(next_data)
    product = _g(model, "ds", "getProductInfo", 0) or {}
    basic = product.get("basicInfo") or {}
    product_id = _int(product.get("productId"))
    if product_id is None or not _text(basic.get("productName")):
        return None
    offer = rating = None
    for graph in _g(model, "seo", "ldJSON") or []:
        nodes = graph.get("@graph") if isinstance(graph, dict) and isinstance(graph.get("@graph"), list) else [graph]
        for node in nodes:
            if isinstance(node, dict) and node.get("@type") == "Product":
                offer = node.get("offers") or offer
                rating = node.get("aggregateRating") or rating
    vendor = product.get("vendorInfo") or {}
    refund = product.get("refundPolicy") or model.get("refundPolicy") or {}
    departure = _g(product, "cityInfo", "departureInfos", 0) or {}
    sales = product.get("salesVolume") or {}
    return {
        "id": product_id,
        "name": _text(basic.get("productName")),
        "link": experience_link(product_id),
        "category": _text(_g(basic, "categoryInfo", "name")),
        "categories": _titles(basic.get("viewTags"), key="name"),
        "description": _strip_html(product.get("productDescription")),
        "sections": [{"title": _text(a.get("title")), "items": _kv_contents(a.get("contents"))}
                     for a in product.get("addInfos") or [] if isinstance(a, dict) and _kv_contents(a.get("contents"))],
        "rating": {"score": _float(_g(product, "commentInfo", "commentScore")),
                   "review_count": _int(_g(product, "commentInfo", "commentCount")),
                   "max": _float(_g(rating, "bestRating")) or 5.0},
        "sales": {"monthly": _int(sales.get("monthlySales")), "yearly": _int(sales.get("yearlySales"))},
        "price": {"from": _float(_g(offer, "lowPrice")), "to": _float(_g(offer, "highPrice")),
                  "currency": _text(_g(offer, "priceCurrency")), "offer_count": _int(_g(offer, "offerCount")),
                  "availability": (_text(_g(offer, "availability")) or "").rsplit("/", 1)[-1] or None} if offer else None,
        "city": {"name": _text(departure.get("name")), "district_id": _int(departure.get("districtId")),
                 "hotel_city_id": _int(departure.get("cityId")), "country_id": _int(departure.get("countryId")),
                 "utc_offset_seconds": _int(_g(product, "cityInfo", "utcOffset"))} if departure else None,
        "vendor": {"id": _int(vendor.get("vendorId")), "name": _text(vendor.get("vendorName")),
                   "brand": _text(vendor.get("vendorBrandName"))} if _int(vendor.get("vendorId")) else None,
        "images": [_text(i.get("rawImgUrl")) for i in _g(product, "mediaInfo", "imgInfos") or [] if _text(_g(i, "rawImgUrl"))],
        "booking": {"min_quantity": _int(basic.get("minBookingQuantity")), "min_days": _int(basic.get("minBookingDays")),
                    "max_days": _int(basic.get("maxBookingDays")),
                    "advance_days": _int(_g(basic, "advanceBookingInfo", "advanceBookingDay")),
                    "advance_cutoff_time": _text(_g(basic, "advanceBookingInfo", "advanceBookingTime")),
                    "has_inventory": "hasInventory" in (basic.get("flags") or [])},
        "packages": [{"id": _int(p.get("packageId")), "name": _text(p.get("packageName")),
                      "introduction": _text(p.get("packageIntroduction")),
                      "min_quantity": _int(p.get("minPackageSaleCount")), "max_quantity": _int(p.get("maxPackageSaleCount")),
                      "advance_booking": _text(p.get("advancedBookingDesc")),
                      "monthly_sales": _int(_g(p, "salesVolume", "monthlySales")),
                      "ticket_ids": [_int(r) for r in p.get("resourceIds") or [] if _int(r) is not None]}
                     for p in product.get("packageInfos") or [] if isinstance(p, dict) and _int(p.get("packageId"))],
        "options": _sale_options(basic.get("packages")),
        "tickets": [{"id": _int(r.get("resourceId")), "name": _text(_g(r, "basicInfo", "name")),
                     "description": _text(_g(r, "basicInfo", "resourceDesc")),
                     "currency": _text(_g(r, "basicInfo", "originMarketCurrency")),
                     "min_quantity": _int(_g(r, "basicInfo", "quantityInfo", "min")),
                     "max_quantity": _int(_g(r, "basicInfo", "quantityInfo", "max")),
                     "age_min": _int(_g(r, "basicInfo", "peopleApplicableInfo", "rangeMin")),
                     "age_max": _int(_g(r, "basicInfo", "peopleApplicableInfo", "rangeMax"))}
                    for r in product.get("resourceInfos") or [] if isinstance(r, dict) and _int(r.get("resourceId"))],
        "refund_policy": {"label": _text(refund.get("refundLabel")), "best": _text(refund.get("bestRefundLabel")),
                          "summary": _text(refund.get("refundShowText")), "rules": _kv_contents(refund.get("contents"))}
        if _text(refund.get("refundLabel")) else None,
        "properties": [{"name": _text(_g(p, "prop", "key")), "values": _titles(p.get("propValues"), key="value")}
                       for p in _g(product, "categoryProperty", "properties") or []
                       if isinstance(p, dict) and _titles(p.get("propValues"), key="value")],
        "booking_notes": [{"name": _text(t.get("name")), "notes": [d for d in t.get("descs") or [] if _text(d)]}
                          for g in _g(product, "tagInfo", "groupTags") or [] for t in g.get("tagItems") or []
                          if isinstance(t, dict) and _text(t.get("name"))],
        "breadcrumbs": [{"name": _text(b.get("title")), "link": _text(b.get("link"))}
                        for b in model.get("breadcrumbInfo") or [] if isinstance(b, dict) and _text(b.get("title"))],
    }


# ---- flights -------------------------------------------------------------------------

def _tz_hours(value):
    """timeZone in hours; a region row answers the sentinel -10000."""
    hours = _float(value)
    return hours if hours is not None and abs(hours) <= 14 else None


def _point(row, parent=None):
    """(latitude, longitude), or (None, None) when the row only repeats its
    parent city's point (PoiSearch gives most airports their city's
    coordinates: JFK, LGA and EWR all sit on midtown Manhattan)."""
    lat, lng = _float(row.get("latitude")), _float(row.get("longitude"))
    if parent is not None and (lat, lng) == (_float(parent.get("latitude")), _float(parent.get("longitude"))):
        return None, None
    return lat, lng


def _airport(row, parent):
    lat, lng = _point(row, parent)
    return {"id": _int(row.get("id")), "name": _text(row.get("name")), "code": _text(row.get("airportCode")) or None,
            "city_code": _text(row.get("cityCode")) or None, "city": _text(row.get("cityName")),
            "country": _text(row.get("countryName")), "latitude": lat, "longitude": lng,
            "timezone_offset_hours": _tz_hours(row.get("timeZone"))}


def _child_city(row):
    return {"id": _int(row.get("id")), "name": _text(row.get("name")), "code": _text(row.get("cityCode")) or None,
            "country": _text(row.get("countryName")), "latitude": _float(row.get("latitude")),
            "longitude": _float(row.get("longitude")), "timezone_offset_hours": _tz_hours(row.get("timeZone"))}


def parse_airports(payload):
    """14427/PoiSearch -> places: a city with its airports, a single airport,
    a region (state / province) or a city group with the cities in it."""
    out = []
    for row in payload.get("results") or []:
        if not isinstance(row, dict) or _int(row.get("id")) is None:
            continue
        kind = PLACE_KINDS.get(_int(row.get("dataType")), "place")
        children = [c for c in row.get("childResults") or [] if isinstance(c, dict)]
        out.append({
            "id": _int(row.get("id")),
            "kind": kind,
            "name": _text(row.get("name")),
            "code": _text(row.get("cityCode")) or None,
            "airport_code": _text(row.get("airportCode")) or None,
            "city": _text(row.get("cityName")),
            "country": _text(row.get("countryName")),
            "country_code": _text(row.get("countryCode")) or None,
            "latitude": _float(row.get("latitude")),
            "longitude": _float(row.get("longitude")),
            "timezone_offset_hours": _tz_hours(row.get("timeZone")),
            "airports": [_airport(c, row) for c in children if _int(c.get("dataType")) == 5],
            "cities": [_child_city(c) for c in children if _int(c.get("dataType")) == 3],
        })
    return out


def parse_fare_day(payload, date):
    """14427/GetLowPriceInCalender -> the fare row for `date` (YYYY-MM-DD)."""
    currency = _text(payload.get("currency"))
    for row in payload.get("lowPriceInCalenderDtoInfoList") or []:
        if _epoch_day(_g(row, "dDate")) != date:
            continue
        price = _float(row.get("currencyPrice"))
        return {"date": date, "price": price if price is not None and price > 0 else None, "currency": currency,
                "is_direct": row.get("direct") if isinstance(row.get("direct"), bool) else None,
                "note": _text(row.get("tagContent"))}
    return {"date": date, "price": None, "currency": currency, "is_direct": None, "note": None}
