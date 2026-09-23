"""Inline SVG icon set (replaces the missing emoji glyphs in the master PDF). 24×24 stroke icons."""

from __future__ import annotations

from markupsafe import Markup

_PATHS: dict[str, str] = {
    "route": '<circle cx="6" cy="19" r="2.2"/><circle cx="18" cy="5" r="2.2"/><path d="M8 19h7.5a3.5 3.5 0 0 0 0-7h-7a3.5 3.5 0 0 1 0-7H16"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "pin": '<path d="M12 21s-7-6.2-7-11.5A7 7 0 0 1 19 9.5C19 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>',
    "layers": '<path d="M12 3 3 8l9 5 9-5-9-5z"/><path d="m3 13 9 5 9-5"/>',
    "gauge": '<path d="M4 16a8 8 0 1 1 16 0"/><path d="m12 16 4-5"/>',
    "shield": '<path d="M12 3 5 6v6c0 4.4 3 7.6 7 9 4-1.4 7-4.6 7-9V6l-7-3z"/><path d="m9 12 2 2 4-4"/>',
    "alert": '<path d="M12 3 2 20h20L12 3z"/><path d="M12 10v4"/><circle cx="12" cy="17" r=".6" fill="currentColor"/>',
    "train": '<rect x="6" y="3" width="12" height="13" rx="2"/><path d="M6 11h12M9 20l-2 2M15 20l2 2M8.5 16l-1.5 4h10l-1.5-4"/>',
    "truck": '<path d="M2 6h11v9H2zM13 9h4l3 3v3h-7z"/><circle cx="6" cy="17" r="1.8"/><circle cx="16.5" cy="17" r="1.8"/>',
    "road": '<path d="M8 3 5 21M16 3l3 18M12 4v3M12 10v3M12 16v3"/>',
    "water": '<path d="M12 3s-6 6.5-6 10.5a6 6 0 0 0 12 0C18 9.5 12 3 12 3z"/>',
    "walk": '<circle cx="13" cy="4" r="1.8"/><path d="m10 21 2-6 3 3v3M9 12l2-4 3 1 2 3M11 8l-2 6"/>',
    "city": '<path d="M3 21V9l5-3v15M8 21V4h8v17M16 21v-9h5v9M11 8h2M11 12h2M11 16h2"/>',
    "cloud": '<path d="M7 18a4 4 0 0 1-.5-8 5.5 5.5 0 0 1 10.6 1.5A3.5 3.5 0 0 1 17 18H7z"/><path d="M9 21l1-2M13 21l1-2"/>',
    "phone": '<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z"/>',
    "hospital": '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M12 8v8M8 12h8"/>',
    "police": '<path d="M12 3 4 6v5c0 5 3.5 8.5 8 10 4.5-1.5 8-5 8-10V6l-8-3z"/><path d="m12 8 1.2 2.5 2.8.4-2 2 .5 2.8L12 14.4l-2.5 1.3.5-2.8-2-2 2.8-.4z"/>',
    "fuel": '<path d="M4 21V5a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v16M3 21h12M14 9h2a2 2 0 0 1 2 2v5a1.5 1.5 0 0 0 3 0V8l-3-3"/><path d="M7 7h4v4H7z"/>',
    "wrench": '<path d="M14.5 6.5a4 4 0 0 0 5 5L12 19a2.1 2.1 0 0 1-3-3l7.5-7.5a4 4 0 0 1-2-2z"/>',
    "car": '<path d="M5 16H3v-4l2-5h14l2 5v4h-2"/><circle cx="7.5" cy="16.5" r="2"/><circle cx="16.5" cy="16.5" r="2"/><path d="M9.5 16.5h5M4 12h16"/>',
    "moon": '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>',
    "check": '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
    "flag": '<path d="M5 21V4M5 4h11l-2 4 2 4H5"/>',
    "rest": '<path d="M4 11h16v3a4 4 0 0 1-4 4H8a4 4 0 0 1-4-4v-3zM8 7c0-1.5 1-1.5 1-3M12 7c0-1.5 1-1.5 1-3M20 12h1a2 2 0 0 1 0 4h-1.5"/>',
    "return": '<path d="M9 14 4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>',
    "users": '<circle cx="9" cy="8" r="3"/><path d="M3 20a6 6 0 0 1 12 0M16 11a3 3 0 1 0 0-6M21 20a6 6 0 0 0-4-5.7"/>',
    "bolt": '<path d="M13 2 4 14h7l-1 8 9-12h-7l1-8z"/>',
    "weather": '<path d="M12 3v2M5.6 5.6 7 7M3 12h2M17 7l1.4-1.4M8.5 12.5a4 4 0 1 1 7.4 1.6"/><path d="M7 20h10a3 3 0 0 0 0-6 4 4 0 0 0-7.7 1.3A2.4 2.4 0 0 0 7 20z"/>',
    "list": '<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1.2"/><circle cx="4.5" cy="12" r="1.2"/><circle cx="4.5" cy="18" r="1.2"/>',
}


def icon(name: str, size: int = 12, color: str = "currentColor", stroke: float = 2.0) -> Markup:
    path = _PATHS.get(name, _PATHS["alert"])
    return Markup(
        f'<svg class="ic" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="{color}" '
        f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{path}</svg>'
    )


HAZARD_ICON = {
    "HZ-01": "route", "HZ-02": "road", "HZ-03": "road", "HZ-04": "road", "HZ-05": "road", "HZ-06": "road",
    "HZ-07": "road", "HZ-08": "train", "HZ-09": "route", "HZ-10": "road", "HZ-11": "wrench", "HZ-12": "water",
    "HZ-13": "alert", "HZ-14": "cloud", "HZ-15": "route", "HZ-16": "route", "HZ-17": "alert", "HZ-18": "water",
    "HZ-19": "alert", "HZ-20": "walk", "HZ-21": "road", "HZ-22": "phone", "HZ-23": "moon", "HZ-24": "truck",
    "HZ-25": "road",
}
