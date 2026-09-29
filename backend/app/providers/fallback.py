"""Composite geocoder: open data first, a commercial provider only when open data cannot answer well.

Kept separate from `osm` and `commercial` because it depends on both, and `commercial` already imports
`osm` — combining them would make that import circular.
"""

from __future__ import annotations

from app.domain.route import GeocodeResult
from app.errors import JmpError
from app.observability.logging import get_logger
from app.providers import commercial, osm
from app.settings import settings

log = get_logger("jmp.providers")


class OsmWithGoogleFallback:
    """OpenStreetMap first; Google only when OSM cannot answer well.

    Falling back on "not found" alone would miss the failure that actually matters. OSM's dangerous
    outcome is not silence but a confident wrong answer — "Apex Hospital, Agra" returning a hospital in
    Nashik, 1,000 km away. That arrives as a low-confidence *match*, not an error, so the confidence floor
    is what catches it. All three of not-found, ambiguous and low-confidence therefore fall through.

    Google is billed per request, so it is only ever asked after OSM has already failed. On real
    field-visit addresses that is roughly half of them; on ordinary town-to-town journeys, almost none.

    Its own cache namespace (`name`), deliberately: results cached under the plain OSM geocoder include
    low-confidence matches that were accepted before this class existed, and reusing that namespace would
    serve them straight back without ever consulting the fallback.
    """

    name = "osm_google"

    def __init__(self) -> None:
        self.primary = osm.NominatimWithPhotonFallback()
        self.fallback = commercial.GoogleGeocoder()

    def geocode(self, query: str, region_hint: str | None = None) -> GeocodeResult:
        floor = settings().geocode_fallback_min_confidence
        weak: GeocodeResult | None = None
        try:
            hit = self.primary.geocode(query, region_hint)
        except JmpError as exc:
            reason = str(getattr(exc, "code", "")) or type(exc).__name__
        else:
            if hit.confidence >= floor:
                return hit
            weak, reason = hit, f"confidence {hit.confidence:.2f} below {floor:.2f}"

        log.info("geocode_fallback", to="google", reason=reason)
        try:
            return self.fallback.geocode(query, region_hint)
        except JmpError:
            # Turning the fallback on must never resolve fewer addresses than leaving it off: an unusable
            # Google (no key, quota spent, outage) leaves the weak OSM match in place rather than losing it.
            if weak is not None:
                log.warning("geocode_fallback_unavailable_keeping_primary", confidence=weak.confidence)
                return weak
            raise
