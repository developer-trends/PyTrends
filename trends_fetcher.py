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
    data = _wikidata_request({"action":"wbsearchentities","search":title,"language":lang,"format":"json"})
    qid = data.get('search',[{}])[0].get('id') if data.get('search') else None
    CACHE['qids'][key] = qid
    return qid

def get_claims(qid, prop):
    cache_key = f"{qid}:{prop}"
    if cache_key in CACHE['props']:
        return CACHE['props'][cache_key]
    data = _wikidata_request({"action":"wbgetentities","ids":qid,"props":"claims","format":"json"})
    claims = data['entities'][qid]['claims'].get(prop, [])
    ids = [c['mainsnak']['datavalue']['value']['id'] for c in claims if 'datavalue' in c['mainsnak']]
    CACHE['props'][cache_key] = ids
    return ids

def resolve_labels(qids):
    missing = [q for q in qids if q and q not in CACHE['labels']]
    if missing:
        data = _wikidata_request({"action":"wbgetentities","ids":"|".join(missing),"props":"labels","languages":"en,vi,th,ko,ja,zh","format":"json"})
        for qid, ent in data.get('entities',{}).items():
            lbls = ent.get('labels',{})
            CACHE['labels'][qid] = lbls.get('en',{}).get('value') or next(iter(lbls.values()))['value']
    return [CACHE['labels'].get(q) for q in qids]

# --- WIKIPEDIA INFOBOX FALLBACK ---
def scrape_infobox(title):
    url = f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"
    try:
        r = requests.get(url, timeout=5); r.raise_for_status()
    except:
        return None, None
    soup = BeautifulSoup(r.text,'html.parser')
    tbl = soup.find('table',class_='infobox')
    s=l=None
    for row in tbl.find_all('tr') if tbl else []:
        th,td=row.find('th'),row.find('td')
        if not(th and td): continue
        k=th.get_text(strip=True).lower();v=td.get_text(strip=True)
        if 'sport' in k and not s: s=v
        if ('league' in k or 'competition' in k) and not l: l=v
    return s,l

# --- ENRICHMENT (BATCHED) ---
def enrich_rows(rows):
    titles = [r[0] for r in rows]
    uniq = list(dict.fromkeys(titles))
    qids = {t: (get_qid(t, detect(t)) or get_qid(t,'en')) for t in uniq}
    props = {qid: {'sports':get_claims(qid,'P641'),'leagues':get_claims(qid,'P118')} for qid in set(qids.values()) if qid}
    all_s = [q for p in props.values() for q in p['sports']]
    all_l = [q for p in props.values() for q in p['leagues']]
    s_lbl = dict(zip(all_s, resolve_labels(all_s)))
    l_lbl = dict(zip(all_l, resolve_labels(all_l)))
    enriched=[]
    for r in rows:
        t=r[0];qid=qids.get(t)
        sp = s_lbl.get(props.get(qid,{}).get('sports',[None])[0])
        lg = l_lbl.get(props.get(qid,{}).get('leagues',[None])[0])
        if not(sp and lg): sx,lx = scrape_infobox(t);sp=sp or sx;lg=lg or lx
        enriched.append(r+[sp,lg])
    save_cache()
    return enriched

# --- SCRAPER & PARSERS ---
def extract_table_rows(page):
    try:
        page.wait_for_selector("table tbody tr", state="attached", timeout=5000)
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
        title = cells.nth(1).inner_text().split("
")[0].strip()
        vol   = cells.nth(2).inner_text().split("
")[0].strip()
        raw   = cells.nth(3).inner_text().split("
")
        parts = [l for l in raw if l and l.lower() not in ("trending_up","timelapse")]
        start = parts[0].strip() if parts else ""
        end   = parts[1].strip() if len(parts)>1 else ""
        url   = f"https://trends.google.com/trends/explore?q={quote(title)}&date=now%201-d&geo=KR&hl=en"
        breakdown = ", ".join(cells.nth(4).locator("span").all_inner_texts())
        out.append([title, vol, start, end, url, breakdown])
    return out



# --- MAIN ---
def main():
    sheet=gspread.authorize(ServiceAccountCredentials.from_json_keyfile_dict(json.loads(os.environ['GOOGLE_SA_JSON']),['https://spreadsheets.google.com/feeds','https://www.googleapis.com/auth/drive'])).open('Trends').sheet1
    rows=scrape_all_pages()
    final=enrich_rows(rows)
    sheet.clear()
    # columns A–G = original, H = sport, I = league
    sheet.append_rows(final, value_input_option='RAW')

if __name__=='__main__':
    main()
