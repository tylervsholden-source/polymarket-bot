"""
Market Classifier — Piyasa sorusunu okuyup kategori + alt kategori belirler.
Harici API çağrısı yok, tamamen regex/keyword tabanlı, senkron.
"""
from __future__ import annotations

import re

# (pattern, category, subcategory)
_RULES: list[tuple[str, str, str]] = [
    # ── SPORTS ──────────────────────────────────────────────────────────────
    (r"\b(nba|nfl|nhl|mlb|mls|ufc|ncaa)\b", "SPORTS", "LEAGUE"),
    (r"\b(lakers|celtics|warriors|bulls|heat|nets|knicks|bucks|suns|nuggets|"
     r"clippers|76ers|raptors|mavericks|hawks|hornets|pelicans|spurs|rockets|"
     r"grizzlies|jazz|thunder|trail blazers|kings|timberwolves|magic|pistons|"
     r"cavaliers|pacers|wizards|blazers)\b", "SPORTS", "NBA"),
    (r"\b(patriots|cowboys|chiefs|49ers|eagles|packers|bears|giants|jets|"
     r"ravens|steelers|broncos|chargers|raiders|colts|titans|texans|jaguars|"
     r"dolphins|bills|bengals|browns|lions|vikings|saints|falcons|panthers|"
     r"buccaneers|seahawks|rams|cardinals)\b", "SPORTS", "NFL"),
    (r"\b(real madrid|barcelona|manchester|arsenal|chelsea|liverpool|"
     r"juventus|psg|bayern|inter|milan|ajax|dortmund|"
     r"galatasaray|fenerbah[cç]e|be[sş]ikta[sş]|trabzonspor|ba[sş]ak[sş]ehir|"
     r"s[üu]per lig|champions league|europa league|premier league|la liga|"
     r"bundesliga|serie a|ligue 1)\b", "SPORTS", "FOOTBALL"),
    (r"\b(points? (over|under|o/u)|rebounds?|assists?|touchdowns?|"
     r"goals? scored|wins? the (game|match|series))\b", "SPORTS", "PLAYER_PROP"),
    (r"\bsuper bowl\b", "SPORTS", "NFL"),
    (r"\bworld cup\b", "SPORTS", "FOOTBALL"),
    (r"\b(wimbledon|us open|french open|australian open|atp|wta)\b", "SPORTS", "TENNIS"),
    (r"\b(formula 1|f1|grand prix|monaco gp)\b", "SPORTS", "MOTORSPORTS"),

    # ── WEATHER ─────────────────────────────────────────────────────────────
    (r"\b(temperature|celsius|fahrenheit|°[cf]|\d+°)\b", "WEATHER", "TEMPERATURE"),
    (r"\b(rain|rainfall|precipitation|snow|snowfall|blizzard)\b", "WEATHER", "PRECIPITATION"),
    (r"\b(hurricane|typhoon|tropical storm|cyclone|tornado)\b", "WEATHER", "STORM"),
    (r"\b(highest temperature|low temperature|record (high|low))\b", "WEATHER", "TEMPERATURE"),
    (r"\b(weather|forecast|climate)\b", "WEATHER", "GENERAL"),

    # ── CRYPTO / FINANCE ────────────────────────────────────────────────────
    (r"\b(bitcoin|btc|ethereum|eth|solana|sol|xrp|bnb|crypto)\b", "CRYPTO", "PRICE"),
    (r"\b(s&p 500|sp500|nasdaq|dow jones|stock market|fed rate|interest rate|"
     r"inflation|gdp|unemployment|nikkei|dax|hang seng|shanghai|cac 40|ftse|"
     r"nifty|sensex|kospi|asx 200)\b", "FINANCE", "MACRO"),
    (r"\b(ipo|merger|acquisition|bankruptcy|earnings)\b", "FINANCE", "CORPORATE"),

    # ── POLITICS ────────────────────────────────────────────────────────────
    (r"\b(election|president|senate|congress|parliament|prime minister|"
     r"vote|polling|ballot|democrat|republican|conservative|labour)\b",
     "POLITICS", "ELECTION"),
    (r"\b(trump|biden|harris|macron|sunak|scholz|modi|xi jinping|putin)\b",
     "POLITICS", "LEADER"),
    (r"\b(bill|legislation|law passed|supreme court|impeach)\b",
     "POLITICS", "POLICY"),

    # ── GEOPOLITICS ─────────────────────────────────────────────────────────
    (r"\b(war|conflict|ceasefire|invasion|military|nato|sanction)\b",
     "GEOPOLITICS", "CONFLICT"),
    (r"\b(nuclear|missile|drone|airstr[ike]+)\b", "GEOPOLITICS", "MILITARY"),

    # ── ENTERTAINMENT ───────────────────────────────────────────────────────
    (r"\b(oscar|emmy|grammy|golden globe|academy award)\b", "ENTERTAINMENT", "AWARDS"),
    (r"\b(box office|opening weekend|film|movie|series|season)\b",
     "ENTERTAINMENT", "MEDIA"),

    # ── SCIENCE / TECH ──────────────────────────────────────────────────────
    (r"\b(spacex|nasa|rocket|launch|orbit|moon|mars)\b", "SCIENCE", "SPACE"),
    (r"\b(ai|artificial intelligence|gpt|llm|openai|anthropic|model release)\b",
     "TECH", "AI"),
    (r"\b(iphone|apple|google|microsoft|meta|amazon|tesla)\b", "TECH", "CORPORATE"),
]

# Compile once
_COMPILED = [(re.compile(pat, re.IGNORECASE), cat, sub) for pat, cat, sub in _RULES]


def classify(question: str) -> dict:
    """
    Returns:
        {"category": "SPORTS", "subcategory": "NBA", "tags": [...]}
    Falls back to GENERAL/UNKNOWN if no rule matches.
    """
    tags: list[str] = []
    category = "GENERAL"
    subcategory = "UNKNOWN"

    for pattern, cat, sub in _COMPILED:
        if pattern.search(question):
            tags.append(sub)
            if category == "GENERAL":  # İlk eşleşen kazanır
                category = cat
                subcategory = sub

    return {
        "category": category,
        "subcategory": subcategory,
        "tags": list(dict.fromkeys(tags)),  # deduplicate, preserve order
    }
