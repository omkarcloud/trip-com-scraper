"""Marshmallow request schemas for every /trip-com/* route.

ONE param per input (tripadvisor QueryOrIdField convention, never a sibling
`url`/`id` pair; resolvers in trip_com/refs.py):

    destination  hotels/search: a hotel city id (228), a trip.com hotel list
                 link, OR a place name (resolved through the autocomplete)
    hotel        a hotel id (346403) OR any trip.com hotel link
    attraction   a POI id (88070) OR a trip.com travel-guide attraction link
    experience   a product id (100444859) OR a trip.com things-to-do link
    destination  experiences/search: a things-to-do city slug (tokyo, newyork)
                 OR the experiences page link
    origin / destination  flights: a 3-letter IATA city or airport code

Dates are YYYY-MM-DD; a missing check-in defaults to two weeks ahead and a
missing check-out to two nights after check-in (api.py fills it so the
cache key is complete).
"""
from marshmallow import ValidationError, validates_schema

from schema_fields import (BaseSchema, ChoiceField, CommaListField, CurrencyField, DateField, Flag,
                           LanguageCodeField, PageField, PageSizeField, PositiveInt, Price, QueryField,
                           RefField, StrippedString)
from trip_com import refs

DEFAULT_CHECKIN_LEAD_DAYS = 14
MAX_NIGHTS = 60             # the site's own maxNights
MAX_HOTEL_PAGES = 500       # 20 per page; Trip.com's biggest cities list ~10,000 hotels
MAX_REVIEW_PAGES = 1000
REVIEWS_PAGE_MAX = 100


class HotelRefField(RefField):
    resolver = staticmethod(refs.resolve_hotel)


class DestinationRefField(RefField):
    resolver = staticmethod(refs.resolve_destination)


class AttractionRefField(RefField):
    resolver = staticmethod(refs.resolve_attraction)


class ExperienceRefField(RefField):
    resolver = staticmethod(refs.resolve_experience)


class ExperiencesDestinationField(RefField):
    resolver = staticmethod(refs.resolve_experiences_destination)


class PlaceCodeField(RefField):
    resolver = staticmethod(refs.resolve_place_code)


class ChildrenAgesField(RefField):
    resolver = staticmethod(refs.resolve_children_ages)

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("load_default", list)
        super().__init__(**kwargs)


