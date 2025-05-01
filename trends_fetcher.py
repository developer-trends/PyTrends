#!/usr/bin/env python3
import os
import json
import time
from urllib.parse import quote

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# ---- Google Sheet Setup ----
def connect_to_sheet(sheet_name):
    scope = [
        'https://spreadsheets.google.com/feeds',
        'https://www.googleapis.com/auth/drive',
    ]
    creds_dict = json.loads(os.environ["GOOGLE_SA_JSON"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client.open(sheet_name).get_worksheet(1)  # 2nd tab

# ---- Extract Trends from the Table ----
def extract_trend_rows(page):
    try:
        page.wait_for_selector("table tbody tr", timeout=30000)
    except PlaywrightTimeoutError:
        print("⚠️ No table rows found on the page.")
        return []

    rows = page.locator("table tbody tr")
    total = rows.count()
    print(f"📝 Found {total} rows in the table")

    data = []
    for i in range(total):
        row = rows.nth(i)
        if not row.is_visible():
            continue
        cells = row.locator("td")
        if cells.count() < 5:
            continue

        title  = cells.nth(1).inner_text().split("\n")[0].strip()
        volume = cells.nth(2).inner_text().split("\n")[0].strip()

        cell3 = cells.nth(3)
        lines = [
            l for l in cell3.inner_text().split("\n")
            if l and l.lower() not in ("trending_up", "timelapse")
        ]
        started = lines[0].strip() if lines else ""
        ended   = lines[1].strip() if len(lines) > 1 else ""

        toggle = cell3.locator("div.vdw3Ld")
        try:
            toggle.click()
            time.sleep(0.2)
            abs_lines = [
                l for l in cell3.inner_text().split("\n")
                if l and l.lower() not in ("trending_up", "timelapse")
            ]
            target_publish = abs_lines[0].strip() if abs_lines else ended
        finally:
            try:
                toggle.click()
                time.sleep(0.1)
            except:
                pass

        td4 = cells.nth(4)
        span_texts = td4.locator("span.mUIrbf-vQzf8d, span.Gwdjic")\
                        .all_inner_texts()
        breakdown = ", ".join(t.strip() for t in span_texts if t.strip())

        q = quote(title)
        explore_url = (
            "https://trends.google.com/trends/explore"
            f"?q={q}&date=now%201-d&geo=KR&hl=ko"
        )

        data.append([
            title, volume, started, ended,
            explore_url, target_publish, breakdown
        ])

    return data

# ---- Scrape All Pages ----
def scrape_pages():
    all_data = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox","--disable-setuid-sandbox"]
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36"
            ),
            locale="ko-KR",
            viewport={"width":1280, "height":800},
            extra_http_headers={"Accept-Language":"ko-KR,en-US;q=0.9"}
        )
        page = context.new_page()
        page.goto(
            "https://trends.google.com/trending?geo=KR&category=17",
            timeout=60000
        )
        print("✅ Page 1 loaded")
        page.wait_for_load_state("networkidle")

        while True:
            batch = extract_trend_rows(page)
            all_data += batch
            nxt = page.locator(
                'button[aria-label="Go to next page"]:not([disabled])'
            )
            if nxt.count() == 0:
                break
            nxt.click()
            print("⏳ Next page…")
            page.wait_for_timeout(2000)

        browser.close()
    return all_data

def chunk_into_rows(flat, n=7):
    return [flat[i:i+n] for i in range(0, len(flat), n)]

def main():
    sheet = connect_to_sheet("Trends")
    scraped = scrape_pages()
    flat    = [item for row in scraped for item in row]
    rows    = chunk_into_rows(flat, 7)

    sheet.clear()
    header = [
        "Trending Topic","Search Volume","Started Time","Ended Time",
        "Explore Link","Target Publish Date","Trend Breakdown"
    ]
    sheet.append_rows([header] + rows, value_input_option="RAW")
    print(f"✅ {len(rows)} trends written (2nd tab).")

if __name__ == "__main__":
    main()
