"""Internet lookups: weather (Open-Meteo, no API key) and quick web answers.

Only the query itself is sent out. Results are returned to the LLM marked as untrusted.
"""
import datetime
import html
import json
import logging
import re
import urllib.parse
import urllib.request

import securestore

log = logging.getLogger("jarvis.web")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) JARVIS-local-assistant"
MAX_BYTES = 2_000_000


def fetch(url, data=None, timeout=8):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(MAX_BYTES).decode("utf-8", "ignore")


def fetch_json(url):
    return json.loads(fetch(url))


# ---------------------------------------------------------------- weather

WMO = {
    0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers",
    81: "showers", 82: "violent showers", 85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorms", 96: "thunderstorms with hail", 99: "thunderstorms with heavy hail",
}
_geo_cache = {}


def _geocode(place):
    key = place.lower()
    if key not in _geo_cache:
        name = place.split(",")[0].strip()
        data = fetch_json("https://geocoding-api.open-meteo.com/v1/search?count=5&language=en&name="
                          + urllib.parse.quote(name))
        results = data.get("results") or []
        if "," in place:  # "Paris, Texas" -> prefer a matching country/region
            hint = place.split(",", 1)[1].strip().lower()
            results = [r for r in results if hint in (r.get("country", "") + " " + r.get("admin1", "")).lower()] or results
        if not results:
            return None
        r = results[0]
        _geo_cache[key] = (r["latitude"], r["longitude"], f"{r['name']}, {r.get('country', '')}".strip(", "))
    return _geo_cache[key]


def get_weather(location: str = "", days_ahead: int = 0) -> str:
    """Get the current weather and the forecast for a day.

    Args:
        location: City name, optionally with country (e.g. "Paris, France"). Leave empty for the user's home city.
        days_ahead: 0 for today, 1 for tomorrow, up to 6.
    """
    location = location.strip() or securestore.get_setting("home_city")
    if not location:
        return "No location given and no home city is set. Ask the user which city."
    days_ahead = max(0, min(6, int(days_ahead)))
    geo = _geocode(location)
    if not geo:
        return f"Couldn't find a place called {location}."
    lat, lon, name = geo
    data = fetch_json(
        "https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat}&longitude={lon}&timezone=auto&forecast_days=7"
        "&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m"
        "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max")
    d = data["daily"]
    i = days_ahead
    day = datetime.date.fromisoformat(d["time"][i]).strftime("%A")
    out = [f"Weather for {name}."]
    if days_ahead == 0:
        c = data["current"]
        out.append(f"Now: {round(c['temperature_2m'])}°C (feels like {round(c['apparent_temperature'])}°C), "
                   f"{WMO.get(c['weather_code'], 'unknown')}, wind {round(c['wind_speed_10m'])} km/h, "
                   f"humidity {c['relative_humidity_2m']}%.")
    out.append(f"{day}: {WMO.get(d['weather_code'][i], 'unknown')}, high {round(d['temperature_2m_max'][i])}°C, "
               f"low {round(d['temperature_2m_min'][i])}°C, {d['precipitation_probability_max'][i]}% chance of rain.")
    return " ".join(out)


# ---------------------------------------------------------------- web answers

def _strip_tags(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def _duckduckgo(query):
    page = fetch("https://html.duckduckgo.com/html/", data=urllib.parse.urlencode({"q": query}).encode())
    titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', page, re.S)
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', page, re.S)
    return [f"{_strip_tags(t)}: {_strip_tags(s)}" for t, s in zip(titles, snippets)][:6]


def _wikipedia(query):
    hits = fetch_json("https://en.wikipedia.org/w/api.php?action=query&list=search&format=json&srlimit=1&srsearch="
                      + urllib.parse.quote(query))["query"]["search"]
    if not hits:
        return []
    title = hits[0]["title"]
    summary = fetch_json("https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(title))
    return [f"Wikipedia, {title}: {summary.get('extract', '')}"]


def web_answer(question: str) -> str:
    """Look something up on the internet and get text results to answer from. Use for facts,
    news, sports scores, prices, definitions, or anything you don't know or that may have changed.

    Args:
        question: A concise search query.
    """
    results = []
    for source in (_duckduckgo, _wikipedia):
        try:
            results = source(question)
        except Exception as e:
            log.warning("%s failed: %s", source.__name__, e)
        if results:
            break
    if not results:
        return "The web search returned nothing. Tell the user you couldn't find it."
    return "\n".join(f"- {r}" for r in results)[:3000]
