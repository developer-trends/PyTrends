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
    try:
        page.wait_for_selector("table[role='grid'] tbody tr", timeout=20000)
    except PlaywrightTimeoutError:
        print("⚠️ Timed out waiting for table rows to attach")
        return []

    rows = page.locator("table[role='grid'] tbody tr")
    out  = []
    print(f"🔢 Found {rows.count()} rows on this page")
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

        # toggle for absolute publish date
        toggle = cells.nth(3).locator("div.vdw3Ld")
        target_publish = ended
        try:
            toggle.click()
            time.sleep(0.2)
            flip = cells.nth(3).inner_text().split("\n")
            p2 = [l for l in flip if l and l.lower() not in ("trending_up","timelapse")]
            target_publish = p2[0].strip() if p2 else ended
        finally:
            try:
                toggle.click()
                time.sleep(0.1)
            except:
                pass

        spans = cells.nth(4).locator("span.mUIrbf-vQzf8d, span.Gwdjic")
        breakdown = ", ".join(t.strip() for t in spans.all_inner_texts() if t.strip())

        q = quote(title)
        explore_url = (
            "https://trends.google.com/trends/explore"
            f"?q={q}&date=now%201-d&geo=KR&hl=ko"
        )

        out.append([
            title,
            volume,
            started,
            ended,
            explore_url,
            target_publish,
            breakdown
        ])
    return out

# ─── Scrape & paginate until end ───────────────────────────────────────────────
def scrape_pages():
    all_data = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox","--disable-setuid-sandbox"]
        )
        # Use original page language—do not force Korean
        context = browser.new_context(
            locale="en-US",
            viewport={"width":1280,"height":800},
            extra_http_headers={"Accept-Language":"en-US,en;q=0.9"},
        )
        page = context.new_page()
        page.goto("https://trends.google.com/trending?geo=KR&category=17", timeout=60000)
        page.wait_for_load_state("networkidle")
        print("✅ Page loaded")

        dismiss_cookie_banner(page)

        # Loop through all pages
        while True:
            batch = extract_table_rows(page)
            all_data.extend(batch)

            # find the next-page button
            nxt = page.locator('button[aria-label="Go to next page"]')
            if nxt.count() == 0:
                print("🚫 No next-page button found — stopping")
                break

            aria_disabled = nxt.first.get_attribute("aria-disabled") or "true"
            if aria_disabled.lower() == "true":
                print("✅ Reached last page — stopping")
                break

            # click and wait for new content
            nxt.first.click()
            print("⏳ Navigating to next page…")
            page.wait_for_timeout(2000)

        browser.close()
    return all_data

# ─── Helper: chunk flat list into rows of 7 columns ─────────────────────────────
def chunk(flat_list, n=7):
    return [flat_list[i:i+n] for i in range(0, len(flat_list), n)]

# ─── Main Entrypoint ───────────────────────────────────────────────────────────
def main():
    sheet   = connect_to_sheet("Trends")
    scraped = scrape_pages()
    flat    = [item for row in scraped for item in row]
    rows    = chunk(flat, 7)

    sheet.clear()
    header = [
        "Trending Topic", "Search Volume", "Started Time", "Ended Time",
        "Explore Link", "Target Publish Date", "Trend Breakdown"
    ]
    sheet.append_rows([header] + rows, value_input_option="RAW")
    print(f"✅ {len(rows)} total trends saved")

if __name__ == "__main__":
    main()
