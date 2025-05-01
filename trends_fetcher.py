#!/usr/bin/env python3
import os
import json
import time
from urllib.parse import quote

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# ─── Google Sheets (2nd tab) ───────────────────────────────────────────
def connect_to_sheet(sheet_name):
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds_dict = json.loads(os.environ["GOOGLE_SA_JSON"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client.open(sheet_name).get_worksheet(1)

# ─── Dismiss cookie consent if present ───────────────────────────────────
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

# ─── Extract rows from the table on current page ─────────────────────────
def extract_table_rows(page):
    try:
        page.wait_for_selector("table tbody tr", timeout=20000)
    except PlaywrightTimeoutError:
        print("⚠️ No table rows found on this page.")
        return []

    rows = page.locator("table tbody tr")
    count = rows.count()
    print(f"🔢 Found {count} rows on this page")
    data = []

    for i in range(count):
        tr = rows.nth(i)
        if not tr.is_visible():
            continue
        cells = tr.locator("td")
        if cells.count() < 5:
            continue

        title  = cells.nth(1).inner_text().split("\n")[0].strip()
        volume = cells.nth(2).inner_text().split("\n")[0].strip()

        raw = cells.nth(3).inner_text().split("\n")
        parts = [l for l in raw if l and l.lower() not in ("trending_up", "timelapse")]
        started = parts[0].strip() if parts else ""
        ended   = parts[1].strip() if len(parts) > 1 else ""

        # toggle for absolute date
        toggle = cells.nth(3).locator("div.vdw3Ld")
        target_publish = ended
        try:
            toggle.click(); time.sleep(0.2)
            flip = cells.nth(3).inner_text().split("\n")
            p2 = [l for l in flip if l and l.lower() not in ("trending_up", "timelapse")]
            target_publish = p2[0].strip() if p2 else ended
        finally:
            try: toggle.click(); time.sleep(0.1)
            except: pass

        spans = cells.nth(4).locator("span.mUIrbf-vQzf8d, span.Gwdjic")
        breakdown = ", ".join(t.strip() for t in spans.all_inner_texts() if t.strip())

        q = quote(title)
        explore_url = (
            "https://trends.google.com/trends/explore"
            f"?q={q}&date=now%201-d&geo=KR&hl=ko"
        )

        data.append([
            title,
            volume,
            started,
            ended,
            explore_url,
            target_publish,
            breakdown
        ])

    return data

# ─── Scrape all pages: load, scrape, click next until disabled ──────────
def scrape_pages():
    all_data = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox","--disable-setuid-sandbox"]
        )
        context = browser.new_context(
            locale="en-US",
            viewport={"width":1280,"height":800},
            extra_http_headers={"Accept-Language":"en-US,en;q=0.9"}
        )
        page = context.new_page()

        # 1) Load first page
        url = "https://trends.google.com/trending?geo=KR&category=17"
        page.goto(url, timeout=60000)
        page.wait_for_load_state("networkidle")
        print("✅ First page loaded")

        dismiss_cookie_banner(page)

        # 2) Scrape first page
        batch = extract_table_rows(page)
        all_data.extend(batch)

        # 3) Pagination loop
        while True:
            btn = page.locator(
                'button[aria-label="Next page"], button[aria-label="Go to next page"]'
            )
            if btn.count() == 0:
                print("🚫 No next-page button – stopping")
                break

            first = btn.first
            disabled_attr = first.get_attribute("disabled")
            aria_disabled = first.get_attribute("aria-disabled") or "false"
            if disabled_attr is not None or aria_disabled.lower() == "true":
                print("✅ Next button disabled – end reached")
                break

            first.click()
            print("⏳ Clicked next – waiting for new data…")
            page.wait_for_timeout(2000)
            page.wait_for_selector("table tbody tr", timeout=20000)

            batch = extract_table_rows(page)
            all_data.extend(batch)

        browser.close()
    return all_data

# ─── Chunk helper ─────────────────────────────────────────────────────────
def chunk(flat, n=7):
    return [flat[i:i+n] for i in range(0, len(flat), n)]

# ─── Main Entrypoint ────────────────────────────────────────────────────────
def main():
    sheet = connect_to_sheet("Trends")
    scraped = scrape_pages()
    flat = [item for row in scraped for item in row]
    rows = chunk(flat, 7)

    sheet.clear()
    header = [
        "Trending Topic","Search Volume","Started Time","Ended Time",
        "Explore Link","Target Publish Date","Trend Breakdown"
    ]
    sheet.append_rows([header] + rows, value_input_option="RAW")
    print(f"✅ {len(rows)} total trends saved")

if __name__=="__main__":
    main()
