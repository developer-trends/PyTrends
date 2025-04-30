from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import time
from urllib.parse import quote
import os

# ---- Google Sheet Setup ----
def connect_to_sheet(sheet_name):
    scope = [
        'https://spreadsheets.google.com/feeds',
        'https://www.googleapis.com/auth/drive',
    ]
    import json
    json_creds = json.loads(os.environ["GOOGLE_SA_JSON"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(json_creds, scope)
    client = gspread.authorize(creds)
    return client.open(sheet_name).get_worksheet(1)
    

# ---- Extract Trends from Current Page ----
def extract_trend_rows(page):
    try:
        page.wait_for_selector("table tbody tr", timeout=15000)
    except PlaywrightTimeoutError:
        print("⚠️ No table rows found on the page.")
        return []

    rows = page.locator("table tbody tr")
    data = []

    count = rows.count()
    for i in range(count):
        row = rows.nth(i)
        if not row.is_visible():
            continue

        cells = row.locator("td")
        if cells.count() < 5:
            continue

        title = cells.nth(1).inner_text().split("\n")[0].strip()
        volume = cells.nth(2).inner_text().split("\n")[0].strip()

        cell3 = cells.nth(3)
        raw = cell3.inner_text().split("\n")
        parts = [l for l in raw if l and l.lower() not in ("trending_up", "timelapse")]
        started = parts[0].strip() if len(parts) > 0 else ""
        ended = parts[1].strip() if len(parts) > 1 else ""

        toggle = cell3.locator("div.vdw3Ld")
        try:
            toggle.click()
            page.wait_for_timeout(300)
            raw2 = cell3.inner_text().split("\n")
            parts2 = [l for l in raw2 if l and l.lower() not in ("trending_up", "timelapse")]
            target_publish = parts2[0].strip() if parts2 else ended
        except:
            target_publish = ended
        finally:
            try:
                toggle.click()
                page.wait_for_timeout(100)
            except:
                pass

        td4 = cells.nth(4)
        span_texts = td4.locator("span.mUIrbf-vQzf8d, span.Gwdjic").all_inner_texts()
        breakdown_items = [t.strip() for t in span_texts if t.strip()]
        breakdown = ", ".join(breakdown_items)

        q = quote(title)
        explore_url = f"https://trends.google.com/trends/explore?q={q}&date=now%201-d&geo=KR&hl=ko"

        data.append([title, volume, started, ended, explore_url, target_publish, breakdown])
    return data

# ---- Scrape All Pages ----
def scrape_pages():
    all_data = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto("https://trends.google.com/trending?geo=KR&category=17", timeout=60000)
        print("✅ Page 1 loaded")
        page.wait_for_timeout(3000)

        while True:
            all_data += extract_trend_rows(page)
            next_btn = page.locator('button[aria-label="Go to next page"]:not([disabled])')
            if next_btn.count() == 0:
                break
            next_btn.click()
            print("⏳ Navigating to next page…")
            page.wait_for_timeout(2000)

        browser.close()
    return all_data

# ---- Helper to Chunk Flat List into Rows ----
def chunk_into_rows(flat_list, n=7):
    return [flat_list[i : i + n] for i in range(0, len(flat_list), n)]

# ---- Main Entrypoint ----
def main():
    SHEET_NAME = "Trends"
    sheet = connect_to_sheet(SHEET_NAME)
    scraped = scrape_pages()

    sheet = connect_to_sheet(JSON_KEYFILE, SHEET_NAME)
    scraped = scrape_pages()
    flat = [item for row in scraped for item in row]
    rows = chunk_into_rows(flat, 7)

    sheet.clear()
    header = [
        "Trending Topic",
        "Search Volume",
        "Started Time",
        "Ended Time",
        "Explore Link",
        "Target Publish Date",
        "Trend Breakdown",
    ]
    sheet.append_rows([header] + rows, value_input_option="RAW")
    print(f"✅ {len(rows)} trends saved to Google Sheet (2nd tab).")

if __name__ == "__main__":
    main()
