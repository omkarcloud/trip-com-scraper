"""The 11 Trip.com endpoints. Every path is served with and without the
`/trip-com` prefix, so code generated against the hosted API on RapidAPI
(paths like /hotels/details) runs unchanged against this server.

Params are validated by the marshmallow schemas in trip_com/schemas.py
(ONE param per input: `hotel` takes a Trip.com hotel id or any trip.com hotel
link; `destination` on hotels/search a place name, a city id or a hotel list
link; `attraction` / `experience` an id or the trip.com link; flights take
3-letter IATA city or airport codes); unknown params are rejected."""
import json
from urllib.parse import urlencode

from bottle import request, response, route

from schema_fields import load_query
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError
from trip_com import api, schemas


def json_response(data, status=200):
    response.status = status
    response.content_type = "application/json"
    return json.dumps(data, ensure_ascii=False)


def query_dict():
    """The request query as unicode strings (bottle 0.12's .get() hands back
    latin-1 decoded bytes, so getunicode is mandatory — a UTF-8 "Zürich"
    would otherwise arrive as "ZÃ¼rich")."""
    return {key: request.query.getunicode(key) for key in request.query.keys()}


def _as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _page_link(path, params, page):
    if not page:
        return None
    query = {k: v for k, v in params.items() if v not in (None, "", False)}
    query["page"] = page
    scheme, host = request.urlparts.scheme, request.urlparts.netloc
    return f"{scheme}://{host}{path}?{urlencode(query, doseq=True)}"


def paginate(result, path, params):
    """Lift the `pagination` block into the flat shape the hosted API
    returns: count / per_page / current_page / total_pages / next / previous.
    A page without a site total sets `has_next` instead, and `total_pages`
    stays null rather than a number that grows every page."""
    pagination = result.pop("pagination", None) or {}
    result.pop("count", None)   # per-page count; `count` is the total
    page = _as_int(pagination.get("page")) or _as_int(params.get("page")) or 1
    if "has_next" in pagination:
        has_next = bool(pagination["has_next"])
        total_pages = pagination.get("total_pages")
        if total_pages is not None:
            total_pages = _as_int(total_pages, None)
    else:
        total_pages = max(_as_int(pagination.get("total_pages")), 0)
        has_next = page < total_pages
    out = {
        "count": pagination.get("total_count"),
        "per_page": pagination.get("items_per_page"),
        "current_page": page,
        "total_pages": total_pages,
        "next": _page_link(path, params, page + 1 if has_next else None),
        "previous": _page_link(path, params, page - 1 if page > 1 else None),
    }
    out.update(result)
    return out


def call(label, schema_cls, impl, paginated=False):
    """Validate, run, map errors: bad params -> 400, unknown hotel / place /
    code -> 404, blocks / transport -> 502, anything else -> 500."""
    raw = query_dict()
    data, error = load_query(schema_cls, raw)
    if error:
        return json_response(error, 400)
    try:
        result = impl(**data)
    except ValueError as e:
        return json_response({"error": str(e)}, 400)
    except BadRequest as e:
        return json_response({"error": f"trip-com rejected the request: {e}"}, 400)
    except NotFound as e:
        return json_response({"error": str(e) or "not found"}, 404)
    except Blocked as e:
        return json_response({"error": f"trip-com blocked the request, retry later: {e}"}, 502)
    except UpstreamError as e:
        return json_response({"error": f"trip-com {label} failed: {e}"}, 502)
    except Exception as e:                 # parser surprises
        return json_response({"error": f"trip-com {label} failed: {type(e).__name__}: {e}"}, 500)
    if paginated:
        result = paginate(result, request.path, raw)
    return json_response(result)


ENDPOINTS = [
    ("/hotels/details", schemas.HotelSchema, api.hotel_details, False),
    ("/destinations/search", schemas.DestinationsSearchSchema, api.destinations_search, False),
    ("/hotels/search", schemas.HotelsSearchSchema, api.hotels_search, True),
    ("/hotels/reviews", schemas.HotelReviewsSchema, api.hotel_reviews, True),
    ("/hotels/photos", schemas.HotelPhotosSchema, api.hotel_photos, False),
    ("/attractions/search", schemas.AttractionsSearchSchema, api.attractions_search, False),
    ("/attractions/details", schemas.AttractionSchema, api.attraction_details, False),
    ("/experiences/search", schemas.ExperiencesSearchSchema, api.experiences_search, False),
    ("/experiences/details", schemas.ExperienceSchema, api.experience_details, False),
    ("/flights/airports", schemas.AirportsSearchSchema, api.flights_airports, False),
    ("/flights/calendar", schemas.FlightsCalendarSchema, api.flights_calendar, False),
]


def mount(path, schema_cls, impl, paginated):
    """Serve a handler at /path and /trip-com/path."""
    label = path.strip("/")

    def handler():
        return call(label, schema_cls, impl, paginated)

    handler.__name__ = "trip_com_" + label.replace("/", "_").replace("-", "_")
    route(path, method="GET")(handler)
    route("/trip-com" + path, method="GET")(handler)


for _path, _schema, _impl, _paginated in ENDPOINTS:
    mount(_path, _schema, _impl, _paginated)


@route("/", method="GET")
@route("/health", method="GET")
def health():
    return json_response({"status": "ok", "endpoints": [p for p, *_ in ENDPOINTS]})
