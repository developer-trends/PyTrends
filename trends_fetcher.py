#!/usr/bin/env python3
import os
import json
import time
from urllib.parse import quote

import requests
from langdetect import detect
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from requests.exceptions import HTTPError

# --- CONFIG ---
CACHE_PATH = os.path.expanduser("~/.trends_cache.json")
WIKIDATA_API = "https://www.wikidata.org/w/api.php"

# --- CACHE UTILS ---
def load_cache():
    try:
        with open(CACHE_PATH) as f:
            return json.load(f)
    except:
        return {"qids": {}, "props": {}, "labels": {}}
CACHE = load_cache()

def save_cache():
    with open(CACHE_PATH, 'w') as f:
        json.dump(CACHE, f)

# --- WIKIDATA HELPERS ---
def _wikidata_request(params):
    backoff = 1
    while True:
        resp = requests.get(WIKIDATA_API, params=params)
        if resp.status_code == 429:
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
            continue
        resp.raise_for_status()
        return resp.json()

# Batch-fetch QIDs for all unique titles

def batch_lookup_qids(titles):
    qids = {}
    for title in titles:
        lang = detect(title)
        key = f"{lang}:{title}"
        qid = CACHE['qids'].get(key)
        if qid is None:
            data = _wikidata_request({"action":"wbsearchentities","search":title,"language":lang,"format":"json"})
            qid = data.get('search',[{}])[0].get('id') if data.get('search') else None
            if not qid:
                # fallback to English
                key_en = f"en:{title}"
                qid = CACHE['qids'].get(key_en) or (
                    _wikidata_request({"action":"wbsearchentities","search":title,"language":"en","format":"json"})
                    .get('search',[{}])[0].get('id')
                )
                CACHE['qids'][key_en] = qid
            CACHE['qids'][key] = qid
        qids[title] = qid
    return qids

# Batch-fetch claims for sports and leagues

def batch_get_claims(qids):
    props = {}
    for title, qid in qids.items():
        if not qid: continue
        for pid in ('P641','P118'):
            key = f"{qid}:{pid}"
            if key not in CACHE['props']:
                data = _wikidata_request({"action":"wbgetentities","ids":qid,"props":"claims","format":"json"})
                claims = data['entities'][qid]['claims'].get(pid, [])
                ids = [c['mainsnak']['datavalue']['value']['id'] for c in claims if 'datavalue' in c['mainsnak']]
                CACHE['props'][key] = ids
        props[qid] = {
            'sports': CACHE['props'].get(f"{qid}:P641", []),
            'leagues': CACHE['props'].get(f"{qid}:P118", [])
        }
    return props

# Batch-resolve labels

def batch_resolve_labels(all_ids):
    missing = [i for i in all_ids if i and i not in CACHE['labels']]
    if missing:
        data = _wikidata_request({"action":"wbgetentities","ids":"|".join(missing),"props":"labels","languages":"en,vi,th,ko,ja,zh","format":"json"})
        for qid, ent in data.get('entities', {}).items():
            lbls = ent.get('labels', {})
            CACHE['labels'][qid] = lbls.get('en',{}).get('value') or next(iter(lbls.values()))['value']
    return {qid: CACHE['labels'].get(qid) for qid in all_ids}

# Infobox fallback

def scrape_infobox(title):
    try:
        r = requests.get(f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}", timeout=5)
        r.raise_for_status()
    except:
        return None, None
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(r.text, 'html.parser')
    tbl = soup.find('table', class_='infobox')
    sport=league=None
    for row in tbl.find_all('tr') if tbl else []:
        th,td = row.find('th'), row.find('td')
        if not(th and td): continue
        k = th.get_text(strip=True).lower()
        v = td.get_text(strip=True)
        if 'sport' in k and not sport: sport = v
        if ('league' in k or 'competition' in k) and not league: league = v
    return sport, league

# --- ENRICHMENT LAYER ---
def enrich_rows(rows):
    """
    For each row, lookup sport and league, append them in cols H and I.
    """
    enriched = []
    for row in rows:
        title = row[0]
        lang = detect(title)
        qid = lookup_qid(title, lang=lang) or lookup_qid(title, lang="en")
        sport = league = None
        if qid:
            props = get_entity_props(qid)
            if props.get("sports"):
                sport = resolve_labels(props["sports"])[0]
            if props.get("leagues"):
                league = resolve_labels(props["leagues"])[0]
        # fallback to Wikipedia infobox if missing
        if not sport or not league:
            fb_s, fb_l = scrape_infobox(title)
            sport = sport or fb_s
            league = league or fb_l
        enriched.append(row + [sport, league])
    save_cache()
    return enriched
# --- SCRAPERS & GOOGLE SHEETS SETUP ---

def connect_to_sheet(sheet_name):
    """Authorize and return the first worksheet of the given sheet."""
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds_dict = json.loads(os.environ["GOOGLE_SA_JSON"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client.open(sheet_name).sheet1

# --- SCRAPERS --- (UNCHANGED) ---
# extract_table_rows, extract_card_rows, scrape_all_pages

# --- MAIN ---
def main():
    sheet = connect_to_sheet('Trends')
    rows  = scrape_all_pages()
    final = enrich_rows(rows)
    sheet.clear()
    sheet.append_rows(final, value_input_option='RAW')

if __name__ == '__main__':
    main()
