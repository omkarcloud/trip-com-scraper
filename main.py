"""Use the scraper straight from Python — no server needed.

    python main.py

Every function returns the same JSON the API does; results are written to
output/*.json. Params go through the same schemas the API uses, so defaults
are filled in (a two-night stay two weeks out, 2 adults, USD) and a hotel id
or any trip.com hotel link both work.
"""
import json
import os

from trip_com import api, schemas

os.makedirs("output", exist_ok=True)


def save(name, data):
    path = os.path.join("output", name)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"saved {path}")


def run(schema_cls, fn, **params):
    return fn(**schema_cls().load(params))


if __name__ == "__main__":
    # a hotel id or any trip.com hotel link — rating, address, amenities, policies, room types, FAQ, photos
    save("hotel_bellagio.json", run(schemas.HotelSchema, api.hotel_details, hotel="715163"))

    # a city name, city id or trip.com hotel list link — 20 hotels per page with the cheapest room's price
    save("hotels_las_vegas.json", run(schemas.HotelsSearchSchema, api.hotels_search, destination="Las Vegas"))

    # guest reviews, up to 100 per page, with translations and hotel replies
    save("reviews_bellagio.json", run(schemas.HotelReviewsSchema, api.hotel_reviews, hotel="715163"))
