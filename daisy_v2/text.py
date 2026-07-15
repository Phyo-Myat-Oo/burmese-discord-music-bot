from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable


MYANMAR_DIGITS = str.maketrans("၀၁၂၃၄၅၆၇၈၉", "0123456789")
EXTENSION = re.compile(r"\.(mp3|m4a|aac|flac|ogg|opus|wav)$", re.IGNORECASE)
TRACK_PREFIX = re.compile(r"^\s*[0-9၀-၉]{1,3}\s*[.\-_။၊]+\s*")
BITRATE_SUFFIX = re.compile(r"\s*\((?:128|192|256|320)[^)]*\)\s*$", re.IGNORECASE)
ARTIST_SPLIT = re.compile(r"\s*(?:[၊,;/]+|\s+နှင့်\s+|\s+&\s+)\s*")


def clean_title(value: str) -> str:
    value = unicodedata.normalize("NFC", value or "")
    value = EXTENSION.sub("", value)
    value = TRACK_PREFIX.sub("", value)
    return " ".join(value.replace("\u200b", "").split()).strip(" -_")


def clean_album_title(value: str) -> str:
    value = unicodedata.normalize("NFC", value or "")
    value = EXTENSION.sub("", value)
    value = BITRATE_SUFFIX.sub("", value)
    return " ".join(value.replace("\u200b", "").split()).strip(" -_")


def search_key(value: str) -> str:
    value = unicodedata.normalize("NFC", value or "").translate(MYANMAR_DIGITS).casefold()
    return "".join(char for char in value if unicodedata.category(char)[0] in {"L", "M", "N"})


def extract_artists(album_title: str) -> list[str]:
    title = clean_album_title(album_title)
    if " - " not in title:
        return []
    prefix = title.split(" - ", 1)[0].strip()
    return list(dict.fromkeys(part.strip() for part in ARTIST_SPLIT.split(prefix) if part.strip()))


def display_duration(seconds: int | None) -> str:
    if seconds is None or seconds < 0:
        return "Unknown"
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def truncate(value: object, limit: int) -> str:
    """Keep user/catalogue text within Discord's field and embed limits."""
    text = str(value or "")
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1] + "…"


def limited_lines(lines: Iterable[str], *, limit: int = 3_900, empty: str = "") -> str:
    """Join lines without exceeding Discord's 4096-character description limit."""
    output: list[str] = []
    used = 0
    for line in lines:
        line = str(line)
        separator = 1 if output else 0
        remaining = limit - used - separator
        if remaining <= 0:
            break
        if len(line) > remaining:
            if remaining > 1:
                output.append(truncate(line, remaining))
            break
        output.append(line)
        used += separator + len(line)
    return "\n".join(output) or empty
