from playwright.sync_api import sync_playwright
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import time
from urllib.parse import quote

# ---- Google Sheet Setup ----
def connect_to_sheet(json_keyfile_path, sheet_name):
    scope = [
        'https://spreadsheets.google.com/feeds',
        'https://www.googleapis.com/auth/drive',
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_name(json_keyfile_path, scope)
    client = gspread.authorize(creds)
    # write into the 2nd tab (index 1)
    return client.open(sheet_name).get_worksheet(1)

# ---- Extract Trends from Current Page ----
# ---- Extract Trends from Current Page ----
def extract_trend_rows(page):
    # wait until at least one <tr> is in the DOM
    page.wait_for_selector("table tbody tr", state="attached", timeout=15000)
    rows = page.locator("table tbody tr")

    data = []
    for i in range(rows.count()):
        row = rows.nth(i)
        if not row.is_visible():
            continue

        cells = row.locator("td")
        if cells.count() < 5:
            continue

        # A: title, B: volume
        title  = cells.nth(1).inner_text().split("\n")[0].strip()
        volume = cells.nth(2).inner_text().split("\n")[0].strip()

        # C/D from the 3rd cell
        cell3 = cells.nth(3)
        raw   = cell3.inner_text().split("\n")
        parts = [l for l in raw if l and l.lower() not in ("trending_up","timelapse")]
        started = parts[0].strip() if len(parts) > 0 else ""
        ended   = parts[1].strip() if len(parts) > 1 else ""

        # Locate the mini‐graph div inside that cell (the toggle handle)
        toggle = cell3.locator("div.vdw3Ld")

        # F: Target Publish Date → flip to absolute, read, then flip back
        try:
            toggle.click()                # show absolute date
            time.sleep(0.2)
            raw2   = cell3.inner_text().split("\n")
            parts2 = [l for l in raw2 if l and l.lower() not in ("trending_up","timelapse")]
            target_publish = parts2[0].strip() if parts2 else ended
        finally:
            toggle.click()                # revert to relative
            time.sleep(0.1)

        # G: breakdown
        td4 = cells.nth(4)
        span_texts = td4.locator("span.mUIrbf-vQzf8d, span.Gwdjic").all_inner_texts()
        breakdown_items = [t.strip() for t in span_texts if t.strip()]
        breakdown = ", ".join(breakdown_items)

        # E: explore link
        q = quote(title)
        explore_url = (
            f"https://trends.google.com/trends/explore"
            f"?q={q}&date=now%201-d&geo=KR&hl=ko"
        )

        data.append([title, volume, started, ended, explore_url, target_publish, breakdown])

    return data


# ---- Scrape All Pages ----
def scrape_pages():
    all_data = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto(
            "https://trends.google.com/trending?geo=KR&category=17",
            timeout=60000
        )
        print("✅ Page 1 loaded")
        page.wait_for_timeout(3000)

        while True:
            all_data += extract_trend_rows(page)
            next_btn = page.locator('button[aria-label="Go to next page"]:not([disabled])')
            if next_btn.count() == 0:
                break
            next_btn.click()
            print("⏳ Navigating to next page…")
            time.sleep(2)

        browser.close()
    return all_data

# ---- Helper to Chunk Flat List into Rows ----
def chunk_into_rows(flat_list, n=7):
    return [flat_list[i : i + n] for i in range(0, len(flat_list), n)]

def main():
    SHEET_NAME   = "Trends"
    JSON_KEYFILE = "trends-458208-4d1f98834c57.json"

    sheet   = connect_to_sheet(JSON_KEYFILE, SHEET_NAME)
    scraped = scrape_pages()  # each element: [A–G]

    # flatten & group into rows of 7 columns
    flat = [item for row in scraped for item in row]
    rows = chunk_into_rows(flat, 7)

    # clear + batch‐write header + all rows
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
