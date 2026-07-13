from __future__ import annotations

import re
import unicodedata

MYANMAR_DIGITS = str.maketrans("၀၁၂၃၄၅၆၇၈၉", "0123456789")
EXTENSION = re.compile(r"\.(mp3|m4a|aac|flac|ogg|opus|wav)$", re.IGNORECASE)
TRACK_PREFIX = re.compile(r"^\s*[0-9၀-၉]{1,3}\s*[.\-_၊။]+\s*")
BITRATE_SUFFIX = re.compile(r"\s*\((?:128|192|256|320)[^)]*\)\s*$", re.IGNORECASE)


def clean_track_title(value: str) -> str:
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
    return "".join(character for character in value if unicodedata.category(character)[0] in {"L", "M", "N"})
