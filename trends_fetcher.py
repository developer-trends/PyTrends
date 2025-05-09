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
        resp.raise_for_status()
        return resp

# --- WIKIDATA UTILITIES ---
def lookup_qid(term, lang="en"):
    cache_key = f"{lang}:{term}"
    if cache_key in CACHE['term_to_qid']:
        return CACHE['term_to_qid'][cache_key]
    params = {"action": "wbsearchentities", "search": term, "language": lang, "format": "json"}
    resp = wikidata_request(params)
    results = resp.json().get("search", [])
    qid = results[0]["id"] if results else None
    CACHE['term_to_qid'][cache_key] = qid
    time.sleep(0.05)
    return qid

def get_entity_props(qid):
    if qid in CACHE['qid_props']:
        return CACHE['qid_props'][qid]
    params = {"action": "wbgetentities", "ids": qid, "props": "claims", "format": "json"}
    resp = wikidata_request(params)
    claims = resp.json()["entities"][qid]["claims"]
    def extract(pid):
        return [c["mainsnak"]["datavalue"]["value"]["id"] for c in claims.get(pid, []) if "datavalue" in c["mainsnak"]]
    props = {"sports": extract("P641"), "leagues": extract("P118")}
    CACHE['qid_props'][qid] = props
    time.sleep(0.05)
    return props

def resolve_labels(qids):
    missing = [q for q in qids if q not in CACHE['qid_label']]
    if missing:
        params = {"action": "wbgetentities", "ids": "|".join(missing), "props": "labels", "languages": "en,vi,th,ko,ja,zh", "format": "json"}
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
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
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
    print(f"🔢 [table] found {total} rows – including all rows")
    out = []
    for i in range(total):
        row = rows.nth(i)
        if not row.is_visible():
            continue
        cells = row.locator("td")
        if cells.count() < 5:
            continue
        title = cells.nth(1).inner_text().split("\n")[0].strip()
        volume = cells.nth(2).inner_text().split("\n")[0].strip()
        raw = cells.nth(3).inner_text().split("\n")
        parts = [l for l in raw if l and l.lower() not in ("trending_up","timelapse")]
        started = parts[0].strip() if parts else ""
        ended = parts[1].strip() if len(parts)>1 else ""
        toggle = cells.nth(3).locator("div.vdw3Ld")
        target_publish = ended
        try:
            toggle.click(); time.sleep(0.2)
            raw2 = cells.nth(3).inner_text().split("\n")
            p2 = [l for l in raw2 if l and l.lower() not in ("trending_up","timelapse")]
            if p2: target_publish = p2[0].strip()
        finally:
            try: toggle.click(); time.sleep(0.1)
            except: pass
        spans = cells.nth(4).locator("span.mUIrbf-vQzf8d, span.Gwdjic")
        breakdown = ", ".join(s.strip() for s in spans.all_inner_texts() if s.strip())
        q = quote(title)
        explore_url = ("https://trends.google.com/trends/explore" f"?q={q}&date=now%201-d&geo=KR&hl=en")
        out.append([title, volume, started, ended, explore_url, target_publish, breakdown])
    return out

def extract_card_rows(page):
    try:
        page.wait_for_selector("div.mZ3RIc", timeout=5000)
    except PlaywrightTimeoutError:
        return []
    cards = page.locator("div.mZ3RIc")
    total = cards.count()
    print(f"🃏 [card] found {total} cards – including all cards")
    out = []
    for i in range(total):
        c = cards.nth(i)
        title = c.locator("span.mUIrbf-vQzf8d").all_inner_texts()[0].strip()
        volume = c.locator("div.search-count-title").inner_text().strip()
        raw = c.locator("div.vdw3Ld").locator("xpath=..").inner_text().split("\n")
        parts = [l for l in raw if l and l.lower() not in ("trending_up","timelapse")]
        started = parts[0].strip() if parts else ""
        ended = parts[1].strip() if len(parts)>1 else ""
        toggle = c.locator("div.vdw3Ld")
        target_publish = ended
        try:
            toggle.click(); time.sleep(0.2)
            raw2 = c.locator("div.vdw3Ld").locator("xpath=..").inner_text().split("\n")
            p2 = [l for l in raw2 if l and l.lower() not in ("trending_up","timelapse")]
            if p2: target_publish = p2[0].strip()
        finally:
            try: toggle.click(); time.sleep(0.1)
            except: pass
        spans = c.locator("div.lqv0Cb span.mUIrbf-vQzf8d, div.lqv0Cb span.Gwdjic")
        breakdown = ", ".join(s.strip() for s in spans.all_inner_texts() if s.strip())
        q = quote(title)
        explore_url = ("https://trends.google.com/trends/explore" f"?q={q}&date=now%201-d&geo=KR&hl=en")
        out.append([title, volume, started, ended, explore_url, target_publish, breakdown])
    return out

def scrape_all_pages():
    all_rows = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox","--disable-setuid-sandbox"])
        page = browser.new_page()
        page.goto("https://trends.google.com/trending?geo=KR&category=17&hl=en", timeout=60000)
        # Instead of waiting for networkidle, wait for either table or cards to appear
        try:
            page.wait_for_selector("table tbody tr", timeout=10000)
        except PlaywrightTimeoutError:
            try:
                page.wait_for_selector("div.mZ3RIc", timeout=10000)
            except PlaywrightTimeoutError:
                print("⚠️ Neither table nor cards appeared; proceeding anyway")
        print("First page ready for scraping")
        dismiss_cookie_banner(page)

        page_num = 1
        while True:
            print(f"📄 Scraping page {page_num}")
            # scrape whichever layout is present
            batch = extract_table_rows(page)
            if not batch:
                batch = extract_card_rows(page)
            print(f"  → got {len(batch)} rows")
            all_rows.extend(batch)

            next_btn = page.get_by_role("button", name="Go to next page")
            if not next_btn.count() or next_btn.first.is_disabled():
                print("No more pages")
                break

            next_btn.first.scroll_into_view_if_needed()
            next_btn.first.click()
            # wait for new content rows to load
            time.sleep(2)
            page_num += 1
        browser.close()
    return all_rows

# --- MAIN ENTRYPOINT ---
def main():
    sheet = connect_to_sheet("Trends")
    rows = scrape_all_pages()
    enriched = enrich_rows(rows)
    sheet.clear()
    sheet.append_rows(enriched, value_input_option="RAW")
    print(f"✅ {len(enriched)} trends saved to sheet (sport → Col H, league → Col I)")

if __name__ == "__main__":
    main()
