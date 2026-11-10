"""The network helper the trip_com module needs: the Accept-Language its
sessions send. Requests go out from your own connection (or your
TRIP_COM_PROXY), so every session announces the plain English list a desktop
browser in the US sends — the language of the returned text is chosen by each
endpoint's `locale` param, not by this header."""

ACCEPT_LANGUAGE = "en-US,en;q=0.9"


def site_accept_language(site=None):
    return ACCEPT_LANGUAGE


def accept_language(proxy=None, country=None):
    return ACCEPT_LANGUAGE
