"""
============================================================
ANAK KRAKATAU 2026 - ADAPTIVE GOVERNMENT SOURCE SCRAPER
Data analysis & dashboard by Kelvin Irawan
============================================================

PURPOSE
-------
This version removes fixed article URLs. Instead, it discovers relevant,
recent content from official government domains using keywords and the site's
own discovery mechanisms:

1. robots.txt -> sitemap links
2. sitemap.xml / sitemap index
3. homepage / official listing pages
4. GET-based internal search forms when available
5. relevant hyperlinks found on official pages

Candidate pages are ranked by:
- keyword relevance
- source/role relevance
- publication/update date
- exact phrase matches

The scraper then extracts dashboard metrics from the best matching pages.
If a current run cannot find a metric, it carries forward the previous JSON
value instead of silently using a hard-coded historical number. Carried-forward
fields are explicitly recorded in metadata.data_quality.

OUTPUTS
-------
1. data/anak_krakatau_2026.json
2. data/impacted_areas.geojson

DEPENDENCIES
------------
requests
beautifulsoup4

OPTIONAL
--------
lxml is not required. XML is parsed with the Python standard library.

USAGE
-----
python scrape_anak_krakatau_2026.py
python scrape_anak_krakatau_2026.py --days-back 45 --max-pages 20
python scrape_anak_krakatau_2026.py --extra-keywords "lahar,abu vulkanik"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import unescape
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------------------------------------------------------------------------
# PATHS / RUNTIME
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
if (SCRIPT_DIR / "data").exists() or (SCRIPT_DIR / "index.html").exists():
    PROJECT_DIR = SCRIPT_DIR
elif (SCRIPT_DIR.parent / "data").exists():
    PROJECT_DIR = SCRIPT_DIR.parent
else:
    PROJECT_DIR = SCRIPT_DIR

DATA_DIR = PROJECT_DIR / "data"
OUTPUT_JSON = DATA_DIR / "anak_krakatau_2026.json"
OUTPUT_GEOJSON = DATA_DIR / "impacted_areas.geojson"
DISCOVERY_LOG = DATA_DIR / "discovery_log.json"

WIB = ZoneInfo("Asia/Jakarta")
TIMEOUT = 30
DEFAULT_DAYS_BACK = 120
DEFAULT_MAX_PAGES = 18
DEFAULT_MAX_CANDIDATES = 24
DEFAULT_SITEMAP_LIMIT = 4000

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0 Safari/537.36 AnakKrakatauDashboard/2.0"
    ),
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.7",
}

# ---------------------------------------------------------------------------
# ADJUSTABLE KEYWORD BANK
# ---------------------------------------------------------------------------

CORE_KEYWORDS = [
    "anak krakatau",
    "gunung anak krakatau",
    "g. anak krakatau",
    "krakatau",
]

EVENT_KEYWORDS = [
    "erupsi",
    "erupsi menerus",
    "aktivitas gunungapi",
    "aktivitas gunung api",
    "abu vulkanik",
    "kolom erupsi",
    "kolom abu",
    "lava fountain",
    "strombolian",
    "kegempaan",
    "deformasi",
    "status aktivitas",
    "tingkat aktivitas",
]

ROLE_KEYWORDS = {
    "status": [
        "level ii",
        "level 2",
        "waspada",
        "level iii",
        "level 3",
        "siaga",
        "tingkat aktivitas",
        "status aktivitas",
        "diturunkan",
        "dinaikkan",
        "rekomendasi",
    ],
    "aviation": [
        "bandara",
        "penerbangan",
        "flight",
        "passenger",
        "penumpang",
        "abu vulkanik",
        "ash",
        "ditutup",
        "closure",
        "rute",
        "route",
        "airport",
    ],
    "health": [
        "rumah sakit",
        "hospital",
        "puskesmas",
        "ispa",
        "respiratory",
        "kesehatan",
        "health",
        "masker",
        "penduduk",
        "jiwa",
        "orang",
    ],
    "impact": [
        "terdampak",
        "dampak",
        "affected",
        "evakuasi",
        "pengungsi",
        "wilayah",
        "provinsi",
        "kabupaten",
        "kota",
    ],
}

# Optional terms to expand during a run. The scraper always remains
# anchored to the core Anak Krakatau topic so it does not drift to unrelated
# volcanoes or generic disaster news.
PROVINCES = [
    "Lampung",
    "Banten",
    "DKI Jakarta",
    "Jawa Barat",
    "Bengkulu",
    "Sumatera Selatan",
    "Sulawesi",
    "Jawa Tengah",
    "Jawa Timur",
]

# ---------------------------------------------------------------------------
# OFFICIAL GOVERNMENT SOURCES
# ---------------------------------------------------------------------------
#
# These are domain roots and generic landing pages, NOT article URLs.
# Change/add domains here if an institution changes its web platform.
# The actual article URL is discovered at runtime.

SOURCES = {
    "geologi": {
        "name": "Kementerian ESDM / Badan Geologi",
        "organization": "Badan Geologi",
        "domains": [
            "https://geologi.esdm.go.id",
            "https://www.esdm.go.id",
            "https://vsi.esdm.go.id",
            "https://magma.esdm.go.id",
        ],
        "seed_paths": [
            "/",
            "/media-center",
            "/media-center/arsip-berita",
            "/index.php/media-center",
            "/index.php/media-center/arsip-berita",
        ],
        "roles": ["status", "impact"],
        "priority": 10,
    },
    "bnpb": {
        "name": "Badan Nasional Penanggulangan Bencana (BNPB)",
        "organization": "BNPB",
        "domains": ["https://bnpb.go.id", "https://www.bnpb.go.id"],
        "seed_paths": ["/", "/berita", "/press-release"],
        "roles": ["impact", "health"],
        "priority": 9,
    },
    "bmkg": {
        "name": "Badan Meteorologi, Klimatologi, dan Geofisika (BMKG)",
        "organization": "BMKG",
        "domains": [
            "https://www.bmkg.go.id",
            "https://bbmkg2.bmkg.go.id",
        ],
        "seed_paths": ["/", "/siaran-pers", "/berita"],
        "roles": ["aviation", "impact"],
        "priority": 9,
    },
    "kemenhub": {
        "name": "Kementerian Perhubungan RI",
        "organization": "Kementerian Perhubungan",
        "domains": [
            "https://www.kemenhub.go.id",
            "https://dephub.go.id",
        ],
        "seed_paths": ["/", "/post", "/post/read", "/berita"],
        "roles": ["aviation", "impact"],
        "priority": 9,
    },
    "kemenkes": {
        "name": "Kementerian Kesehatan RI",
        "organization": "Kementerian Kesehatan",
        "domains": [
            "https://www.kemkes.go.id",
            "https://kemkes.go.id",
        ],
        "seed_paths": ["/", "/id", "/eng"],
        "roles": ["health", "impact"],
        "priority": 9,
    },
    "nadma": {
        "name": "NADMA Malaysia / MetMalaysia",
        "organization": "NADMA / MetMalaysia",
        "domains": [
            "https://www.nadma.gov.my",
            "https://www.met.gov.my",
        ],
        "seed_paths": ["/", "/bi/media-en/news", "/media-en/news"],
        "roles": ["impact"],
        "priority": 6,
    },
}

# Airport metadata is geographic/static reference data rather than a scraped
# metric. Impact labels can be refreshed later without inventing coordinates.
AIRPORTS = [
    {"name": "Soekarno-Hatta International Airport", "code": "CGK", "province": "Banten", "country": "Indonesia", "lat": -6.1256, "lng": 106.6559},
    {"name": "Halim Perdanakusuma Airport", "code": "HLP", "province": "DKI Jakarta", "country": "Indonesia", "lat": -6.2666, "lng": 106.8900},
    {"name": "Radin Inten II Airport", "code": "TKG", "province": "Lampung", "country": "Indonesia", "lat": -5.2427, "lng": 105.1751},
    {"name": "Budiarto Airport", "code": "RTO", "province": "Banten", "country": "Indonesia", "lat": -6.2930, "lng": 106.5690},
    {"name": "Pondok Cabe Airport", "code": "PCB", "province": "Banten", "country": "Indonesia", "lat": -6.3369, "lng": 106.7640},
    {"name": "Husein Sastranegara Airport", "code": "BDO", "province": "West Java", "country": "Indonesia", "lat": -6.9006, "lng": 107.5764},
    {"name": "Muhammad Taufiq Kiemas Airport", "code": "TNB", "province": "Lampung", "country": "Indonesia", "lat": -5.2110, "lng": 105.1630},
    {"name": "Atung Bungsu Airport", "code": "PXA", "province": "South Sumatra", "country": "Indonesia", "lat": -4.0330, "lng": 103.3950},
    {"name": "Singapore Changi Airport", "code": "SIN", "province": "Singapore", "country": "Singapore", "lat": 1.3644, "lng": 103.9915, "regional": True},
    {"name": "Kuala Lumpur International Airport", "code": "KUL", "province": "Malaysia", "country": "Malaysia", "lat": 2.7456, "lng": 101.7099, "regional": True},
]

# Keep the original analytical polygons exactly as a static reference. They
# are intentionally not presented as scraped official boundaries.
GEOJSON = {
    "type": "FeatureCollection",
    "name": "Anak Krakatau 2026 Approximate Ash Coverage",
    "features": [
        {
            "type": "Feature",
            "properties": {
                "name": "Reported ash-affected corridor — Indonesia",
                "coverage_type": "ash_affected",
                "regions": ["Bengkulu", "Lampung", "Banten", "DKI Jakarta", "Jawa Barat"],
                "note": "Approximate analytical corridor based on reported volcanic-ash impact. It is not an official administrative or concentration boundary.",
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [103.65, -5.02], [104.20, -5.12], [104.65, -5.32], [105.15, -5.22],
                    [105.62, -5.38], [106.02, -5.48], [106.38, -5.60], [106.74, -5.83],
                    [107.02, -6.13], [106.88, -6.42], [106.46, -6.58], [105.92, -6.62],
                    [105.42, -6.52], [104.96, -6.66], [104.54, -6.42], [104.10, -6.18],
                    [103.82, -5.78], [103.65, -5.02],
                ]]
            },
        },
        {
            "type": "Feature",
            "properties": {
                "name": "Higher-level ash monitoring plume — offshore",
                "coverage_type": "higher_level_monitoring",
                "note": "Approximate offshore monitoring corridor consistent with higher-level ash observations reported by BMKG; not an official ash concentration boundary.",
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [98.85, -4.22], [99.72, -4.32], [100.58, -4.55], [101.42, -4.68],
                    [102.22, -4.88], [102.98, -5.12], [103.60, -5.42], [103.74, -5.82],
                    [103.38, -6.20], [102.76, -6.53], [101.92, -6.62], [101.06, -6.42],
                    [100.20, -6.12], [99.46, -5.60], [99.02, -4.92], [98.85, -4.22],
                ]]
            },
        },
    ],
}

# ---------------------------------------------------------------------------
# DATA STRUCTURES
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    source_key: str
    organization: str
    url: str
    title: str
    date: Optional[datetime]
    text: str
    html: str
    discovery_method: str
    relevance_score: float = 0.0
    role_score: Dict[str, float] | None = None

    def as_log(self) -> dict:
        return {
            "source_key": self.source_key,
            "organization": self.organization,
            "url": self.url,
            "title": self.title,
            "date": self.date.strftime("%Y-%m-%d") if self.date else None,
            "discovery_method": self.discovery_method,
            "relevance_score": round(self.relevance_score, 2),
            "role_score": {k: round(v, 2) for k, v in (self.role_score or {}).items()},
        }

# ---------------------------------------------------------------------------
# HTTP HELPERS
# ---------------------------------------------------------------------------

SESSION = requests.Session()
RETRY = Retry(
    total=3,
    backoff_factor=0.6,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
    raise_on_status=False,
)
SESSION.mount("http://", HTTPAdapter(max_retries=RETRY, pool_connections=10, pool_maxsize=10))
SESSION.mount("https://", HTTPAdapter(max_retries=RETRY, pool_connections=10, pool_maxsize=10))
SESSION.headers.update(HEADERS)


def now_dt() -> datetime:
    return datetime.now(WIB)


def now_iso() -> str:
    return now_dt().isoformat(timespec="seconds")


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or " ").strip()


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    # Preserve query parameters for internal search URLs, but remove fragments.
    clean = parsed._replace(fragment="")
    return urlunparse(clean)


def same_domain(url: str, allowed_domains: Sequence[str]) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == (urlparse(d).hostname or "").lower() or host.endswith("." + (urlparse(d).hostname or "").lower()) for d in allowed_domains)


def is_html_candidate(url: str) -> bool:
    path = (urlparse(url).path or "").lower()
    bad_ext = (
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar", ".7z",
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".mp4", ".mp3",
    )
    return not path.endswith(bad_ext)


def fetch(url: str, *, allow_non_html: bool = False) -> Tuple[str, bool, int, str, str]:
    try:
        response = SESSION.get(url, timeout=TIMEOUT, allow_redirects=True)
        status = response.status_code
        final_url = response.url
        content_type = response.headers.get("content-type", "")
        if not response.ok:
            print(f"  ERROR HTTP {status} -> {final_url}")
            return "", False, status, final_url, content_type
        if not allow_non_html and "html" not in content_type.lower() and "xml" not in content_type.lower():
            print(f"  SKIP non-HTML {content_type} -> {final_url}")
            return "", False, status, final_url, content_type
        text = response.text
        print(f"  OK HTTP {status} -> {final_url}")
        return text, True, status, final_url, content_type
    except requests.RequestException as exc:
        print(f"  ERROR {exc}")
        return "", False, 0, url, ""

# ---------------------------------------------------------------------------
# TEXT / DATE / NUMBER PARSING
# ---------------------------------------------------------------------------

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "januari": 1, "februari": 2, "maret": 3, "april": 4,
    "mei": 5, "juni": 6, "juli": 7, "agustus": 8,
    "september": 9, "oktober": 10, "november": 11, "desember": 12,
}

MONTH_PATTERN = r"(January|February|March|April|May|June|July|August|September|October|November|December|Januari|Februari|Maret|April|Mei|Juni|Juli|Agustus|September|Oktober|November|Desember)"


def html_to_text(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer"]):
        tag.decompose()
    return normalize_space(soup.get_text(" ", strip=True))


def extract_title(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    h1 = soup.find("h1")
    if h1 and normalize_space(h1.get_text(" ", strip=True)):
        return normalize_space(h1.get_text(" ", strip=True))
    return normalize_space(soup.title.get_text(" ", strip=True)) if soup.title else ""


def parse_iso_date(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    value = raw.strip()
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo:
            dt = dt.astimezone(WIB).replace(tzinfo=None)
        return dt
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value[:10], fmt)
        except ValueError:
            continue
    return None


def extract_date_from_text(text: str, *, limit_chars: int = 2600) -> Optional[datetime]:
    sample = (text or "")[:limit_chars]
    values: List[datetime] = []

    iso_matches = re.findall(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", sample)
    for y, m, d in iso_matches:
        try:
            values.append(datetime(int(y), int(m), int(d)))
        except ValueError:
            pass

    pat = rf"\b(\d{{1,2}})\s+{MONTH_PATTERN}\s+(20\d{{2}})\b"
    for d, month_name, y in re.findall(pat, sample, re.I):
        try:
            month = MONTHS[month_name.lower()]
            values.append(datetime(int(y), month, int(d)))
        except (KeyError, ValueError):
            pass

    # English month-first forms occasionally appear on bilingual government pages.
    pat2 = rf"\b{MONTH_PATTERN}\s+(\d{{1,2}}),?\s+(20\d{{2}})\b"
    for month_name, d, y in re.findall(pat2, sample, re.I):
        try:
            month = MONTHS[month_name.lower()]
            values.append(datetime(int(y), month, int(d)))
        except (KeyError, ValueError):
            pass

    return max(values) if values else None


def extract_article_date(html: str, text: str) -> Optional[datetime]:
    soup = BeautifulSoup(html or "", "html.parser")

    # JSON-LD is common on government CMS pages.
    for script in soup.find_all("script", attrs={"type": re.compile(r"application/ld\+json", re.I)}):
        raw = script.get_text(" ", strip=True)
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        objects = obj if isinstance(obj, list) else [obj]
        for item in objects:
            if not isinstance(item, dict):
                continue
            for key in ("datePublished", "dateModified", "uploadDate"):
                dt = parse_iso_date(str(item.get(key, "")))
                if dt:
                    return dt

    # OpenGraph / article metadata.
    for attr, name in (("property", "article:published_time"), ("property", "article:modified_time"), ("name", "date"), ("name", "publishdate")):
        node = soup.find("meta", attrs={attr: re.compile(rf"^{re.escape(name)}$", re.I)})
        if node and node.get("content"):
            dt = parse_iso_date(node.get("content", ""))
            if dt:
                return dt

    # <time datetime=...> is another reliable source.
    for time_tag in soup.find_all("time")[:10]:
        dt = parse_iso_date(time_tag.get("datetime", "")) or extract_date_from_text(time_tag.get_text(" ", strip=True))
        if dt:
            return dt

    # Finally inspect the beginning of the page text.
    return extract_date_from_text(text)


def parse_human_number(value: str) -> Optional[float]:
    """Parse Indonesian/English number formats conservatively."""
    if value is None:
        return None
    raw = str(value).strip().replace(" ", "")
    if not raw:
        return None

    # Decimal with both separators: decide separator by the last occurrence.
    if "." in raw and "," in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        parts = raw.split(",")
        if len(parts) == 2 and len(parts[-1]) <= 2:
            raw = raw.replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "." in raw:
        parts = raw.split(".")
        if len(parts) == 2 and len(parts[-1]) <= 2:
            pass
        else:
            raw = raw.replace(".", "")

    raw = re.sub(r"[^0-9.-]", "", raw)
    try:
        return float(raw)
    except ValueError:
        return None


def parse_number(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    number = parse_human_number(value)
    return int(round(number)) if number is not None else None


def find_number(patterns: Sequence[str], text: str) -> Optional[int]:
    for pattern in patterns:
        match = re.search(pattern, text or "", re.I | re.S)
        if match:
            number = parse_number(match.group(1))
            if number is not None:
                return number
    return None


def first_number_with_context(text: str, keyword_patterns: Sequence[str], radius: int = 220) -> Optional[int]:
    lower = (text or "").lower()
    for keyword in keyword_patterns:
        match = re.search(keyword, lower, re.I)
        if not match:
            continue
        window = text[max(0, match.start() - radius): match.end() + radius]
        number_match = re.search(r"(?<!\w)([\d.,]+)(?!\w)", window)
        if number_match:
            n = parse_number(number_match.group(1))
            if n is not None:
                return n
    return None

# ---------------------------------------------------------------------------
# DISCOVERY
# ---------------------------------------------------------------------------


def tokenize_terms(terms: Sequence[str]) -> List[str]:
    out: List[str] = []
    for term in terms:
        term = normalize_space(term.lower())
        if not term:
            continue
        out.append(term)
        for token in re.findall(r"[a-z0-9-]+", term):
            if len(token) >= 4:
                out.append(token)
    return list(dict.fromkeys(out))


def url_score(url: str, anchor_text: str, keywords: Sequence[str]) -> float:
    hay = normalize_space(f"{url} {anchor_text}").lower()
    score = 0.0
    for term in keywords:
        if term.lower() in hay:
            score += 12.0 if " " in term else 4.0
    if re.search(r"20\d{2}", url):
        score += 1.0
    return score


def parse_robots_sitemaps(base_url: str) -> List[str]:
    robots_url = urljoin(base_url, "/robots.txt")
    text, ok, _, _, _ = fetch(robots_url, allow_non_html=True)
    if not ok or not text:
        return []
    sitemaps = []
    for line in text.splitlines():
        if line.lower().startswith("sitemap:"):
            value = line.split(":", 1)[1].strip()
            if value:
                sitemaps.append(value)
    return list(dict.fromkeys(sitemaps))


def parse_sitemap_document(url: str, visited: Set[str], max_urls: int) -> List[str]:
    if url in visited or len(visited) > 30:
        return []
    visited.add(url)
    text, ok, _, final_url, content_type = fetch(url, allow_non_html=True)
    if not ok or not text:
        return []
    if "xml" not in content_type.lower() and not text.lstrip().startswith("<"):
        return []

    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        # Some servers serve malformed XML-ish content. Fall back to regex.
        locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", text, re.I | re.S)
        return [normalize_url(unescape(x.strip())) for x in locs[:max_urls]]

    tag = root.tag.rsplit("}", 1)[-1].lower()
    locs: List[str] = []
    if tag == "sitemapindex":
        child_sitemaps = []
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1].lower() == "loc" and element.text:
                child_sitemaps.append(element.text.strip())
        for child in child_sitemaps[:20]:
            locs.extend(parse_sitemap_document(child, visited, max_urls - len(locs)))
            if len(locs) >= max_urls:
                break
    else:
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1].lower() == "loc" and element.text:
                locs.append(element.text.strip())
                if len(locs) >= max_urls:
                    break
    return [normalize_url(unescape(x)) for x in locs[:max_urls] if x]


def discover_internal_search_urls(
    html: str,
    page_url: str,
    query: str,
    allowed_domains: Sequence[str],
) -> List[str]:
    """Use only GET-based search forms discovered from the site itself."""
    soup = BeautifulSoup(html or "", "html.parser")
    found: List[str] = []
    for form in soup.find_all("form")[:50]:
        method = (form.get("method") or "get").lower()
        if method != "get":
            continue
        inputs = form.find_all(["input", "textarea", "select"])
        search_input = None
        for input_tag in inputs:
            name = (input_tag.get("name") or "").lower()
            typ = (input_tag.get("type") or "text").lower()
            placeholder = (input_tag.get("placeholder") or "").lower()
            if typ in {"search", "text"} and name in {"q", "query", "keyword", "search", "s", "term", "searchterm"}:
                search_input = input_tag
                break
            if any(k in placeholder for k in ("search", "cari", "kata kunci", "keyword")) and name:
                search_input = input_tag
                break
        if search_input is None:
            continue

        action = urljoin(page_url, form.get("action") or page_url)
        if not same_domain(action, allowed_domains):
            continue

        params = dict(parse_qsl(urlparse(action).query, keep_blank_values=True))
        params[search_input.get("name")] = query
        target = urlunparse(urlparse(action)._replace(query=urlencode(params)))
        found.append(normalize_url(target))
    return list(dict.fromkeys(found))


def extract_links(html: str, page_url: str, allowed_domains: Sequence[str], keywords: Sequence[str]) -> List[Tuple[str, str, float]]:
    soup = BeautifulSoup(html or "", "html.parser")
    links: List[Tuple[str, str, float]] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "").strip()
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        absolute = normalize_url(urljoin(page_url, href))
        if not same_domain(absolute, allowed_domains):
            continue
        if not is_html_candidate(absolute):
            continue
        label = normalize_space(anchor.get_text(" ", strip=True))
        score = url_score(absolute, label, keywords)
        links.append((absolute, label, score))
    return links


def page_navigation_links(html: str, page_url: str, allowed_domains: Sequence[str]) -> List[str]:
    soup = BeautifulSoup(html or "", "html.parser")
    out: List[str] = []
    for anchor in soup.find_all("a", href=True):
        label = normalize_space(anchor.get_text(" ", strip=True)).lower()
        rel = " ".join(anchor.get("rel", [])).lower()
        if rel == "next" or label in {"next", "older", "selanjutnya", "berikutnya", "›", ">"} or "selanjutnya" in label:
            target = normalize_url(urljoin(page_url, anchor.get("href", "")))
            if same_domain(target, allowed_domains) and is_html_candidate(target):
                out.append(target)
    return list(dict.fromkeys(out))[:3]


def discovery_seed_urls(source_cfg: dict) -> List[Tuple[str, str]]:
    result: List[Tuple[str, str]] = []
    for domain in source_cfg["domains"]:
        result.append((domain, "domain_home"))
        for path in source_cfg.get("seed_paths", []):
            result.append((urljoin(domain, path), "configured_listing"))
    return list(dict.fromkeys(result))


def rank_candidate_urls(urls: Iterable[Tuple[str, str, float]], limit: int) -> List[Tuple[str, str, float]]:
    by_url: Dict[str, Tuple[str, str, float]] = {}
    for url, label, score in urls:
        if url not in by_url or score > by_url[url][2]:
            by_url[url] = (url, label, score)
    return sorted(by_url.values(), key=lambda x: (-x[2], len(x[0])))[:limit]


def discover_source(source_key: str, source_cfg: dict, search_terms: Sequence[str], max_pages: int, sitemap_limit: int) -> Tuple[List[Candidate], dict]:
    print(f"\n=== DISCOVERING {source_cfg['name']} ===")
    allowed_domains = source_cfg["domains"]
    discovery_urls = discovery_seed_urls(source_cfg)
    discovered_search_urls: List[str] = []
    raw_link_candidates: List[Tuple[str, str, float]] = []
    method_by_url: Dict[str, str] = {}
    fetched_listing: Set[str] = set()

    # 1) Fetch domain roots / configured listing pages and inspect internal links + search forms.
    for seed_url, method in discovery_urls:
        if seed_url in fetched_listing:
            continue
        if not same_domain(seed_url, allowed_domains):
            continue
        html, ok, _, final_url, _ = fetch(seed_url)
        if not ok:
            continue
        fetched_listing.add(seed_url)
        fetched_listing.add(final_url)
        method_by_url[final_url] = method
        raw_link_candidates.extend(extract_links(html, final_url, allowed_domains, search_terms))
        discovered_search_urls.extend(
            discover_internal_search_urls(html, final_url, "Anak Krakatau", allowed_domains)
        )
        discovered_search_urls.extend(
            discover_internal_search_urls(html, final_url, "Gunung Anak Krakatau", allowed_domains)
        )
        # Only follow a small number of official pagination links.
        for next_url in page_navigation_links(html, final_url, allowed_domains)[:2]:
            if next_url in fetched_listing:
                continue
            page_html, page_ok, _, page_final, _ = fetch(next_url)
            if not page_ok:
                continue
            fetched_listing.add(next_url)
            fetched_listing.add(page_final)
            raw_link_candidates.extend(extract_links(page_html, page_final, allowed_domains, search_terms))
        time.sleep(0.15)

    # 2) Search forms discovered from the official site.
    for search_url in list(dict.fromkeys(discovered_search_urls))[:8]:
        html, ok, _, final_url, _ = fetch(search_url)
        if not ok:
            continue
        method_by_url[final_url] = "internal_site_search"
        raw_link_candidates.extend(extract_links(html, final_url, allowed_domains, search_terms))
        time.sleep(0.15)

    # 3) robots.txt -> sitemap(s), then common sitemap fallbacks.
    sitemap_urls: List[str] = []
    for domain in source_cfg["domains"]:
        sitemap_urls.extend(parse_robots_sitemaps(domain))
        sitemap_urls.extend([
            urljoin(domain, "/sitemap.xml"),
            urljoin(domain, "/sitemap_index.xml"),
            urljoin(domain, "/sitemap-index.xml"),
        ])
    sitemap_urls = list(dict.fromkeys(sitemap_urls))

    sitemap_page_urls: List[str] = []
    visited_sitemaps: Set[str] = set()
    for sitemap_url in sitemap_urls[:15]:
        sitemap_urls_found = parse_sitemap_document(sitemap_url, visited_sitemaps, sitemap_limit)
        sitemap_page_urls.extend(sitemap_urls_found)
        if len(sitemap_page_urls) >= sitemap_limit:
            break

    tokenized_search = tokenize_terms(search_terms)
    for page_url in sitemap_page_urls:
        if not same_domain(page_url, allowed_domains) or not is_html_candidate(page_url):
            continue
        score = url_score(page_url, "", tokenized_search)
        # If the slug is not descriptive, keep a small sample of newer-looking URLs.
        if score > 0 or re.search(r"20\d{2}", page_url):
            raw_link_candidates.append((page_url, "", score + 2.0))
            method_by_url.setdefault(page_url, "sitemap")

    ranked = rank_candidate_urls(raw_link_candidates, max(40, max_pages * 4))

    # 4) Fetch candidate articles/pages and score full content.
    candidates: List[Candidate] = []
    seen_urls: Set[str] = set()
    for url, anchor_label, seed_score in ranked:
        if url in seen_urls:
            continue
        seen_urls.add(url)
        html, ok, _, final_url, content_type = fetch(url)
        if not ok or not html:
            continue
        if "html" not in content_type.lower() and "xml" not in content_type.lower():
            continue
        text = html_to_text(html)
        if not text:
            continue
        title = extract_title(html) or anchor_label or final_url
        date = extract_article_date(html, text)
        relevance = score_relevance(title, text, search_terms)
        relevance += seed_score * 0.45
        if date:
            days_old = max(0.0, (now_dt().replace(tzinfo=None) - date).total_seconds() / 86400.0)
            relevance += max(0.0, 42.0 - min(days_old, 42.0)) * 0.75
        role_scores = {role: score_role(text, ROLE_KEYWORDS.get(role, [])) for role in source_cfg.get("roles", [])}
        # Ignore pages with weak topic evidence. This protects against broad landing pages
        # being selected just because they are recent.
        if relevance < 10 and score_role(text, CORE_KEYWORDS) < 10:
            continue
        candidates.append(
            Candidate(
                source_key=source_key,
                organization=source_cfg["organization"],
                url=final_url,
                title=title,
                date=date,
                text=text,
                html=html,
                discovery_method=method_by_url.get(url, "linked_page"),
                relevance_score=relevance,
                role_score=role_scores,
            )
        )
        if len(candidates) >= max_pages:
            break
        time.sleep(0.12)

    candidates.sort(key=lambda c: (-c.relevance_score, -(c.date.timestamp() if c.date else 0)))
    log = {
        "source_key": source_key,
        "organization": source_cfg["organization"],
        "configured_domains": source_cfg["domains"],
        "discovery_search_urls": list(dict.fromkeys(discovered_search_urls))[:10],
        "sitemap_count": len(sitemap_page_urls),
        "candidate_count": len(candidates),
        "candidates": [c.as_log() for c in candidates],
    }
    return candidates, log


def score_relevance(title: str, text: str, keywords: Sequence[str]) -> float:
    title_l = (title or "").lower()
    text_l = (text or "").lower()
    score = 0.0
    for term in keywords:
        t = term.lower()
        if t in title_l:
            score += 35.0 if " " in t else 14.0
        count = min(5, text_l.count(t))
        score += count * (12.0 if " " in t else 3.0)
    # Strong topic anchors.
    if "anak krakatau" in title_l:
        score += 30
    elif "krakatau" in title_l:
        score += 10
    if "gunung" in title_l and "krakatau" in title_l:
        score += 10
    return score


def score_role(text: str, role_terms: Sequence[str]) -> float:
    text_l = (text or "").lower()
    score = 0.0
    for term in role_terms:
        count = min(5, text_l.count(term.lower()))
        score += count * (5.0 if " " in term else 2.0)
    return score

# ---------------------------------------------------------------------------
# CANDIDATE SELECTION
# ---------------------------------------------------------------------------


def best_candidate(candidates: Sequence[Candidate], role: str, extra_terms: Sequence[str] = ()) -> Optional[Candidate]:
    if not candidates:
        return None
    role_terms = list(ROLE_KEYWORDS.get(role, [])) + list(extra_terms)
    ranked: List[Tuple[float, Candidate]] = []
    for candidate in candidates:
        role_score = score_role(candidate.text, role_terms)
        recency = 0.0
        if candidate.date:
            days_old = max(0.0, (now_dt().replace(tzinfo=None) - candidate.date).total_seconds() / 86400.0)
            recency = max(0.0, 35.0 - min(days_old, 35.0)) * 0.7
        source_boost = 2.0 if candidate.organization == "Badan Geologi" and role == "status" else 0.0
        ranked.append((candidate.relevance_score + role_score * 3.0 + recency + source_boost, candidate))
    ranked.sort(key=lambda x: (-x[0], -(x[1].date.timestamp() if x[1].date else 0)))
    chosen = ranked[0][1]
    chosen.role_score = chosen.role_score or {}
    chosen.role_score[role] = score_role(chosen.text, role_terms)
    return chosen


def latest_candidate(candidates: Sequence[Candidate]) -> Optional[Candidate]:
    valid = [c for c in candidates if c.date]
    if valid:
        return max(valid, key=lambda c: c.date or datetime.min)
    return candidates[0] if candidates else None

# ---------------------------------------------------------------------------
# EXTRACTION LOGIC
# ---------------------------------------------------------------------------


def extract_status(text: str) -> Optional[str]:
    normalized = (text or "").replace("–", "-").replace("—", "-")
    if re.search(r"Level\s*II\s*(?:\(|-)?\s*Waspada", normalized, re.I):
        return "Level II — Waspada"
    if re.search(r"Level\s*III\s*(?:\(|-)?\s*Siaga", normalized, re.I):
        return "Level III — Siaga"
    if re.search(r"Level\s*IV\s*(?:\(|-)?\s*Awas", normalized, re.I):
        return "Level IV — Awas"
    if re.search(r"Level\s*I\s*(?:\(|-)?\s*Normal", normalized, re.I):
        return "Level I — Normal"
    return None


def extract_status_date(text: str, status: Optional[str]) -> Optional[str]:
    if not status:
        return None
    # Prefer a date/time immediately associated with the status statement.
    pattern = r"(?:Level\s*(?:II|2|III|3|IV|4|I|1)|tingkat aktivitas).{0,260}?(\d{1,2}\s+" + MONTH_PATTERN + r"\s+20\d{2})(?:\s+(?:pukul|jam)?\s*(\d{1,2}[.:]\d{2})\s*WIB)?"
    match = re.search(pattern, text or "", re.I | re.S)
    if match:
        date_text = match.group(1)
        time_text = match.group(3)
        month = MONTHS.get(re.search(MONTH_PATTERN, date_text, re.I).group(1).lower()) if re.search(MONTH_PATTERN, date_text, re.I) else None
        dm = re.match(r"(\d{1,2})\s+([A-Za-z]+)\s+(20\d{2})", date_text)
        if dm and month:
            day, year = int(dm.group(1)), int(dm.group(3))
            if time_text:
                return f"{day} {dm.group(2)} {year}, {time_text.replace('.', ':')} WIB"
            return f"{day} {dm.group(2)} {year}"
    return None


def extract_level_iii_start(text: str) -> Optional[str]:
    patterns = [
        rf"(?:Level\s*III|Level\s*3).{{0,120}}?(?:sejak|mulai|pada)?\s*(\d{{1,2}}\s+{MONTH_PATTERN}\s+20\d{{2}})(?:\s+pukul\s*(\d{{1,2}}[.:]\d{{2}}))?",
        rf"(?:dinaikkan|dinaikkan menjadi|ditetapkan).{{0,150}}?(\d{{1,2}}\s+{MONTH_PATTERN}\s+20\d{{2}})(?:\s+pukul\s*(\d{{1,2}}[.:]\d{{2}}))?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text or "", re.I | re.S)
        if match:
            date_txt = match.group(1)
            time_txt = match.group(3) if len(match.groups()) >= 3 else None
            if time_txt:
                return f"{date_txt}, {time_txt.replace('.', ':')} WIB"
            return date_txt
    return None


def extract_datetime_after_anchor(text: str, anchors: Sequence[str], lookahead: int = 160) -> Optional[str]:
    for anchor in anchors:
        match = re.search(anchor, text or "", re.I)
        if not match:
            continue
        window = text[match.start(): match.end() + lookahead]
        dm = re.search(rf"(\d{{1,2}}\s+{MONTH_PATTERN}\s+20\d{{2}})(?:\s*(?:pukul|jam)?\s*(\d{{1,2}}[.:]\d{{2}})\s*(?:WIB|WITA|WIT)?)?", window, re.I)
        if dm:
            time_txt = dm.group(3)
            return f"{dm.group(1)}, {time_txt.replace('.', ':')} WIB" if time_txt else dm.group(1)
    return None


def extract_continuous_start(text: str) -> Optional[str]:
    return extract_datetime_after_anchor(text, [r"erupsi menerus dimulai", r"erupsi menerus", r"continuous eruption began", r"mulai mengalami erupsi menerus"])


def extract_continuous_end(text: str) -> Optional[str]:
    return extract_datetime_after_anchor(text, [r"erupsi menerus berakhir", r"erupsi menerus berakhir", r"continuous eruption ended", r"erupsi menerus.*?berakhir"])


def extract_continuous_duration(text: str) -> Optional[str]:
    patterns = [
        r"(\d+(?:[.,]\d+)?)\s*(?:jam|hours?)",
        r"(?:selama|for)\s+(\d+(?:[.,]\d+)?)\s*(?:jam|hours?)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text or "", re.I)
        if m:
            return f"~{m.group(1)} hours"
    if re.search(r"sekitar\s+25\s+jam|approximately\s+25\s+hours", text or "", re.I):
        return "~25 hours"
    return None


def extract_eruptions_count(text: str) -> Optional[int]:
    patterns = [
        r"([\d.,]+)\s+kali\s+erupsi",
        r"([\d.,]+)\s+eruptions?",
        r"tercatat\s+([\d.,]+)\s+erupsi",
        r"recorded\s+([\d.,]+)\s+eruptions?",
    ]
    return find_number(patterns, text)


def extract_strombolian_count(text: str) -> Optional[int]:
    patterns = [
        r"([\d.,]+)\s+kali\s+aktivitas\s+strombolian",
        r"([\d.,]+)\s+kali\s+strombolian",
        r"([\d.,]+)\s+strombolian",
    ]
    return find_number(patterns, text)


def extract_population(text: str) -> Optional[int]:
    patterns = [
        rf"([\d.,]+)\s*(?:juta|million)\s+(?:orang|people|jiwa|penduduk)",
        r"([\d.,]+)\s+(?:orang|people|jiwa|penduduk).{0,80}?(?:terdampak|terpapar|affected|exposed)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text or "", re.I | re.S)
        if not m:
            continue
        raw = m.group(1)
        if re.search(r"juta|million", m.group(0), re.I):
            n = parse_human_number(raw)
            return int(round(n * 1_000_000)) if n is not None else None
        return parse_number(raw)
    return None


def extract_aviation(text: str) -> dict:
    return {
        "total_affected_flights": find_number([
            r"([\d.,]+)\s+(?:affected\s+)?flights",
            r"([\d.,]+)\s+penerbangan\s+(?:terdampak|affected)",
            r"(?:sebanyak|sekitar)\s+([\d.,]+)\s+penerbangan",
        ], text),
        "total_affected_passengers": find_number([
            r"([\d.,]+)\s+(?:affected\s+)?passengers",
            r"([\d.,]+)\s+penumpang\s+(?:terdampak|affected)",
            r"(?:sebanyak|sekitar)\s+([\d.,]+)\s+penumpang",
        ], text),
        "temporarily_closed": {
            "flights": find_number([
                r"([\d.,]+)\s+(?:flights|penerbangan).{0,260}?(?:ditutup sementara|temporary closure|closed temporarily)",
                r"(?:ditutup sementara|temporary closure|closed temporarily).{0,260}?([\d.,]+)\s+(?:flights|penerbangan)",
            ], text),
            "passengers": find_number([
                r"(?:ditutup sementara|temporary closure|closed temporarily).{0,320}?([\d.,]+)\s+(?:passengers|penumpang)",
            ], text),
        },
        "route_adjustment": {
            "flights": find_number([
                r"penyesuaian\s+rute.{0,260}?([\d.,]+)\s+penerbangan",
                r"([\d.,]+)\s+(?:flights|penerbangan).{0,180}?(?:route adjustment|penyesuaian\s+rute)",
            ], text),
            "passengers": find_number([
                r"penyesuaian\s+rute.{0,320}?([\d.,]+)\s+penumpang",
                r"route adjustment.{0,260}?([\d.,]+)\s+passengers",
            ], text),
        },
    }


def extract_health(text: str) -> dict:
    return {
        "hospitals": find_number([
            r"([\d.,]+)\s+rumah\s+sakit",
            r"([\d.,]+)\s+hospitals?",
        ], text),
        "puskesmas": find_number([
            r"([\d.,]+)\s+puskesmas",
        ], text),
        "affected_regencies_cities": find_number([
            r"([\d.,]+)\s+kabupaten\s*/\s*kota",
            r"([\d.,]+)\s+kabupaten/kota",
            r"([\d.,]+)\s+regencies?\s*/\s*cities?",
        ], text),
        "ispa_cases": find_number([
            r"([\d.,]+)\s+(?:kasus\s+)?ISPA",
            r"([\d.,]+)\s+respiratory\s+cases",
        ], text),
    }


def extract_regions(text: str) -> List[str]:
    found = []
    lower = (text or "").lower()
    for province in PROVINCES:
        if province.lower() in lower:
            found.append(province)
    return found


def extract_airport_statuses(text: str) -> List[dict]:
    output = []
    lower = (text or "").lower()
    for airport in AIRPORTS:
        code = airport["code"].lower()
        name = airport["name"].lower()
        if name in lower or re.search(rf"\b{re.escape(code)}\b", lower):
            item = dict(airport)
            window_match = re.search(
                rf"(.{{0,180}}(?:{re.escape(code)}|{re.escape(name)}).{{0,180}})",
                text or "",
                re.I | re.S,
            )
            window = window_match.group(1) if window_match else ""
            if re.search(r"ditutup|closure|closed", window, re.I):
                item["impact"] = "Temporary closure / closure reported"
            elif re.search(r"terdampak|affected|ash", window, re.I):
                item["impact"] = "Affected / ash impact reported"
            output.append(item)
    return output

# ---------------------------------------------------------------------------
# CARRY-FORWARD / QUALITY CONTROL
# ---------------------------------------------------------------------------


def read_previous_data() -> Optional[dict]:
    try:
        if OUTPUT_JSON.exists():
            return json.loads(OUTPUT_JSON.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"WARNING: Could not read previous JSON: {exc}")
    return None


def previous_get(data: Optional[dict], path: Sequence[str], default: Any = None) -> Any:
    current = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


class QualityTracker:
    def __init__(self) -> None:
        self.carried_forward: List[str] = []
        self.current: List[str] = []
        self.missing: List[str] = []

    def resolve(self, field_name: str, current_value: Any, previous_value: Any = None) -> Any:
        if current_value not in (None, "", []):
            self.current.append(field_name)
            return current_value
        if previous_value not in (None, "", []):
            self.carried_forward.append(field_name)
            return previous_value
        self.missing.append(field_name)
        return None

# ---------------------------------------------------------------------------
# BUILD OUTPUT
# ---------------------------------------------------------------------------


def candidate_source_record(candidate: Optional[Candidate], role: str, status: str = "selected") -> dict:
    if not candidate:
        return {
            "role": role,
            "selection": status,
            "url": None,
            "title": None,
            "date": None,
            "organization": None,
            "reference_type": "official_government_dynamic_discovery",
        }
    return {
        "role": role,
        "selection": status,
        "url": candidate.url,
        "title": candidate.title,
        "date": candidate.date.strftime("%Y-%m-%d") if candidate.date else None,
        "organization": candidate.organization,
        "reference_type": "official_government_dynamic_discovery",
        "discovery_method": candidate.discovery_method,
        "relevance_score": round(candidate.relevance_score, 2),
        "role_score": round(score_role(candidate.text, ROLE_KEYWORDS.get(role, [])), 2),
    }


def build_dynamic_timeline(geologi_candidates: Sequence[Candidate], previous: Optional[dict]) -> List[dict]:
    entries: List[dict] = []
    seen: Set[str] = set()
    for candidate in sorted(
        geologi_candidates,
        key=lambda c: (c.date or datetime.min, c.relevance_score),
        reverse=True,
    ):
        if not candidate.date:
            continue
        key = normalize_space(candidate.title.lower())
        if key in seen:
            continue
        seen.add(key)
        description = candidate.text[:420]
        # Drop boilerplate prefixes where possible.
        description = re.sub(r"^.*?(?:LAPORAN KHUSUS|Dengan ini disampaikan)", "", description, flags=re.I)
        description = normalize_space(description)[:360]
        entries.append({
            "date": candidate.date.strftime("%d %B %Y"),
            "title": candidate.title,
            "description": description,
            "source_url": candidate.url,
            "source": candidate.organization,
        })
        if len(entries) >= 10:
            break

    if len(entries) >= 3:
        return entries

    # Keep historical timeline from the previous file if discovery is thin.
    previous_timeline = previous_get(previous, ["eruption", "timeline"], [])
    if previous_timeline:
        merged = entries + [x for x in previous_timeline if normalize_space(str(x.get("title", "")).lower()) not in seen]
        return merged[:10]
    return entries


def latest_selected_by_role(source_candidates: Dict[str, List[Candidate]], role: str, preferred_sources: Sequence[str]) -> Optional[Candidate]:
    pool: List[Candidate] = []
    for source_key in preferred_sources:
        pool.extend(source_candidates.get(source_key, []))
    return best_candidate(pool, role)


def merge_airports(previous: Optional[dict], current_sources: Sequence[Candidate]) -> List[dict]:
    airport_map = {a["code"]: dict(a) for a in AIRPORTS}
    previous_airports = previous_get(previous, ["impacted_airports"], []) or []
    for item in previous_airports:
        if item.get("code") in airport_map:
            airport_map[item["code"]].update(item)
    combined_text = " ".join(c.text for c in current_sources[:6])
    discovered = extract_airport_statuses(combined_text)
    for item in discovered:
        airport_map[item["code"]].update(item)
    return list(airport_map.values())


def build_output(
    source_candidates: Dict[str, List[Candidate]],
    source_logs: List[dict],
    previous: Optional[dict],
    quality: QualityTracker,
) -> dict:
    # Status: prefer Badan Geologi, but remain adaptive if a platform/domain changes.
    geologi_pool = source_candidates.get("geologi", [])
    status_candidate = best_candidate(geologi_pool, "status")
    if not status_candidate:
        all_official = [c for items in source_candidates.values() for c in items]
        status_candidate = best_candidate(all_official, "status")
    status_current = extract_status(status_candidate.text) if status_candidate else None
    status_previous = previous_get(previous, ["event", "current_status"])
    current_status = quality.resolve("event.current_status", status_current, status_previous)

    status_date_current = extract_status_date(status_candidate.text, status_current) if status_candidate else None
    status_date_previous = previous_get(previous, ["event", "status_date"])
    status_date = quality.resolve("event.status_date", status_date_current, status_date_previous)

    # Core event timing from the best Geology reports.
    geologi_combined = "\n".join(c.text for c in sorted(geologi_pool, key=lambda c: c.date or datetime.min, reverse=True)[:8])
    level_iii_current = extract_level_iii_start(geologi_combined)
    continuous_start_current = extract_continuous_start(geologi_combined)
    continuous_end_current = extract_continuous_end(geologi_combined)
    duration_current = extract_continuous_duration(geologi_combined)
    eruptions_current = extract_eruptions_count(geologi_combined)
    strombolian_current = extract_strombolian_count(geologi_combined)

    level_iii_start = quality.resolve("event.level_iii_start", level_iii_current, previous_get(previous, ["event", "level_iii_start"]))
    continuous_start = quality.resolve("event.continuous_eruption_start", continuous_start_current, previous_get(previous, ["event", "continuous_eruption_start"]))
    continuous_end = quality.resolve("event.continuous_eruption_end", continuous_end_current, previous_get(previous, ["event", "continuous_eruption_end"]))
    continuous_duration = quality.resolve("event.continuous_eruption_duration", duration_current, previous_get(previous, ["event", "continuous_eruption_duration"]))
    eruptions_count = quality.resolve("event.eruptions_until_3_sep", eruptions_current, previous_get(previous, ["event", "eruptions_until_3_sep"]))
    strombolian_count = quality.resolve("event.strombolian_after_continuous", strombolian_current, previous_get(previous, ["event", "strombolian_after_continuous"]))

    # Health.
    health_sources = []
    for key in ("kemenkes", "bnpb"):
        health_sources.extend(source_candidates.get(key, []))
    health_candidate = best_candidate(health_sources, "health")
    health_current = extract_health(health_candidate.text) if health_candidate else {}

    hospitals = quality.resolve("health.hospitals", health_current.get("hospitals"), previous_get(previous, ["health", "hospitals"]))
    puskesmas = quality.resolve("health.puskesmas", health_current.get("puskesmas"), previous_get(previous, ["health", "puskesmas"]))
    regencies = quality.resolve("health.affected_regencies_cities", health_current.get("affected_regencies_cities"), previous_get(previous, ["health", "affected_regencies_cities"]))
    ispa = quality.resolve("health.ispa_cases", health_current.get("ispa_cases"), previous_get(previous, ["health", "ispa_cases"]))

    # Population can be in Kemenkes, BNPB, or Kemenhub-type impact reports.
    population_pool = []
    for key in ("kemenkes", "bnpb"):
        population_pool.extend(source_candidates.get(key, []))
    population_candidate = best_candidate(population_pool, "health", extra_terms=["penduduk", "terpapar", "terdampak", "people", "population"])
    population_current = extract_population(population_candidate.text) if population_candidate else None
    population = quality.resolve("population.exposed_population", population_current, previous_get(previous, ["population", "exposed_population"]))

    # Aviation.
    aviation_pool = []
    for key in ("kemenhub", "bmkg"):
        aviation_pool.extend(source_candidates.get(key, []))
    aviation_candidate = best_candidate(aviation_pool, "aviation")
    aviation_current = extract_aviation(aviation_candidate.text) if aviation_candidate else {}
    previous_aviation = previous_get(previous, ["aviation"], {}) or {}
    aviation = {
        "total_affected_flights": quality.resolve("aviation.total_affected_flights", aviation_current.get("total_affected_flights"), previous_aviation.get("total_affected_flights")),
        "total_affected_passengers": quality.resolve("aviation.total_affected_passengers", aviation_current.get("total_affected_passengers"), previous_aviation.get("total_affected_passengers")),
        "temporarily_closed": {
            "flights": quality.resolve("aviation.temporarily_closed.flights", aviation_current.get("temporarily_closed", {}).get("flights"), previous_get(previous, ["aviation", "temporarily_closed", "flights"])),
            "passengers": quality.resolve("aviation.temporarily_closed.passengers", aviation_current.get("temporarily_closed", {}).get("passengers"), previous_get(previous, ["aviation", "temporarily_closed", "passengers"])),
        },
        "route_adjustment": {
            "flights": quality.resolve("aviation.route_adjustment.flights", aviation_current.get("route_adjustment", {}).get("flights"), previous_get(previous, ["aviation", "route_adjustment", "flights"])),
            "passengers": quality.resolve("aviation.route_adjustment.passengers", aviation_current.get("route_adjustment", {}).get("passengers"), previous_get(previous, ["aviation", "route_adjustment", "passengers"])),
        },
        "source_method": "adaptive_official_discovery",
    }

    impact_sources = []
    for key in ("bnpb", "bmkg", "kemenhub", "kemenkes"):
        impact_sources.extend(source_candidates.get(key, []))
    combined_impact_text = "\n".join(c.text for c in impact_sources[:12])
    regions_current = extract_regions(combined_impact_text)
    previous_regions = previous_get(previous, ["regional_impacts", "indonesia_regions"], []) or []
    regions = regions_current or previous_regions
    if regions_current:
        quality.current.append("regional_impacts.indonesia_regions")
    elif previous_regions:
        quality.carried_forward.append("regional_impacts.indonesia_regions")
    else:
        quality.missing.append("regional_impacts.indonesia_regions")

    previous_regional = previous_get(previous, ["regional_impacts"], {}) or {}
    regional_description = previous_regional.get("indonesia_description") or (
        "Reported government sources mention ash/disaster impacts across: " + ", ".join(regions) + "." if regions else ""
    )

    airports = merge_airports(previous, [aviation_candidate] if aviation_candidate else [])

    timeline = build_dynamic_timeline(geologi_pool, previous)

    # Government sources shown in the dashboard should be the dynamically selected article pages.
    selected_sources = []
    for role, candidate in (
        ("status", status_candidate),
        ("health", health_candidate),
        ("population", population_candidate),
        ("aviation", aviation_candidate),
    ):
        selected_sources.append(candidate_source_record(candidate, role))

    # Add a few source-level records even when not selected for a headline metric.
    for log in source_logs:
        if log.get("candidates"):
            first = log["candidates"][0]
            selected_sources.append({
                "role": "discovery",
                "selection": "top_candidate",
                "url": first.get("url"),
                "title": first.get("title"),
                "date": first.get("date"),
                "organization": log.get("organization"),
                "reference_type": "official_government_dynamic_discovery",
                "discovery_method": first.get("discovery_method"),
                "relevance_score": first.get("relevance_score"),
            })

    # Deduplicate source records by URL + role.
    seen_source_keys = set()
    dedup_sources = []
    for record in selected_sources:
        key = (record.get("role"), record.get("url"))
        if key in seen_source_keys:
            continue
        seen_source_keys.add(key)
        dedup_sources.append(record)

    return {
        "metadata": {
            "last_updated": now_iso(),
            "last_updated_display": now_dt().strftime("%d %B %Y, %H:%M WIB"),
            "data_as_of": now_dt().strftime("%d %B %Y"),
            "scraper": "scrape_anak_krakatau_2026.py",
            "author": "Kelvin Irawan",
            "reference_mode": "adaptive_official_government_discovery",
            "discovery_strategy": [
                "robots.txt sitemap discovery",
                "sitemap.xml / sitemap index",
                "official homepage/listing links",
                "GET-based internal site search forms",
                "keyword relevance + role relevance + recency ranking",
            ],
            "keyword_bank": list(dict.fromkeys(CORE_KEYWORDS + EVENT_KEYWORDS + PROVINCES)),
            "description": "Automatically refreshed Anak Krakatau 2026 impact dashboard using keyword-based discovery across official government domains instead of fixed article URLs.",
            "note": "When a current metric cannot be extracted, the previous JSON value is carried forward and marked in data_quality rather than replaced by a hard-coded historical fallback.",
            "data_quality": {
                "current_fields": sorted(set(quality.current)),
                "carried_forward_fields": sorted(set(quality.carried_forward)),
                "missing_fields": sorted(set(quality.missing)),
                "current_field_count": len(set(quality.current)),
                "carried_forward_field_count": len(set(quality.carried_forward)),
                "missing_field_count": len(set(quality.missing)),
                "discovery_source_count": len(source_candidates),
                "discovery_candidate_count": sum(len(v) for v in source_candidates.values()),
            },
        },
        "event": {
            "name": "Gunung Anak Krakatau",
            "location": "Sunda Strait, Lampung",
            "current_status": current_status,
            "status_date": status_date,
            "level_iii_start": level_iii_start,
            "continuous_eruption_start": continuous_start,
            "continuous_eruption_end": continuous_end,
            "continuous_eruption_duration": continuous_duration,
            "eruptions_until_3_sep": eruptions_count,
            "strombolian_after_continuous": strombolian_count,
        },
        "population": {
            "exposed_population": population,
            "source_method": "adaptive_official_discovery",
        },
        "health": {
            "hospitals": hospitals,
            "puskesmas": puskesmas,
            "affected_regencies_cities": regencies,
            "ispa_cases": ispa,
            "source_method": "adaptive_official_discovery",
        },
        "aviation": aviation,
        "impacted_airports": airports,
        "regional_impacts": {
            "indonesia_regions": regions,
            "indonesia_description": regional_description,
            "singapore": previous_regional.get("singapore", {
                "status": "Not re-derived from Indonesian government sources",
                "description": "Retained from previous dashboard data unless an official regional government source provides an update.",
                "source_method": "carried_forward_if_available",
            }),
            "malaysia_airlines": previous_regional.get("malaysia_airlines", {
                "status": "Not re-derived from government source",
                "description": "Retained from previous dashboard data unless an official source provides an update.",
                "source_method": "carried_forward_if_available",
            }),
            "malaysia": previous_regional.get("malaysia", {
                "status": "Monitoring",
                "description": "Regional monitoring is sourced from official government discovery where available.",
                "source_method": "adaptive_official_discovery",
            }),
        },
        "eruption": {"timeline": timeline},
        "sources": dedup_sources,
        "discovery": {
            "generated_at": now_iso(),
            "source_logs": source_logs,
        },
    }

# ---------------------------------------------------------------------------
# ARGUMENTS / MAIN
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Adaptive Anak Krakatau government-source scraper")
    parser.add_argument("--days-back", type=int, default=DEFAULT_DAYS_BACK, help="Prefer pages published within this many days")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="Maximum article pages fetched per source")
    parser.add_argument("--max-candidates", type=int, default=DEFAULT_MAX_CANDIDATES, help="Reserved tuning parameter for future discovery expansion")
    parser.add_argument("--sitemap-limit", type=int, default=DEFAULT_SITEMAP_LIMIT, help="Maximum sitemap URLs inspected")
    parser.add_argument("--extra-keywords", default="", help="Comma-separated additional keywords")
    parser.add_argument("--dry-run", action="store_true", help="Discover and print sources without writing JSON/GeoJSON")
    return parser.parse_args()


def build_search_terms(args: argparse.Namespace) -> List[str]:
    terms = list(CORE_KEYWORDS) + list(EVENT_KEYWORDS)
    if args.extra_keywords:
        terms.extend(x.strip() for x in args.extra_keywords.split(",") if x.strip())
    return list(dict.fromkeys(terms))


def main() -> None:
    args = parse_args()
    search_terms = build_search_terms(args)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print("\n============================================================")
    print(" ANAK KRAKATAU 2026 — ADAPTIVE GOVERNMENT SOURCE SCRAPER")
    print("============================================================")
    print(f"Run time: {now_dt().strftime('%d %B %Y, %H:%M WIB')}")
    print(f"Search terms: {', '.join(search_terms)}")
    print("Discovery mode: official domains -> sitemap/homepage/internal search -> ranked article pages")

    previous = read_previous_data()
    source_candidates: Dict[str, List[Candidate]] = {}
    source_logs: List[dict] = []

    for source_key, cfg in SOURCES.items():
        candidates, log = discover_source(
            source_key,
            cfg,
            search_terms,
            max_pages=max(1, args.max_pages),
            sitemap_limit=max(100, args.sitemap_limit),
        )
        source_candidates[source_key] = candidates
        source_logs.append(log)
        print(f"  -> {len(candidates)} relevant candidate(s)")

    # Soft date check for visibility. We do not discard older pages because a
    # historical metric may be the only official evidence currently available.
    cutoff = now_dt().replace(tzinfo=None) - timedelta(days=max(1, args.days_back))
    recent_count = sum(1 for cs in source_candidates.values() for c in cs if c.date and c.date >= cutoff)
    print(f"\nRecent candidates inside {args.days_back} days: {recent_count}")

    if args.dry_run:
        print("\nDRY RUN — top candidates")
        for source_key, candidates in source_candidates.items():
            print(f"\n[{source_key}]")
            for candidate in candidates[:8]:
                date_txt = candidate.date.strftime("%Y-%m-%d") if candidate.date else "no-date"
                print(f"  {date_txt} | {candidate.title[:110]}")
                print(f"       {candidate.url}")
        return

    quality = QualityTracker()
    data = build_output(source_candidates, source_logs, previous, quality)

    OUTPUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8")

    geojson_out = json.loads(json.dumps(GEOJSON))
    geojson_out["generated_at"] = now_iso()
    geojson_out["data_note"] = "Static analytical coverage reference; not a live government polygon boundary."
    OUTPUT_GEOJSON.write_text(json.dumps(geojson_out, ensure_ascii=False, indent=2), encoding="utf-8")

    DISCOVERY_LOG.write_text(json.dumps({
        "generated_at": now_iso(),
        "mode": "adaptive_official_government_discovery",
        "sources": source_logs,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n============================================================")
    print(" SCRAPING COMPLETE")
    print("============================================================")
    print(f"Status: {data['event']['current_status']}")
    print(f"Population: {data['population']['exposed_population']}")
    print(f"Flights: {data['aviation']['total_affected_flights']}")
    print(f"Passengers: {data['aviation']['total_affected_passengers']}")
    print(f"Hospitals: {data['health']['hospitals']}")
    print(f"Puskesmas: {data['health']['puskesmas']}")
    print(f"ISPA: {data['health']['ispa_cases']}")
    print(f"Carried forward fields: {data['metadata']['data_quality']['carried_forward_field_count']}")
    print(f"Missing fields: {data['metadata']['data_quality']['missing_field_count']}")
    print(f"JSON: {OUTPUT_JSON}")
    print(f"GeoJSON: {OUTPUT_GEOJSON}")
    print(f"Discovery log: {DISCOVERY_LOG}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped by user.")
        sys.exit(1)
    except Exception as exc:
        print("\nSCRAPER ERROR:")
        print(exc)
        sys.exit(1)
