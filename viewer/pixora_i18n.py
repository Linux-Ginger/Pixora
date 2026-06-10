#!/usr/bin/env python3
# Pixora — pixora_i18n.py — single gettext bootstrap shared by every entry
# point, so language detection can never drift between modules again.

import gettext
import json
import os

LOCALE_DIR = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "locale"))
SUPPORTED_LANGS = ("nl", "en", "de", "fr")
_SETTINGS_PATH = os.path.expanduser("~/.config/pixora/settings.json")


def detect_system_lang():
    """Map env locale to a supported language. Fallback: en."""
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        val = os.environ.get(var, "")
        if val:
            code = val.split(":")[0].split(".")[0].split("_")[0].lower()
            if code in SUPPORTED_LANGS:
                return code
    return "en"


def _get_language():
    try:
        with open(_SETTINGS_PATH, "r") as f:
            lang = json.load(f).get("language")
    except Exception:
        lang = None
    return lang if lang in SUPPORTED_LANGS else detect_system_lang()


LANG = _get_language()
translation = gettext.translation(
    "pixora", localedir=LOCALE_DIR, languages=[LANG], fallback=True)
_ = translation.gettext
ngettext = translation.ngettext
