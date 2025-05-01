#!/usr/bin/env python3
import os, json, time
from urllib.parse import quote

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from playwright.sync_api import sync_playwright

# ─── Google Sheets Setup (2nd tab) ────────────────────────────────────────────
def connect_to_sheet(sheet_name: str):
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds_dict = json.loads(os.environ["GOOGLE_SA_JSON"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client.open(sheet_name).get_worksheet(1)


# ─── Scrape one “card” page ───────────────────────────────────────────────────
def extract_card_rows(page):
    page.wait_for_selector("div.mZ3RIc", timeout=20000)
    cards = page.locator("div.mZ3RIc")
    n = cards.count()
    print(f"🃏 Found {n} cards on this page")
    out = []

    for i in range(n):
        c = cards.nth(i)

        # title: just grab the first visible span.mUIrbf-vQzf8d
        title = c.locator("span.mUIrbf-vQzf8d").first.inner_text().strip()
        volume = c.locator("div.search-count-title").inner_text().strip()

        # started/ended live under the sibling of that little chart div
        info = c.locator("div.vdw3Ld").locator("xpath=..").inner_text().split("\n")
        parts = [l for l in info if l and l.lower() not in ("trending_up", "timelapse")]
        started = parts[0].strip() if len(parts) > 0 else ""
        ended   = parts[1].strip() if len(parts) > 1 else ""

        # flip to absolute → target publish
        toggle = c.locator("div.vdw3Ld")
        target_publish = ended
        try:
            toggle.click()
            time.sleep(0.25)   # give it a moment
            flip = c.locator("div.vdw3Ld").locator("xpath=..").inner_text().split("\n")
            p2   = [l for l in flip if l and l.lower() not in ("trending_up", "timelapse")]
            if p2:
                target_publish = p2[0].strip()
        finally:
            try:
                toggle.click()
                time.sleep(0.25)
            except:
                pass

        # breakdown
        spans = c.locator("div.lqv0Cb span.mUIrbf-vQzf8d, div.lqv0Cb span.Gwdjic")
        breakdown = ", ".join(s.strip() for s in spans.all_inner_texts() if s.strip())

        # explore URL
        q = quote(title)
        explore_url = (
            "https://trends.google.com/trends/explore"
            f"?q={q}&date=now%201-d&geo=KR&hl=ko"
        )

        out.append([title, volume, started, ended, explore_url, target_publish, breakdown])

    return out


# ─── Drive the “next page” loop ───────────────────────────────────────────────
def scrape_all_pages():
    all_rows = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        page = browser.new_page()
        page.goto(
            "https://trends.google.com/trending?geo=KR&category=17",
            timeout=60000
        )
        page.wait_for_load_state("networkidle")
        print("✅ First page loaded")

        # first batch
        all_rows.extend(extract_card_rows(page))

        # then paginate
        while True:
            btn = page.locator('button[aria-label="Go to next page"]')
            if btn.count() == 0:
                print("🚫 No Next button → done")
                break
            nxt = btn.first
            # stop if disabled either way
            if nxt.get_attribute("disabled") is not None or \
               nxt.get_attribute("aria-disabled") == "true":
                print("✅ Next is disabled → end reached")
                break

            nxt.click()
            print("⏳ Clicked ▶ → waiting for new cards…")
            time.sleep(2)
            all_rows.extend(extract_card_rows(page))

        browser.close()

    return all_rows


# ─── Main & upload ────────────────────────────────────────────────────────────
def main():
    sheet = connect_to_sheet("Trends")
    rows  = scrape_all_pages()

    header = [
        "Trending Topic","Search Volume","Started Time","Ended Time",
        "Explore Link","Target Publish Date","Trend Breakdown",
    ]
    sheet.clear()
    # one batch upload
    sheet.append_rows([header] + rows, value_input_option="RAW")
    print(f"✅ {len(rows)} total trends saved to Google Sheets (2nd tab)")

if __name__ == "__main__":
    main()
