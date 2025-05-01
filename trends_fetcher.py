#!/usr/bin/env python3
import os, json, time
from urllib.parse import quote
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# ─── Google Sheets (2nd tab) ───────────────────────────────────────────────────
def connect_to_sheet(sheet_name):
    scope = [
      'https://spreadsheets.google.com/feeds',
      'https://www.googleapis.com/auth/drive',
    ]
    creds_dict = json.loads(os.environ["GOOGLE_SA_JSON"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client.open(sheet_name).get_worksheet(1)

# ─── Dismiss cookie consent if present ────────────────────────────────────────
def dismiss_cookie_banner(page):
    for btn_label in ("Accept all","I agree","AGREE"):
        try:
            btn = page.get_by_role("button", name=btn_label)
            if btn.count():
                btn.first.click()
                page.wait_for_timeout(800)
                print("🛡️ Dismissed cookie banner")
                return
        except:
            pass

# ─── Table layout extractor ──────────────────────────────────────────────────
def extract_table_rows(page):
    page.wait_for_selector("table tbody tr", timeout=20000)
    rows = page.locator("table tbody tr")
    out  = []
    for i in range(rows.count()):
        row = rows.nth(i)
        if not row.is_visible(): continue
        cells = row.locator("td")
        if cells.count() < 5: continue

        title  = cells.nth(1).inner_text().split("\n")[0].strip()
        volume = cells.nth(2).inner_text().split("\n")[0].strip()

        # started / ended
        raw   = cells.nth(3).inner_text().split("\n")
        parts = [l for l in raw if l and l.lower() not in ("trending_up","timelapse")]
        started = parts[0].strip() if parts else ""
        ended   = parts[1].strip() if len(parts)>1 else ""

        # target publish (toggle absolute)
        toggle = cells.nth(3).locator("div.vdw3Ld")
        try:
            toggle.click(); time.sleep(0.2)
            raw2   = cells.nth(3).inner_text().split("\n")
            p2     = [l for l in raw2 if l and l.lower() not in ("trending_up","timelapse")]
            target_publish = p2[0].strip() if p2 else ended
        finally:
            try: toggle.click(); time.sleep(0.1)
            except: pass

        # breakdown
        td4 = cells.nth(4)
        spans = td4.locator("span.mUIrbf-vQzf8d, span.Gwdjic")
        breakdown = ", ".join(s.strip() for s in spans.all_inner_texts() if s.strip())

        # explore link
        q = quote(title)
        explore_url = (
            "https://trends.google.com/trends/explore"
            f"?q={q}&date=now%201-d&geo=KR&hl=ko"
        )

        out.append([title, volume, started, ended, explore_url, target_publish, breakdown])
    return out

# ─── Card layout extractor (fallback) ─────────────────────────────────────────
def extract_card_rows(page):
    page.wait_for_selector("div.mZ3RIc", timeout=20000)
    cards = page.locator("div.mZ3RIc")
    out   = []
    for i in range(cards.count()):
        c = cards.nth(i)
        title  = c.locator("button .mUIrbf-vQzf8d").inner_text().strip()
        volume = c.locator("div.search-count-title").inner_text().strip()

        info = c.locator("div.vdw3Ld").locator("xpath=..") \
                   .inner_text().split("\n")
        parts = [l for l in info if l and l.lower() not in ("trending_up","timelapse")]
        started = parts[0].strip() if parts else ""
        ended   = parts[1].strip() if len(parts)>1 else ""

        toggle = c.locator("div.vdw3Ld")
        try:
            toggle.click(); time.sleep(0.2)
            info2 = c.locator("div.vdw3Ld").locator("xpath=..") \
                     .inner_text().split("\n")
            p2    = [l for l in info2 if l and l.lower() not in ("trending_up","timelapse")]
            target_publish = p2[0].strip() if p2 else ended
        finally:
            try: toggle.click(); time.sleep(0.1)
            except: pass

        spans = c.locator("div.lqv0Cb span.mUIrbf-vQzf8d, div.lqv0Cb span.Gwdjic")
        breakdown = ", ".join(t.strip() for t in spans.all_inner_texts() if t.strip())

        q = quote(title)
        explore_url = (
            "https://trends.google.com/trends/explore"
            f"?q={q}&date=now%201-d&geo=KR&hl=ko"
        )

        out.append([title, volume, started, ended, explore_url, target_publish, breakdown])
    return out

# ─── Main scraper driver ──────────────────────────────────────────────────────
def scrape_pages():
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
          headless=True,
          args=["--no-sandbox","--disable-setuid-sandbox"]
        )
        ctx = browser.new_context(
          locale="ko-KR",
          viewport={"width":1280,"height":800},
          extra_http_headers={"Accept-Language":"ko-KR,en-US;q=0.9"},
          user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36"
          )
        )
        page = ctx.new_page()
        page.goto("https://trends.google.com/trending?geo=KR&category=17", timeout=60000)
        page.wait_for_load_state("networkidle")
        print("✅ Page loaded")

        dismiss_cookie_banner(page)

        # *First* look for a table; if found, use table extractor.
        if page.locator("table tbody tr").count() > 0:
            print("🔢 Table layout detected")
            extractor = extract_table_rows
        else:
            print("🃏 Card layout detected")
            extractor = extract_card_rows

        # paginate
        while True:
            batch = extractor(page)
            results += batch
            nxt = page.locator('button[aria-label="Go to next page"]:not([disabled])')
            if nxt.count() == 0:
                break
            nxt.click()
            print("⏳ Next page…")
            page.wait_for_timeout(2000)

        browser.close()
    return results

def chunk(flat, n=7):
    return [flat[i:i+n] for i in range(0, len(flat), n)]

def main():
    sheet   = connect_to_sheet("Trends")
    scraped = scrape_pages()
    flat    = [v for row in scraped for v in row]
    rows    = chunk(flat, 7)

    sheet.clear()
    header = [
      "Trending Topic","Search Volume","Started Time","Ended Time",
      "Explore Link","Target Publish Date","Trend Breakdown"
    ]
    sheet.append_rows([header] + rows, value_input_option="RAW")
    print(f"✅ {len(rows)} trends saved.")

if __name__=="__main__":
    main()
