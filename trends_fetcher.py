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

def get_qid(title, lang='en'):
    key = f"{lang}:{title}"
    if key in CACHE['qids']:
        return CACHE['qids'][key]
    data = _wikidata_request({
        "action":"wbsearchentities",
        "search":title,
        "language":lang,
        "format":"json"
    })
    results = data.get('search', [])
    qid = results[0].get('id') if results else None
    CACHE['qids'][key] = qid
    return qid

def get_claims(qid, prop):
    cache_key = f"{qid}:{prop}"
    if cache_key in CACHE['props']:
        return CACHE['props'][cache_key]
    data = _wikidata_request({
        "action":"wbgetentities",
        "ids":qid,
        "props":"claims",
        "format":"json"
    })
    claims = data['entities'][qid]['claims'].get(prop, [])
    ids = [c['mainsnak']['datavalue']['value']['id']
           for c in claims if 'datavalue' in c['mainsnak']]
    CACHE['props'][cache_key] = ids
    return ids

def resolve_labels(qids):
    missing = [q for q in qids if q and q not in CACHE['labels']]
    if missing:
        data = _wikidata_request({
            "action":"wbgetentities",
            "ids":"|".join(missing),
            "props":"labels",
            "languages":"en,vi,th,ko,ja,zh",
            "format":"json"
        })
        for qid, ent in data.get('entities', {}).items():
            lbls = ent.get('labels', {})
            CACHE['labels'][qid] = (
                lbls.get('en', {}).get('value')
                or next(iter(lbls.values()))['value']
            )
    return [CACHE['labels'].get(q) for q in qids]

# --- WIKIPEDIA INFOBOX FALLBACK ---
def scrape_infobox(title):
    url = f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"
    try:
        r = requests.get(url, timeout=5)
        r.raise_for_status()
    except:
        return None, None
    soup = BeautifulSoup(r.text, 'html.parser')
    tbl = soup.find('table', class_='infobox')
    sport = league = None
    for row in tbl.find_all('tr') if tbl else []:
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

# --- ENRICHMENT (BATCHED) ---
def enrich_rows(rows):
    titles = [r[0] for r in rows]
    uniq = list(dict.fromkeys(titles))
    # lookup QIDs with auto + EN fallback
    qids = {
        t: (get_qid(t, detect(t)) or get_qid(t, 'en'))
        for t in uniq
    }
    # fetch all claims
    props = {
        qid: {
            'sports': get_claims(qid, 'P641'),
            'leagues': get_claims(qid, 'P118')
        }
        for qid in set(qids.values()) if qid
    }
    # batch resolve labels
    all_s = [q for p in props.values() for q in p['sports']]
    all_l = [q for p in props.values() for q in p['leagues']]
    s_lbl = dict(zip(all_s, resolve_labels(all_s)))
    l_lbl = dict(zip(all_l, resolve_labels(all_l)))

    enriched = []
    for row in rows:
        t = row[0]
        q = qids.get(t)
        sp = s_lbl.get(props.get(q, {}).get('sports', [None])[0])
        lg = l_lbl.get(props.get(q, {}).get('leagues', [None])[0])
        if not (sp and lg):
            fb_s, fb_l = scrape_infobox(t)
            sp = sp or fb_s
            lg = lg or fb_l
        enriched.append(row + [sp, lg])

    save_cache()
    return enriched

# --- PLAYWRIGHT SCRAPERS ---
def dismiss_cookie_banner(page):
    for label in ("Accept all","I agree","AGREE"):
        try:
            btn = page.get_by_role("button", name=label)
            if btn.count():
                btn.first.click()
                page.wait_for_timeout(800)
                return
        except:
            pass

def extract_table_rows(page):
    try:
        page.wait_for_selector("table tbody tr", timeout=5000)
    except PlaywrightTimeoutError:
        return []
    rows = page.locator("table tbody tr")
    total = rows.count()
    out = []
    for i in range(1, total):
        r = rows.nth(i)
        if not r.is_visible():
            continue
        cells = r.locator("td")
        if cells.count() < 5:
            continue
        title = cells.nth(1).inner_text().split("\\n")[0].strip()
        vol   = cells.nth(2).inner_text().split("\\n")[0].strip()
        raw   = cells.nth(3).inner_text().split("\\n")
        parts = [l for l in raw if l and l.lower() not in ("trending_up","timelapse")]
        start = parts[0].strip() if parts else ""
        end   = parts[1].strip() if len(parts)>1 else ""
        url   = (
            "https://trends.google.com/trends/explore"
            f"?q={quote(title)}&date=now%201-d&geo=KR&hl=en"
        )
        breakdown = ", ".join(cells.nth(4).locator("span").all_inner_texts())
        out.append([title, vol, start, end, url, breakdown])
    return out

def extract_card_rows(page):
    cards = page.locator("div.mZ3RIc").all()
    out = []
    for c in cards:
        txt = c.inner_text(timeout=3000)
        lines = [l.strip() for l in txt.split()]
        title = lines[0] if lines else ""
        vol   = lines[1] if len(lines) > 1 else ""
        start = end = ""
        url   = (
            "https://trends.google.com/trends/explore"
            f"?q={quote(title)}&date=now%201-d&geo=KR&hl=en"
        )
        breakdown = ", ".join(c.locator("span").all_inner_texts())
        out.append([title, vol, start, end, url, breakdown])
    return out

def scrape_all_pages():
    all_rows = []
    with sync_playwright() as p:
        page = p.chromium.launch(headless=True).new_page()
        page.goto(
            "https://trends.google.com/trends/trending?geo=KR&category=17&hl=en",
            timeout=60000
        )
        page.wait_for_timeout(2000)
        is_table = page.locator("table tbody tr").count() > 0

        while True:
            batch = (
                extract_table_rows(page)
                if is_table
                else extract_card_rows(page)
            )
            all_rows.extend(batch)

            btn = page.get_by_role("button", name="Go to next page")
            if not btn.count() or btn.first.is_disabled():
                break

            btn.first.click()
            page.wait_for_timeout(2000)
            is_table = page.locator("table tbody tr").count() > 0

        page.context.close()
    return all_rows

# --- MAIN ---
def main():
    sheet = gspread.authorize(
        ServiceAccountCredentials.from_json_keyfile_dict(
            json.loads(os.environ["GOOGLE_SA_JSON"]),
            ["https://spreadsheets.google.com/feeds",
             "https://www.googleapis.com/auth/drive"]
        )
    ).open("Trends").sheet1

    rows = scrape_all_pages()
    final = enrich_rows(rows)

    sheet.clear()
    # A–G original, H=sport, I=league
    sheet.append_rows(final, value_input_option="RAW")

if __name__ == "__main__":
    main()
