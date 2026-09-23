"""Sunrise/sunset (NOAA simplified algorithm, ±2 min) — used for the night-driving rule (HZ-23)."""

from __future__ import annotations

import math
from datetime import date


def sun_times_utc_hours(d: date, lat: float, lng: float) -> tuple[float, float] | None:
    """Return (sunrise, sunset) in fractional UTC hours, or None for polar day/night."""
    n = d.timetuple().tm_yday
    gamma = 2 * math.pi / 365 * (n - 1)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
                       - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma))
    decl = (0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma)
            - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma)
            - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma))
    lat_r = math.radians(lat)
    cos_ha = math.cos(math.radians(90.833)) / (math.cos(lat_r) * math.cos(decl)) - math.tan(lat_r) * math.tan(decl)
    if cos_ha < -1 or cos_ha > 1:
        return None
    ha = math.degrees(math.acos(cos_ha))
    sunrise_min = 720 - 4 * (lng + ha) - eqtime
    sunset_min = 720 - 4 * (lng - ha) - eqtime
    return sunrise_min / 60.0, sunset_min / 60.0


def local_sun_times(d: date, lat: float, lng: float, utc_offset_h: float = 5.5) -> tuple[float, float] | None:
    t = sun_times_utc_hours(d, lat, lng)
    if t is None:
        return None
    return (t[0] + utc_offset_h) % 24, (t[1] + utc_offset_h) % 24


def fmt_hhmm(hours: float) -> str:
    h = int(hours) % 24
    m = int(round((hours - int(hours)) * 60))
    if m == 60:
        h, m = (h + 1) % 24, 0
    return f"{h:02d}:{m:02d}"
