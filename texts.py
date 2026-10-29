"""Small UI texts used inside calendar events (language follows HA language)."""

from __future__ import annotations

TEXTS = {
    "uk": {
        "outage": "Відключення",
        "possible": "Можливе відключення",
        "emergency": "Екстрене відключення",
        "no_power": "Світла немає",
        "ongoing": "триває",
        "duration": "Тривалість",
        "h": "год",
        "min": "хв",
        "before": "було",
        "after": "стало",
        "none": "без відключень",
        "no_data": "немає даних",
    },
    "en": {
        "outage": "Outage",
        "possible": "Possible outage",
        "emergency": "Emergency outage",
        "no_power": "No power",
        "ongoing": "ongoing",
        "duration": "Duration",
        "h": "h",
        "min": "min",
        "before": "was",
        "after": "now",
        "none": "no outages",
        "no_data": "no data",
    },
}


def texts(language: str | None) -> dict[str, str]:
    return TEXTS["uk" if (language or "").lower().startswith("uk") else "en"]


def fmt_duration(minutes: float, t: dict[str, str]) -> str:
    total = int(round(minutes))
    hours, mins = divmod(total, 60)
    if hours:
        return f"{hours} {t['h']} {mins} {t['min']}"
    return f"{mins} {t['min']}"


def fmt_ranges(ranges: list[tuple[int, int, str]] | None, t: dict[str, str]) -> str:
    if ranges is None:
        return t["no_data"]
    if not ranges:
        return t["none"]
    parts = []
    for start, end, kind in ranges:
        text = f"{start // 60:02d}:{start % 60:02d}–{end // 60:02d}:{end % 60:02d}"
        if kind == "possible":
            text += f" ({t['possible'].lower()})"
        elif kind == "emergency":
            text += f" ({t['emergency'].lower()})"
        parts.append(text)
    return ", ".join(parts)