class LocaleField(LanguageCodeField):
    """Trip.com locale: 'en', 'en-US', 'ja-JP' ... -> 'xx-RR' (default en-US)."""

    def __init__(self, **kwargs):
        kwargs.setdefault("load_default", "en-US")
        kwargs.setdefault("metadata", {"description": "content language, e.g. en-US, ja-JP, de-DE, zh-CN"})
        super().__init__(**kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        tag = super()._deserialize(value, attr, data, **kwargs)
        return refs.normalize_locale(tag) if tag else tag


class OptionalTextField(StrippedString):
    """Optional free text (1-100 chars when given)."""

    def __init__(self, max_length=100, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)
        self.max_length = max_length

    def _deserialize(self, value, attr, data, **kwargs):
        text = super()._deserialize(value, attr, data, **kwargs)
        if text is not None and len(text) > self.max_length:
            raise ValidationError(f"Longer than maximum length {self.max_length}.")
        return text


def _currency():
    return CurrencyField(load_default="USD", metadata={"description": "3-letter price currency (USD, EUR, JPY, ...)"})


# ---- destinations ----------------------------------------------------------------------

class DestinationsSearchSchema(BaseSchema):
    query = QueryField(max_length=100, metadata={"description": "city, region, landmark or hotel name to look up"})
    locale = LocaleField()


# ---- hotels ------------------------------------------------------------------------------

class StaySchema(BaseSchema):
    check_in = DateField(bound="future", default_days_ahead=DEFAULT_CHECKIN_LEAD_DAYS, metadata={
        "description": "check-in date YYYY-MM-DD (default: two weeks ahead)"})
    check_out = DateField(bound="future", metadata={
        "description": "check-out date YYYY-MM-DD (default: two nights after check-in)"})
    adults = PositiveInt(max_value=10, load_default=2, metadata={"description": "adults per room (default 2)"})
    children_ages = ChildrenAgesField(metadata={"description": "children's ages, comma separated (e.g. 3,7)"})
    rooms = PositiveInt(max_value=8, load_default=1, metadata={"description": "number of rooms (default 1)"})
    currency = _currency()
    locale = LocaleField()

    @validates_schema
    def _stay_is_sane(self, data, **kwargs):
        check_in, check_out = data.get("check_in"), data.get("check_out")
        if check_in and check_out:
            from datetime import date
            nights = (date.fromisoformat(check_out) - date.fromisoformat(check_in)).days
            if nights < 1:
                raise ValidationError("Must be after check_in.", "check_out")
            if nights > MAX_NIGHTS:
                raise ValidationError(f"At most {MAX_NIGHTS} nights.", "check_out")


class HotelsSearchSchema(StaySchema):
    destination = DestinationRefField(metadata={
        "description": "Trip.com hotel city id (228), a trip.com hotel list link, or a place name (e.g. Tokyo)"})
    query = OptionalTextField(metadata={
        "description": "hotel name or landmark to narrow the list (e.g. Grand Hyatt, Eiffel)"})
    sort = ChoiceField(list(refs.HOTEL_SORTS), load_default="recommended", metadata={
        "description": "recommended, price_low_to_high, price_high_to_low, top_reviewed, stars_high_to_low, distance, distance_by_road"})
    stars = CommaListField(allowed=refs.STAR_CHOICES, max_items=4, metadata={
        "description": "star classes to keep, comma separated: 2 (2 and below), 3, 4, 5"})
    min_price = Price(metadata={"description": "lowest nightly room price, in `currency`"})
    max_price = Price(metadata={"description": "highest nightly room price, in `currency`"})
    min_score = ChoiceField(list(refs.MIN_SCORES), metadata={"description": "minimum guest rating: 6, 7, 8 or 9 (out of 10)"})
    free_cancellation = Flag(metadata={"description": "true = only rooms with free cancellation"})
    breakfast = Flag(metadata={"description": "true = only rates with breakfast included"})
    payment = ChoiceField(list(refs.PAYMENTS), metadata={"description": "pay_at_hotel, prepay or no_card_needed"})
    bed_type = ChoiceField(list(refs.BED_TYPES), metadata={
        "description": "double, two_beds, king, single, three_beds or four_or_more_beds"})
    page = PageField(max_page=MAX_HOTEL_PAGES, metadata={"description": "page number, 20 hotels per page"})

    @validates_schema
    def _price_range(self, data, **kwargs):
        low, high = data.get("min_price"), data.get("max_price")
        if low is not None and high is not None and high < low:
            raise ValidationError("Must be at least min_price.", "max_price")


class HotelSchema(StaySchema):
    hotel = HotelRefField(metadata={"description": "Trip.com hotel id (346403) or any trip.com hotel link"})


class HotelPhotosSchema(BaseSchema):
    hotel = HotelRefField(metadata={"description": "Trip.com hotel id (346403) or any trip.com hotel link"})
    locale = LocaleField()


class HotelReviewsSchema(BaseSchema):
    hotel = HotelRefField(metadata={"description": "Trip.com hotel id (346403) or any trip.com hotel link"})
    sort = ChoiceField(list(refs.REVIEW_SORTS), load_default="relevance", metadata={
        "description": "relevance, recent, highest_rating or lowest_rating"})
    traveler_type = ChoiceField(list(refs.TRAVELER_TYPES), metadata={
        "description": "business, family, friends, solo, couple or booked_for_others"})
    include_duplicates = Flag(load_default=False, metadata={
        "description": "true = also count the Ctrip-side copies of the same stays (Trip.com's larger total)"})
    page = PageField(max_page=MAX_REVIEW_PAGES, metadata={"description": "page number"})
    per_page = PageSizeField(default=10, max_size=REVIEWS_PAGE_MAX, metadata={
        "description": "reviews per page (1-100, default 10)"})
    locale = LocaleField()


# ---- attractions / experiences ------------------------------------------------------------

class AttractionsSearchSchema(BaseSchema):
    query = QueryField(max_length=100, metadata={
        "description": "destination (Tokyo, Paris) or a search phrase (museum, Tokyo Tower)"})
    currency = _currency()
    locale = LocaleField()


class AttractionSchema(BaseSchema):
    attraction = AttractionRefField(metadata={
        "description": "Trip.com attraction id (88070) or a trip.com travel-guide attraction link"})
    currency = _currency()
    locale = LocaleField()


class ExperiencesSearchSchema(BaseSchema):
    destination = ExperiencesDestinationField(metadata={
        "description": "things-to-do city slug (tokyo, paris, newyork, hongkong) or the trip.com experiences page link"})
    currency = _currency()
    locale = LocaleField()


class ExperienceSchema(BaseSchema):
    experience = ExperienceRefField(metadata={
        "description": "Trip.com experience / ticket id (100444859) or a trip.com things-to-do link"})
    currency = _currency()
    locale = LocaleField()


# ---- flights --------------------------------------------------------------------------------

class AirportsSearchSchema(BaseSchema):
    query = QueryField(max_length=100, metadata={"description": "city, airport name or IATA code to look up"})
    locale = LocaleField()


class FlightsCalendarSchema(BaseSchema):
    origin = PlaceCodeField(metadata={"description": "departure city or airport IATA code (TYO, NRT, LON)"})
    destination = PlaceCodeField(metadata={"description": "arrival city or airport IATA code (SHA, PVG, NYC)"})
    start_date = DateField(bound="future", default_days_ahead=1, metadata={
        "description": "first departure date YYYY-MM-DD (default: tomorrow)"})
    end_date = DateField(bound="future", metadata={
        "description": f"last departure date YYYY-MM-DD (default: 6 days after start_date; at most {refs.MAX_CALENDAR_DAYS} days)"})
    # No cabin / passenger / trip-type params: GetLowPriceInCalender answers
    # the same one-way lowest fare whatever it is sent for them (cabin Y/C/F,
    # 1-3 adults, children, infants and FlightWay OW/RT all returned identical
    # prices on 2026-11-07 and 2026-11-10), so offering them would label
    # economy fares as business or round-trip.
    currency = _currency()
    locale = LocaleField()

    @validates_schema
    def _window(self, data, **kwargs):
        from datetime import date
        start, end = data.get("start_date"), data.get("end_date")
        if start and end:
            days = (date.fromisoformat(end) - date.fromisoformat(start)).days
            if days < 0:
                raise ValidationError("Must be on or after start_date.", "end_date")
            if days >= refs.MAX_CALENDAR_DAYS:
                raise ValidationError(f"At most {refs.MAX_CALENDAR_DAYS} days after start_date.", "end_date")
