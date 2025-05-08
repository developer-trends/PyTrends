#!/usr/bin/env python3
import os, json, time
from urllib.parse import quote
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

def connect_to_sheet(sheet_name):
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds_dict = json.loads(os.environ["GOOGLE_SA_JSON"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client.open(sheet_name).get_worksheet(0)

def dismiss_cookie_banner(page):
    for label in ("Accept all", "I agree", "AGREE"):
        try:
            btn = page.get_by_role("button", name=label)
            if btn.count():
                btn.first.click()
                page.wait_for_timeout(800)
                return
        except: pass

def extract_sport_league(context, explore_url):
    try:
        new_tab = context.new_page()
        new_tab.goto(explore_url, timeout=20000)
        new_tab.wait_for_load_state("networkidle")
        new_tab.wait_for_timeout(1500)

        body_text = new_tab.content()

        sport, league = '', ''

        if "soccer" in body_text.lower() or "football" in body_text.lower():
            sport = "Soccer"
        elif "basketball" in body_text.lower():
            sport = "Basketball"
        elif "baseball" in body_text.lower():
            sport = "Baseball"
        elif "mma" in body_text.lower():
            sport = "MMA"
        elif "boxing" in body_text.lower():
            sport = "Boxing"
        elif "volleyball" in body_text.lower():
            sport = "Volleyball"

        for kw in ["Premier League", "La Liga", "Bundesliga", "NBA", "MLB", "UFC", "ONE FC", "Bellator", "KBO", "LCK", "Champions League"]:
            if kw.lower() in body_text.lower():
                league = kw
                break

        new_tab.close()
        return sport, league
    except Exception as e:
        return "", ""

def extract_table_rows(page, context):
    try:
        page.wait_for_selector("table tbody tr", timeout=5000)
    except PlaywrightTimeoutError:
        return []
    rows = page.locator("table tbody tr")
    total = rows.count()
    print(f"🔢 Found {total} table rows")

    out = []
    for i in range(1, total):  # skip header row
        row = rows.nth(i)
        if not row.is_visible():
            continue
        cells = row.locator("td")
        if cells.count() < 5:
            continue

        title = cells.nth(1).inner_text().split("\n")[0].strip()
        volume = cells.nth(2).inner_text().split("\n")[0].strip()

        raw = cells.nth(3).inner_text().split("\n")
        parts = [l for l in raw if l and l.lower() not in ("trending_up", "timelapse")]
        started = parts[0].strip() if parts else ""
        ended = parts[1].strip() if len(parts) > 1 else ""

        toggle = cells.nth(3).locator("div.vdw3Ld")
        target_publish = ended
        try:
            toggle.click(); time.sleep(0.2)
            raw2 = cells.nth(3).inner_text().split("\n")
            p2 = [l for l in raw2 if l and l.lower() not in ("trending_up", "timelapse")]
            if p2:
                target_publish = p2[0].strip()
        finally:
            try: toggle.click(); time.sleep(0.1)
            except: pass

        spans = cells.nth(4).locator("span.mUIrbf-vQzf8d, span.Gwdjic")
        breakdown = ", ".join(s.strip() for s in spans.all_inner_texts() if s.strip())

        q = quote(title)
        explore_url = f"https://trends.google.com/trends/explore?q={q}&date=now%201-d&geo=KR&hl=en"

        sport, league = extract_sport_league(context, explore_url)

        out.append([
            title,          # A
            volume,         # B
            started,        # C
            ended,          # D
            explore_url,    # E
            target_publish, # F
            breakdown,      # G
            sport,          # H (now I)
            league          # I (now J)
        ])

    return out

def scrape_all_pages():
    all_rows = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://trends.google.com/trending?geo=KR&category=17&hl=en", timeout=60000)
        page.wait_for_load_state("networkidle")
        print("✅ First page loaded")

        dismiss_cookie_banner(page)

        page_num = 1
        while True:
            print(f"📄 Scraping page {page_num}")
            batch = extract_table_rows(page, context)
            if not batch:
                print("⚠️ No rows found on this page.")
                break

            print(f"  → {len(batch)} rows scraped.")
            all_rows.extend(batch)

            next_btn = page.get_by_role("button", name="Go to next page")
            if not next_btn.count() or next_btn.first.is_disabled():
                break

            next_btn.first.scroll_into_view_if_needed()
            next_btn.first.click()
            time.sleep(3)
            page_num += 1

        browser.close()
    return all_rows

def main():
    sheet = connect_to_sheet("Trends")
    rows = scrape_all_pages()
    sheet.clear()
    sheet.append_rows(rows, value_input_option="RAW")
    print(f"✅ {len(rows)} total trends saved to Google Sheet.")

if __name__ == "__main__":
    main()
