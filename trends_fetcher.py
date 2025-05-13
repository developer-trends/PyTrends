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
import openai

# --- CONFIGURATION ---
# Use your GitHub repository secret named 'GPT_AI' for the OpenAI API key
openai.api_key = os.environ.get("GPT_AI")  
CACHE_FILE = os.path.expanduser("~/.trends_wikidata_cache.json")

# --- CACHING SETUP ---
try:
    with open(CACHE_FILE) as f:
        CACHE = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    CACHE = {"term_to_qid": {}, "qid_props": {}, "qid_label": {}}

def save_cache():
    with open(CACHE_FILE, "w") as f:
        json.dump(CACHE, f)

# --- WIKIDATA HELPERS (unchanged) ---
WIKIDATA_API = "https://www.wikidata.org/w/api.php"

def wikidata_request(params):
    backoff = 1
    while True:
        resp = requests.get(WIKIDATA_API, params=params)
        if resp.status_code == 429:
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
            continue
        resp.raise_for_status()
        return resp

# ... lookup_qid, get_entity_props, resolve_labels unchanged ...

# --- ENRICHMENT LAYER ---
def enrich_rows(rows):
    """
    Takes scraped rows and returns new rows with two extra columns:
    - Column H: Sport
    - Column I: League
    """
    enriched = []
    for row in rows:
        title = row[0]
        lang = detect(title)
        qid = lookup_qid(title, lang=lang)

        sport = ""
        league = ""
        if qid:
            props = get_entity_props(qid)
            if props.get("sports"):
                sport = resolve_labels(props["sports"])[0]
            if props.get("leagues"):
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
    return client.open(sheet_name).sheet1

# --- SCRAPING LOGIC (unchanged) ---
# extract_table_rows, extract_card_rows, scrape_all_pages ...

# --- MAIN ENTRYPOINT ---
def main():
    sheet = connect_to_sheet("Trends")
    rows = scrape_all_pages()
    rows_enriched = enrich_rows(rows)

    sheet.clear()
    sheet.append_rows(rows_enriched, value_input_option="RAW")

    print(f"✅ Wrote {len(rows_enriched)} rows (Cols A–I, with Sport in H, League in I)")

if __name__ == "__main__":
    main()
```
