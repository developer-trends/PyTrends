#!/usr/bin/env python3
import os
import json
import time
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup
from langdetect import detect
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from requests.exceptions import HTTPError

# --- CONFIG ---
CACHE_PATH = os.path.expanduser("~/.trends_cache.json")
WIKIDATA_API = "https://www.wikidata.org/w/api.php"

# --- UTILS ---
def load_cache():
    try:
        with open(CACHE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
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

def get_qid(title, lang='en'):
    key = f"{lang}:{title}"
    if key in CACHE['qids']:
        return CACHE['qids'][key]
    data = _wikidata_request({"action":"wbsearchentities", "search":title, "language":lang, "format":"json"})
    qid = data.get('search', [{}])[0].get('id')
    CACHE['qids'][key] = qid
    return qid

def get_claims(qid, prop):
    cache_key = f"{qid}:{prop}"
    if cache_key in CACHE['props']:
        return CACHE['props'][cache_key]
    ents = _wikidata_request({"action":"wbgetentities", "ids":qid, "props":"claims", "format":"json"})
    claims = ents['entities'][qid]['claims'].get(prop, [])
    ids = [c['mainsnak']['datavalue']['value']['id'] for c in claims if 'datavalue' in c['mainsnak']]
    CACHE['props'][cache_key] = ids
    return ids

def resolve_labels(qids):
    missing = [q for q in qids if q and q not in CACHE['labels']]
    if missing:
        data = _wikidata_request({
            "action":"wbgetentities", "ids":"|".join(missing),
            "props":"labels", "languages":"en,vi,th,ko,ja,zh", "format":"json"
        })
        for qid, ent in data.get('entities', {}).items():
            labels = ent.get('labels', {})
            # prefer English
            label = labels.get('en', {}).get('value') or next(iter(labels.values()))['value']
            CACHE['labels'][qid] = label
    return [CACHE['labels'].get(q) for q in qids]

# --- FALLBACK: WIKIPEDIA INFOBOX ---
def scrape_infobox(title):
    url = f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"
    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
    except:
        return None, None
    soup = BeautifulSoup(resp.text, 'html.parser')
    table = soup.find('table', class_='infobox')
    sport = league = None
    for row in table.find_all('tr') if table else []:
        th, td = row.find('th'), row.find('td')
        if not (th and td):
            continue
        key = th.get_text(strip=True).lower()
        val = td.get_text(strip=True)
        if 'sport' in key and not sport:
            sport = val
        if ('league' in key or 'competition' in key) and not league:
            league = val
    return sport, league

# --- ENRICHMENT ---
def enrich(topic):
    lang = detect(topic)
    qid = get_qid(topic, lang)
    sport_ids = get_claims(qid, 'P641')
    league_ids = get_claims(qid, 'P118')
    sports = resolve_labels(sport_ids)
    leagues = resolve_labels(league_ids)
    sport = sports[0] if sports else None
    league = leagues[0] if leagues else None
    if not (sport and league):
        fb_s, fb_l = scrape_infobox(topic)
        sport = sport or fb_s
        league = league or fb_l
    return sport, league

# --- SCRAPER ---
def scrape_trends(geo='KR', category=17, hl='en'):
    base = f"https://trends.google.com/trending?geo={geo}&category={category}&hl={hl}"
    rows = []
    with sync_playwright() as p:
        page = p.chromium.launch().new_page()
        page.goto(base, timeout=60000)
        # wait for content
        page.wait_for_selector("table tbody tr, div.mZ3RIc", timeout=10000)
        page_num = 1
        while True:
            locator = page.locator("table tbody tr")
            batch = extract_rows(locator) or extract_cards(page)
            rows.extend(batch)
            btn = page.get_by_role('button', name='Go to next page')
            if not btn.count() or btn.first.is_disabled():
                break
            btn.first.click()
            page.wait_for_timeout(2000)
            page_num += 1
        page.context.close()
    return rows

# --- ROW PARSERS ---
def extract_rows(rows):
    out = []
    for i in range(rows.count()):
        r = rows.nth(i)
        if not r.is_visible():
            continue
        cells = r.locator('td')
        if cells.count() < 5:
            continue
        title = cells.nth(1).inner_text().split()[0]
        volume = cells.nth(2).inner_text().split()[0]
        started, ended = parse_times(cells.nth(3).inner_text())
        url = make_explore_url(title)
        breakdown = parse_breakdown(cells.nth(4))
        out.append([title, volume, started, ended, url, breakdown])
    return out

def extract_cards(page):
    cards = page.locator('div.mZ3RIc')
    count = cards.count()
    if count == 0:
        return []
    out = []
    for i in range(count):
        c = cards.nth(i)
        try:
            title = c.locator('span.mUIrbf-vQzf8d').inner_text(timeout=5000)
            volume = c.locator('div.search-count-title').inner_text(timeout=5000)
            started, ended = parse_times(c.locator('div.vdw3Ld').inner_text(timeout=3000))
            url = make_explore_url(title)
            breakdown = parse_breakdown(c.locator('div.lqv0Cb'))
            out.append([title, volume, started, ended, url, breakdown])
        except PlaywrightTimeoutError:
            # Skip cards that don't match expected format
            continue
    return out

# --- HELPERS ---
def parse_times(text):
    parts = [l for l in text.split() if l.lower() not in ('trending_up','timelapse')]
    return (parts[0], parts[1] if len(parts)>1 else '')

def parse_breakdown(elem):
    texts = elem.locator('span').all_inner_texts()
    return ', '.join(t.strip() for t in texts if t.strip())

def make_explore_url(title, geo='KR'):
    q = quote(title)
    return f"https://trends.google.com/trends/explore?q={q}&date=now%201-d&geo={geo}&hl=en"

# --- GOOGLE SHEETS ---
def connect_sheet(name):
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        json.loads(os.environ['GOOGLE_SA_JSON']),
        ['https://spreadsheets.google.com/feeds','https://www.googleapis.com/auth/drive']
    )
    return gspread.authorize(creds).open(name).sheet1

# --- MAIN ---
def main():
    sheet = connect_sheet('Trends')
    data = scrape_trends()
    enriched = [row + list(enrich(row[0])) for row in data]
    save_cache()
    sheet.clear()
    sheet.append_rows(enriched, value_input_option='RAW')

if __name__ == '__main__':
    main()
