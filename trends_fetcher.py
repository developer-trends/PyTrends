#!/usr/bin/env python3
import os, json, time, requests
from urllib.parse import quote
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# --- Caching setup ---
CACHE_FILE = os.path.expanduser("~/.trends_wikidata_cache.json")
try:
    with open(CACHE_FILE) as f:
        CACHE = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    CACHE = {"term_to_qid": {}, "qid_props": {}, "qid_label": {}}


def save_cache():
    with open(CACHE_FILE, 'w') as f:
        json.dump(CACHE, f)

# --- Wikidata enrichment utils ---
WIKIDATA_API = "https://www.wikidata.org/w/api.php"

# Batch resolve labels for a list of Q-IDs
def resolve_labels(qids):
    missing = [q for q in qids if q not in CACHE['qid_label']]
    if missing:
        # batch call up to 50 at once
        params = {
            "action": "wbgetentities",
            "ids": "|".join(missing),
            "props": "labels",
            "languages": "en",
            "format": "json"
        }
        resp = requests.get(WIKIDATA_API, params=params)
        if resp.ok:
            data = resp.json().get('entities', {})
            for qid, ent in data.items():
                label = ent.get('labels', {}).get('en', {}).get('value')
                if label:
                    CACHE['qid_label'][qid] = label
    return [CACHE['qid_label'].get(q) for q in qids]

# Retrieve claims for a single entity Q-ID
def get_entity_props(qid):
    if qid in CACHE['qid_props']:
        return CACHE['qid_props'][qid]
    # fetch claims
    params = {"action": "wbgetentities", "ids": qid, "props": "claims", "format": "json"}
    resp = requests.get(WIKIDATA_API, params=params)
    resp.raise_for_status()
    claims = resp.json()['entities'][qid]['claims']
    def extract(p):
        return [c['mainsnak']['datavalue']['value']['id']
                for c in claims.get(p, [])
                if 'datavalue' in c['mainsnak']]
    props = {
        'sports': extract('P641'),
        'leagues': extract('P118'),
        'teams': extract('P54')
    }
    CACHE['qid_props'][qid] = props
    # brief pause to avoid bursting
    time.sleep(0.05)
    return props

# Lookup entity term: returns Q-ID (cached)
def lookup_qid(term):
    if term in CACHE['term_to_qid']:
        return CACHE['term_to_qid'][term]
    params = {"action": "wbsearchentities", "search": term, "language": "en", "format": "json"}
    resp = requests.get(WIKIDATA_API, params=params)
    resp.raise_for_status()
    results = resp.json().get('search', [])
    qid = results[0]['id'] if results else None
    CACHE['term_to_qid'][term] = qid
    time.sleep(0.05)
    return qid

# Enrich scraped rows with sport, league, team
def enrich_rows(rows):
    enriched = []
    for row in rows:
        title = row[0]
        qid = lookup_qid(title)
        sport = league = team = None
        if qid:
            props = get_entity_props(qid)
            # resolve first items if present
            sport = resolve_labels(props['sports'])[0] if props['sports'] else None
            league = resolve_labels(props['leagues'])[0] if props['leagues'] else None
            team = resolve_labels(props['teams'])[0] if props['teams'] else None
        enriched.append(row + [sport, league, team])
    # save cache to disk after enrichment
    save_cache()
    return enriched

# --- Original scraper code unchanged below ---

def connect_to_sheet(sheet_name):
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds_dict = json.loads(os.environ["GOOGLE_SA_JSON"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client.open(sheet_name).get_worksheet(0)

# ... (dismiss_cookie_banner, extract_table_rows, extract_card_rows, scrape_all_pages unchanged) ...

def main():
    sheet = connect_to_sheet("Trends")
    rows  = scrape_all_pages()
    # enrich with sport, league, and team columns
    rows_enriched = enrich_rows(rows)

    sheet.clear()
    sheet.append_rows(rows_enriched, value_input_option="RAW")
    print(f"✅ {len(rows_enriched)} trends saved (including sport, league, team)")

if __name__=="__main__":
    main()
