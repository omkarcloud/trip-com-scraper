"""Configuration for the Trip Com Scraper. Everything can be set with an
environment variable; the defaults work out of the box.

    PORT            port the API listens on (default 8000)
    TRIP_COM_PROXY  proxy URL for every request, e.g. http://user:pass@host:port
                    (default: none — direct). Trip.com's hotel, review,
                    things-to-do and flight endpoints answered plain requests
                    from cloud servers and home connections alike, so you very
                    likely don't need this. Set it only if you start seeing
                    failures at high volume.

Everything else below is a plain constant with a working default — edit it
here if you need to.
"""
import os

PORT = int(os.environ.get("PORT", "8000"))

# Retry policy for transport errors and blocks (every request). A retry
# opens a fresh connection.
MAX_RETRIES = 3
RETRY_BACKOFF = 2          # seconds, multiplied by the attempt number

TRIP_COM_PROXY = os.environ.get("TRIP_COM_PROXY") or None


def trip_com_proxy():
    """The exit every Trip.com request leaves through. None = your own connection."""
    return os.environ.get("TRIP_COM_PROXY") or None
