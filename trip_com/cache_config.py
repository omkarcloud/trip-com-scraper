"""Cache TTL per /trip-com/* endpoint (cache.py, keyed on the validated
params). Prices move by the hour on Trip.com, static content by the week.
"""
from datetime import timedelta

# Autocomplete: city ids never change.
DESTINATIONS_CACHE = timedelta(days=7)
# Hotel list: the cheapest-room price per card is live inventory.
HOTELS_SEARCH_CACHE = timedelta(minutes=30)
# Hotel page + SEO extras: static property content (no room rates in it).
HOTEL_DETAILS_CACHE = timedelta(hours=12)
# Photo album: hotels add photos rarely.
HOTEL_PHOTOS_CACHE = timedelta(days=1)
# Reviews: a busy hotel gets a few a day; the first page shifts slowly.
HOTEL_REVIEWS_CACHE = timedelta(hours=6)
# Things-to-do search page: the ranking and prices refresh daily.
ATTRACTIONS_SEARCH_CACHE = timedelta(hours=12)
# POI page: opening hours and intro are editorial content.
ATTRACTION_DETAILS_CACHE = timedelta(days=1)
# Experiences list / product page: "from" prices and discounts move daily.
EXPERIENCES_CACHE = timedelta(hours=6)
# Airport autocomplete: static reference data.
AIRPORTS_CACHE = timedelta(days=7)
# One lowest fare per day: airfares are repriced several times a day.
FARE_DAY_CACHE = timedelta(hours=1)
