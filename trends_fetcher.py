#!/usr/bin/env python3
import os, json, time
from urllib.parse import quote

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from playwright.sync_api import sync_playwright

# ─── 1) Google Sheets Setup ────────────────────────────────────────────────────
def connect_to_sheet(sheet_name: str):
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds_dict = json.loads(os.environ["GOOGLE_SA_JSON"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    # second tab is index 1
    return client.open(sheet_name).get_worksheet(1)


# ─── 2) Scrape one page of “cards” ─────────────────────────────────────────────
def extract_card_rows(page):
    # wait for at least one card
    page.wait_for_selector("div.mZ3RIc", timeout=20000)
    cards = page.locator("div.mZ3RIc")
    count = cards.count()
    print(f"🃏 Found {count} cards on this page")
    out = []

    for i in range(count):
        c = cards.nth(i)

        title = c.locator("button .mUIrbf-vQzf8d").inner_text().strip()
        volume = c.locator("div.search-count-title").inner_text().strip()

        # the little “Started/Ended” cell lives next to div.vdw3Ld
        info = c.locator("div.vdw3Ld").locator("xpath=..").inner_text().split("\n")
        parts = [
            line
            for line in info
            if line and line.lower() not in ("trending_up", "timelapse")
        ]
        started = parts[0].strip() if len(parts) > 0 else ""
        ended = parts[1].strip() if len(parts) > 1 else ""

        # toggle button to flip to absolute date → target_publish
        toggle = c.locator("div.vdw3Ld")
        target_publish = ended
        try:
            toggle.click()
            time.sleep(0.2)
            flip = c.locator("div.vdw3Ld").locator("xpath=..").inner_text().split("\n")
            p2 = [
                l
                for l in flip
                if l and l.lower() not in ("trending_up", "timelapse")
            ]
            target_publish = p2[0].strip() if p2 else ended
        finally:
            # flip back
            try:
                toggle.click()
                time.sleep(0.1)
            except:
                pass

        # trend breakdown
        spans = c.locator("div.lqv0Cb span.mUIrbf-vQzf8d, div.lqv0Cb span.Gwdjic")
        breakdown = ", ".join(s.strip() for s in spans.all_inner_texts() if s.strip())

        # explore link
        q = quote(title)
        explore_url = (
            "https://trends.google.com/trends/explore"
            f"?q={q}&date=now%201-d&geo=KR&hl=ko"
        )

        out.append(
            [title, volume, started, ended, explore_url, target_publish, breakdown]
        )

    return out


# ─── 3) Pagination driver ─────────────────────────────────────────────────────
def scrape_all_pages():
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        page = browser.new_page()
        page.goto(
            "https://trends.google.com/trending?geo=KR&category=17", timeout=60000
        )
        page.wait_for_load_state("networkidle")
        print("✅ Page loaded")

        # first page
        batch = extract_card_rows(page)
        results.extend(batch)

        # then loop “Next”
        while True:
            btn = page.locator('button[aria-label="Go to next page"]')
            if btn.count() == 0:
                print("🚫 No next‐page button found → done")
                break
            nxt = btn.first
            # if it’s disabled
            if nxt.get_attribute("disabled") is not None or nxt.get_attribute(
                "aria-disabled"
            ) == "true":
                print("✅ Next button disabled → end reached")
                break
            nxt.click()
            print("⏳ Clicked Next → waiting for cards…")
            # wait for new batch: simple fixed wait
            time.sleep(2)
            batch = extract_card_rows(page)
            results.extend(batch)

        browser.close()

    return results


# ─── 4) Flatten & upload ───────────────────────────────────────────────────────
def main():
    sheet = connect_to_sheet("Trends")
    all_rows = scrape_all_pages()

    # header + rows
    header = [
        "Trending Topic",
        "Search Volume",
        "Started Time",
        "Ended Time",
        "Explore Link",
        "Target Publish Date",
        "Trend Breakdown",
    ]
    # batch‐write
    sheet.clear()
    sheet.append_rows([header] + all_rows, value_input_option="RAW")
    print(f"✅ {len(all_rows)} total trends saved")


if __name__ == "__main__":
    main()
