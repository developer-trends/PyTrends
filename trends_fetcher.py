#!/usr/bin/env python3
import os
import json
import time
import requests
from langdetect import detect
from urllib.parse import quote
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# --- CACHING SETUP ---
CACHE_FILE = os.path.expanduser("~/.trends_wikidata_cache.json")
try:
    with open(CACHE_FILE) as f:
        CACHE = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    CACHE = {"term_to_qid": {}, "qid_props": {}, "qid_label": {}}

def save_cache():
    with open(CACHE_FILE, "w") as f:
        json.dump(CACHE, f)

# --- WIKIDATA UTILITIES ---
WIKIDATA_API = "https://www.wikidata.org/w/api.php"

def lookup_qid(term, lang="en"):
    """Search Wikidata for `term` in language `lang` → QID."""
    cache_key = f"{lang}:{term}"
    if cache_key in CACHE['term_to_qid']:
        return CACHE['term_to_qid'][cache_key]
    params = {
        "action": "wbsearchentities",
        "search": term,
        "language": lang,
        "format": "json"
    }
    resp = requests.get(WIKIDATA_API, params=params)
    resp.raise_for_status()
    results = resp.json().get("search", [])
    qid = results[0]["id"] if results else None
    CACHE['term_to_qid'][cache_key] = qid
    time.sleep(0.05)
    return qid

def get_entity_props(qid):
    """Return dict with lists of QIDs for sports, leagues, teams."""
    if qid in CACHE['qid_props']:
        return CACHE['qid_props'][qid]

    params = {
        "action": "wbgetentities",
        "ids": qid,
        "props": "claims",
        "format": "json"
    }
    resp = requests.get(WIKIDATA_API, params=params)
    resp.raise_for_status()
    claims = resp.json()["entities"][qid]["claims"]

    def extract(pid):
        return [
            c["mainsnak"]["datavalue"]["value"]["id"]
            for c in claims.get(pid, [])
            if "datavalue" in c["mainsnak"]
        ]

    props = {
        "sports": extract("P641"),
        "leagues": extract("P118"),
        "teams": extract("P54")
    }
    CACHE['qid_props'][qid] = props
    time.sleep(0.05)
    return props

def resolve_labels(qids):
    """Batch-fetch human labels for a list of QIDs."""
    missing = [q for q in qids if q not in CACHE['qid_label']]
    if missing:
        params = {
            "action": "wbgetentities",
            "ids": "|".join(missing),
            "props": "labels",
            "languages": "en,vi,th,ko,ja,zh",  # request all likely langs
            "format": "json"
        }
        resp = requests.get(WIKIDATA_API, params=params)
        resp.raise_for_status()
        data = resp.json().get("entities", {})
        for qid, ent in data.items():
            lbls = ent.get("labels", {})
            # pick English first, else the first available
            label = lbls.get("en", {}).get("value") or next(iter(lbls.values()))["value"]
            CACHE['qid_label'][qid] = label
    return [CACHE['qid_label'].get(q) for q in qids]

# --- ENRICHMENT LAYER ---

def enrich_rows(rows):
    enriched = []
    for row in rows:
        title = row[0]
        lang = detect(title)  # auto-detect language
        qid = lookup_qid(title, lang=lang)
        sport = league = team = None
        if qid:
            props = get_entity_props(qid)
            if props["sports"]:
                sport = resolve_labels(props["sports"])[0]
            if props["leagues"]:
                league = resolve_labels(props["leagues"])[0]
            if props["teams"]:
                team = resolve_labels(props["teams"])[0]
        enriched.append(row + [sport, league, team])

    save_cache()
    return enriched

# --- GOOGLE SHEETS SETUP ---

def connect_to_sheet(sheet_name):
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds_dict = json.loads(os.environ["GOOGLE_SA_JSON"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client.open(sheet_name).get_worksheet(0)

# --- PLAYWRIGHT SCRAPERS ---

def dismiss_cookie_banner(page):
    for label in ("Accept all", "I agree", "AGREE"):
        try:
            btn = page.get_by_role("button", name=label)
            if btn.count():
                btn.first.click()
                page.wait_for_timeout(800)
                print("🛡️ Dismissed cookie banner")
                return
        except:
            pass

def extract_table_rows(page):
    try:
        page.wait_for_selector("table tbody tr", state="attached", timeout=5000)
    except PlaywrightTimeoutError:
        return []
    rows = page.locator("table tbody tr")
    total = rows.count()
    print(f"🔢 [table] found {total} rows – skipping the first one")
    out = []
    for i in range(1, total):
        row = rows.nth(i)
        if not row.is_visible(): 
            continue
        cells = row.locator("td")
        if cells.count() < 5: 
            continue

        title  = cells.nth(1).inner_text().split("\n")[0].strip()
        volume = cells.nth(2).inner_text().split("\n")[0].strip()
        raw   = cells.nth(3).inner_text().split("\n")
        parts = [l for l in raw if l and l.lower() not in ("trending_up","timelapse")]
        started = parts[0].strip() if parts else ""
        ended   = parts[1].strip() if len(parts)>1 else ""

        toggle = cells.nth(3).locator("div.vdw3Ld")
        target_publish = ended
        try:
            toggle.click(); time.sleep(0.2)
            raw2 = cells.nth(3).inner_text().split("\n")
            p2   = [l for l in raw2 if l and l.lower() not in ("trending_up","timelapse")]
            if p2:
                target_publish = p2[0].strip()
        finally:
            try:
