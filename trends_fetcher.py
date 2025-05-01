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

# ─── Scrape ONE page of table rows ─────────────────────────────────────────────
def extract_table_rows(page):
    try:
        page.wait_for_selector("table[role='grid'] tbody tr", timeout=20000)
    except PlaywrightTimeoutError:
        print("⚠️  No table rows found on this page.")
        return []

    rows = page.locator("table[role='grid'] tbody tr")
    count = rows.count()
    print(f"🔢  Found {count} rows on current page")
    out = []

    for i in range(count):
        tr = rows.nth(i)
        if not tr.is_visible():
            continue
        cells = tr.locator("td")
        if cells.count() < 5:
            continue

        title  = cells.nth(1).inner_text().split("\n")[0].strip()
        volume = cells.nth(2).inner_text().split("\n")[0].strip()

        raw    = cells.nth(3).inner_text().split("\n")
        parts  = [l for l in raw if l and l.lower() not in ("trending_up","timelapse")]
        started = parts[0].strip() if parts else ""
        ended   = parts[1].strip() if len(parts)>1 else ""

        spans = cells.nth(4).locator("span.mUIrbf-vQzf8d, span.Gwdjic")
        breakdown = ", ".join([t.strip() for t in spans.all_inner_texts() if t.strip()])

        q = quote(title)
        explore_url = (
            "https://trends.google.com/trends/explore"
            f"?q={q}&date=now%201-d&geo=KR&hl=ko"
        )

        target_publish = ended
        toggle = cells.nth(3).locator("div.vdw3Ld")
        try:
            toggle.click(); time.sleep(0.2)
            flipped = cells.nth(3).inner_text().split("\n")
            p2 = [l for l in flipped if l and l.lower() not in ("trending_up","timelapse")]
            target_publish = p2[0].strip() if p2 else ended
        finally:
            try: toggle.click(); time.sleep(0.1)
            except: pass

        out.append([title, volume, started, ended, explore_url, target_publish, breakdown])

    return out

# ─── Walk through all pages ────────────────────────────────────────────────────
def scrape_pages():
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox","--disable-setuid-sandbox"]
        )
        context = browser.new_context(
            locale="ko-KR",
            viewport={"width":1280,"height":800},
            extra_http_headers={"Accept-Language":"ko-KR,en-US;q=0.9"},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
                " (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()

        # ◀️ HERE’S THE CORRECT URL ▶️
        page.goto("https://trends.google.com/trending?geo=KR&category=17", timeout=60000)
        page.wait_for_load_state("networkidle")
        print("✅  Page loaded")

        while True:
            batch = extract_table_rows(page)
            results += batch

            nxt = page.locator('button[aria-label="Go to next page"]')
            if nxt.count() == 0:
                print("🚫  No next‐page button found")
                break

            disabled = nxt.first.get_attribute("aria-disabled") or "true"
            if disabled.lower() == "true":
                print("✅  Last page reached")
                break

            nxt.first.click()
            print("⏳  Moving to next page…")
            page.wait_for_timeout(2000)

        browser.close()
    return results

# ─── Helper: chunk flat list into rows of 7 columns ─────────────────────────────
def chunk(flat, n=7):
    return [flat[i : i + n] for i in range(0, len(flat), n)]

# ─── Main Entrypoint ───────────────────────────────────────────────────────────
def main():
    sheet   = connect_to_sheet("Trends")
    scraped = scrape_pages()
    flat    = [v for row in scraped for v in row]
    rows    = chunk(flat, 7)

    header = [
      "Trending Topic","Search Volume","Started Time","Ended Time",
      "Explore Link","Target Publish Date","Trend Breakdown"
    ]
    sheet.clear()
    sheet.append_rows([header] + rows, value_input_option="RAW")
    print(f"✅  {len(rows)} total trends saved")

if __name__ == "__main__":
    main()
