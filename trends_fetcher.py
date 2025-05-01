#!/usr/bin/env python3
import os, json, time
from urllib.parse import quote
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# ─── Google Sheets (2nd tab) ───────────────────────────────────────────────────
def connect_to_sheet(sheet_name):
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds_dict = json.loads(os.environ["GOOGLE_SA_JSON"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    # 2nd tab:
    return client.open(sheet_name).get_worksheet(1)

# ─── Cookie banner (rare) ─────────────────────────────────────────────────────
def dismiss_cookie_banner(page):
    for label in ("Accept all","I agree","AGREE"):
        try:
            btn = page.get_by_role("button", name=label)
            if btn.count():
                btn.first.click()
                page.wait_for_timeout(800)
                print("🛡️ Dismissed cookie banner")
                return
        except:
            pass

# ─── Table extractor ──────────────────────────────────────────────────────────
def extract_table_rows(page):
    try:
        page.wait_for_selector("table tbody tr", timeout=5000)
    except PlaywrightTimeoutError:
        return []

    rows = page.locator("table tbody tr")
    out = []
    print(f"🔢 [table] found {rows.count()} rows")
    for i in range(rows.count()):
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

        # target publish (click toggle)
        toggle = cells.nth(3).locator("div.vdw3Ld")
        target_publish = ended
        try:
            toggle.click(); time.sleep(0.2)
            raw2 = cells.nth(3).inner_text().split("\n")
            p2 = [l for l in raw2 if l and l.lower() not in ("trending_up","timelapse")]
            if p2:
                target_publish = p2[0].strip()
        finally:
            try: toggle.click(); time.sleep(0.1)
            except: pass

        spans = cells.nth(4).locator("span.mUIrbf-vQzf8d, span.Gwdjic")
        breakdown = ", ".join(s.strip() for s in spans.all_inner_texts() if s.strip())

        q = quote(title)
        explore_url = (
            "https://trends.google.com/trends/explore"
            f"?q={q}&date=now%201-d&geo=KR&hl=en"
        )

        out.append([
            title, volume, started, ended,
            explore_url, target_publish, breakdown
        ])
    return out

# ─── Card extractor fallback ───────────────────────────────────────────────────
def extract_card_rows(page):
    try:
        page.wait_for_selector("div.mZ3RIc", timeout=5000)
    except PlaywrightTimeoutError:
        return []

    cards = page.locator("div.mZ3RIc")
    print(f"🃏 [card] found {cards.count()} cards")
    out = []
    for i in range(cards.count()):
        c = cards.nth(i)
        t = c.locator("span.mUIrbf-vQzf8d").all_inner_texts()
        title = t[0].strip() if t else ""
        volume = c.locator("div.search-count-title").inner_text().strip()

        raw = c.locator("div.vdw3Ld").locator("xpath=..").inner_text().split("\n")
        parts = [l for l in raw if l and l.lower() not in ("trending_up","timelapse")]
        started = parts[0].strip() if parts else ""
        ended   = parts[1].strip() if len(parts)>1 else ""

        toggle = c.locator("div.vdw3Ld")
        target_publish = ended
        try:
            toggle.click(); time.sleep(0.2)
            info2 = c.locator("div.vdw3Ld").locator("xpath=..").inner_text().split("\n")
            p2 = [l for l in info2 if l and l.lower() not in ("trending_up","timelapse")]
            if p2:
                target_publish = p2[0].strip()
        finally:
            try: toggle.click(); time.sleep(0.1)
            except: pass

        spans = c.locator("div.lqv0Cb span.mUIrbf-vQzf8d, div.lqv0Cb span.Gwdjic")
        breakdown = ", ".join(s.strip() for s in spans.all_inner_texts() if s.strip())

        q = quote(title)
        explore_url = (
            "https://trends.google.com/trends/explore"
            f"?q={q}&date=now%201-d&geo=KR&hl=en"
        )

        out.append([
            title, volume, started, ended,
            explore_url, target_publish, breakdown
        ])
    return out

# ─── Pagination loop ──────────────────────────────────────────────────────────
def scrape_all_pages():
    all_rows = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True,
            args=["--no-sandbox","--disable-setuid-sandbox"]
        )
        page = browser.new_page()
        # force English UI
        page.goto(
            "https://trends.google.com/trending?geo=KR&category=17&hl=en",
            timeout=60000
        )
        page.wait_for_load_state("networkidle")
        print("✅ First page loaded")

        dismiss_cookie_banner(page)

        page_num = 1
        while True:
            print(f"📄 Scraping page {page_num}")

            # try table first, fallback to cards
            batch = extract_table_rows(page)
            if not batch:
                print("⚙️  table extractor returned 0 → falling back to cards")
                batch = extract_card_rows(page)

            print(f"  → got {len(batch)} rows")
            all_rows.extend(batch)

            # next button
            btn = page.locator('button[aria-label="Go to next page"]')
            if btn.count() == 0 or btn.first.is_disabled():
                print("✅ No more pages (▶ gone/disabled)")
                break

            btn.first.scroll_into_view_if_needed()
            btn.first.click()
            print("⏳ Clicked ▶ → waiting 3 s…")
            time.sleep(3)
            page_num += 1

        browser.close()
    return all_rows

# ─── Main: scrape & batch-write ─────────────────────────────────────────────────
def main():
    sheet = connect_to_sheet("Trends")
    rows  = scrape_all_pages()

    header = [
        "Trending Topic","Search Volume","Started Time","Ended Time",
        "Explore Link","Target Publish Date","Trend Breakdown"
    ]
    sheet.clear()
    sheet.append_rows([header] + rows, value_input_option="RAW")
    print(f"✅ {len(rows)} total trends saved to Google Sheet (2nd tab)")

if __name__=="__main__":
    main()
