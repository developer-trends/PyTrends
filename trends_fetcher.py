#!/usr/bin/env python3
import os
import json
import time
import requests
from requests.exceptions import HTTPError
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

# --- WIKIDATA BACKOFF HELPER ---
WIKIDATA_API = "https://www.wikidata.org/w/api.php"

def wikidata_request(params):
    backoff = 1
    while True:
        resp = requests.get(WIKIDATA_API, params=params)
        if resp.status_code == 429:
            print(f"⚠️ Rate limited, sleeping {backoff}s…")
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
            continue
        try:
            resp.raise_for_status()
        except HTTPError:
            raise
        return resp

# --- WIKIDATA UTILITIES ---
def lookup_qid(term, lang="en"):
    """Search Wikidata for term in language lang → QID."""
    cache_key = f"{lang}:{term}"
    if cache_key in CACHE['term_to_qid']:
        return CACHE['term_to_qid'][cache_key]
    params = {
        "action": "wbsearchentities",
        "search": term,
        "language": lang,
        "format": "json"
    }
    resp = wikidata_request(params)
    results = resp.json().get("search", [])
    qid = results[0]["id"] if results else None
    CACHE['term_to_qid'][cache_key] = qid
    time.sleep(0.05)
    return qid

def get_entity_props(qid):
    """Return dict with lists of QIDs for sports, leagues."""
    if qid in CACHE['qid_props']:
        return CACHE['qid_props'][qid]

    params = {
        "action": "wbgetentities",
        "ids": qid,
        "props": "claims",
        "format": "json"
    }
    resp = wikidata_request(params)
    claims = resp.json()["entities"][qid]["claims"]

    def extract(pid):
        return [
            c["mainsnak"]["datavalue"]["value"]["id"]
            for c in claims.get(pid, [])
            if "datavalue" in c["mainsnak"]
        ]

    props = {
        "sports": extract("P641"),
        "leagues": extract("P118")
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
            "languages": "en,vi,th,ko,ja,zh",
            "format": "json"
        }
        resp = wikidata_request(params)
        data = resp.json().get("entities", {})
        for qid, ent in data.items():
            lbls = ent.get("labels", {})
            label = lbls.get("en", {}).get("value") or next(iter(lbls.values()))["value"]
            CACHE['qid_label'][qid] = label
    return [CACHE['qid_label'].get(q) for q in qids]

# --- ENRICHMENT LAYER ---
def enrich_rows(rows):
    enriched = []
    for row in rows:
        title = row[0]
        lang = detect(title)
        qid = lookup_qid(title, lang=lang)
        sport = league = None
        if qid:
            props = get_entity_props(qid)
            if props["sports"]:
                sport = resolve_labels(props["sports"])[0]
            if props["leagues"]:
                league = resolve_labels(props["leagues"])[0]
        enriched.append(row + [sport, league])

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

# (rest of scraping logic unchanged)

# --- MAIN ENTRYPOINT ---
def main():
    sheet = connect_to_sheet("Trends")
    rows  = scrape_all_pages()
    rows_enriched = enrich_rows(rows)
    sheet.clear()
    sheet.append_rows(rows_enriched, value_input_option="RAW")
    print(f"✅ {len(rows_enriched)} trends saved to Google Sheet (including sport and league)")

if __name__ == "__main__":
    main()
