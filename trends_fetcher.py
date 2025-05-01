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

# ─── Table layout extractor ──────────────────────────────────────────────────
def extract_table_rows(page):
    try:
        page.wait_for_selector("table tbody tr", timeout=20000)
    except PlaywrightTimeoutError:
        print("⚠️ No table rows found.")
        return []

    rows = page.locator("table tbody tr")
    print(f"🔢 Found {rows.count()} table rows")
    out = []
    for i in range(rows.count()):
        row = rows.nth(i)
        if not row.is_visible():
            continue
        cells = row.locator("td")
        if cells.count() < 5:
            continue

        title  = cells.nth(1).inner_text().split("\n")[0].strip()
        volume = cells.nth(2).inner_text().split("\n")[0].strip()

        raw = cells.nth(3).inner_text().split("\n")
        parts = [l for l in raw if l and l.lower() not in ("trending_up","timelapse")]
        started = parts[0].strip() if parts else ""
        ended   = parts[1].strip() if len(parts)>1 else ""

        # flip to absolute date
        toggle = cells.nth(3).locator("div.vdw3Ld")
        target_publish = ended
        try:
            toggle.click(); time.sleep(0.25)
            raw2 = cells.nth(3).inner_text().split("\n")
            p2   = [l for l in raw2 if l and l.lower() not in ("trending_up","timelapse")]
            if p2:
                target_publish = p2[0].strip()
        finally:
            try:
                toggle.click(); time.sleep(0.25)
            except:
                pass

        spans = cells.nth(4).locator("span.mUIrbf-vQzf8d, span.Gwdjic")
        breakdown = ", ".join(s.strip() for s in spans.all_inner_texts() if s.strip())

        q = quote(title)
        explore_url = (
            "https://trends.google.com/trends/explore"
            f"?q={q}&date=now%201-d&geo=KR&hl=ko"
        )

        out.append([title, volume, started, ended, explore_url, target_publish, breakdown])
    return out

# ─── Card layout extractor (fallback) ─────────────────────────────────────────
def extract_card_rows(page):
    try:
        page.wait_for_selector("div.mZ3RIc", timeout=20000)
    except PlaywrightTimeoutError:
        print("⚠️ No card elements found.")
        return []

    cards = page.locator("div.mZ3RIc")
    print(f"🃏 Found {cards.count()} cards")
    out = []
    for i in range(cards.count()):
        c = cards.nth(i)
        spans = c.locator("span.mUIrbf-vQzf8d").all_inner_texts()
        title  = spans[0].strip() if spans else ""
        volume = c.locator("div.search-count-title").inner_text().strip()

        info = c.locator("div.vdw3Ld").locator("xpath=..").inner_text().split("\n")
        parts = [l for l in info if l and l.lower() not in ("trending_up","timelapse")]
        started = parts[0].strip() if parts else ""
        ended   = parts[1].strip() if len(parts)>1 else ""

        toggle = c.locator("div.vdw3Ld")
        target_publish = ended
        try:
            toggle.click(); time.sleep(0.25)
            info2 = c.locator("div.vdw3Ld").locator("xpath=..").inner_text().split("\n")
            p2    = [l for l in info2 if l and l.lower() not in ("trending_up","timelapse")]
            if p2:
                target_publish = p2[0].strip()
        finally:
            try:
                toggle.click(); time.sleep(0.25)
            except:
                pass

        br = c.locator("div.lqv0Cb span.mUIrbf-vQzf8d, div.lqv0Cb span.Gwdjic")
        breakdown = ", ".join(t.strip() for t in br.all_inner_texts() if t.strip())

        q = quote(title)
        explore_url = (
            "https://trends.google.com/trends/explore"
            f"?q={q}&date=now%201-d&geo=KR&hl=ko"
        )

        out.append([title, volume, started, ended, explore_url, target_publish, breakdown])
    return out

# ─── Pagination + full scrape ─────────────────────────────────────────────────
def scrape_all_pages():
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox","--disable-setuid-sandbox"]
        )
        page = browser.new_page()
        page.goto("https://trends.google.com/trending?geo=KR&category=17", timeout=60000)
        page.wait_for_load_state("networkidle")
        print("✅ First page loaded")

        dismiss_cookie_banner(page)

        # pick the correct extractor once
        use_table = page.locator("table tbody tr").count() > 0
        extractor = extract_table_rows if use_table else extract_card_rows
        print(f"🔍 Using {'table' if use_table else 'card'} extractor")

        # scrape page #1
        results.extend(extractor(page))

        # then loop ▶ until disabled
        while True:
            btn = page.locator('button[aria-label="Go to next page"]')
            if btn.count() == 0:
                print("🚫 No next-page button. Done.")
                break
            nxt = btn.first
            # scroll it in view, then check disabled
            nxt.scroll_into_view_if_needed()
            disabled = nxt.get_attribute("disabled") is not None or nxt.get_attribute("aria-disabled")=="true"
            if disabled:
                print("✅ Next-page is disabled → last page reached")
                break

            nxt.click()
            print("⏳ Clicked ▶, waiting for new rows…")
            # give time for the new rows to attach
            time.sleep(2)
            results.extend(extractor(page))

        browser.close()

    return results

# ─── Kick-off & sheet upload ──────────────────────────────────────────────────
def main():
    sheet = connect_to_sheet("Trends")
    rows  = scrape_all_pages()
    header = [
        "Trending Topic","Search Volume","Started Time","Ended Time",
        "Explore Link","Target Publish Date","Trend Breakdown",
    ]
    sheet.clear()
    # one batch append to avoid rate limits
    sheet.append_rows([header] + rows, value_input_option="RAW")
    print(f"✅ {len(rows)} total trends saved to Google Sheets (2nd tab)")

if __name__=="__main__":
    main()
