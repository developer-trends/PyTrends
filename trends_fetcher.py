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
                print("🛡️ Dismissed cookie banner")
                return
        except:
            pass

def extract_sport_league(browser, title):
    with browser.new_context() as context:
        page = context.new_page()
        try:
            q = quote(title)
            explore_url = (
                "https://trends.google.com/trends/explore"
                f"?q={q}&date=now%201-d&geo=KR&hl=en"
            )
            page.goto(explore_url, timeout=15000)
            page.wait_for_load_state("networkidle")
            time.sleep(2)

            possible_text = page.locator("text=/sport|league/i").all_inner_texts()
            joined = " ".join(possible_text).lower()

            sport = ""
            league = ""

            if "basketball" in joined:
                sport = "Basketball"
                league = "NBA" if "nba" in joined else ""
            elif "soccer" in joined or "football" in joined:
                sport = "Soccer"
                if "premier league" in joined: league = "Premier League"
                elif "la liga" in joined: league = "La Liga"
                elif "ucl" in joined or "champions league" in joined: league = "Champions League"
            elif "mma" in joined or "ufc" in joined:
                sport = "MMA"
                league = "UFC"
            elif "baseball" in joined:
                sport = "Baseball"
                league = "MLB"
            elif "volleyball" in joined:
                sport = "Volleyball"
            elif "esports" in joined:
                sport = "E-Sports"
            elif "boxing" in joined:
                sport = "Boxing"

            return sport, league

        except:
            return "", ""

def extract_table_rows(page, browser):
    try:
        page.wait_for_selector("table tbody tr", state="attached", timeout=5000)
    except PlaywrightTimeoutError:
        return []

    rows = page.locator("table tbody tr")
    total = rows.count()
    print(f"🔢 [table] found {total} rows – skipping the first one")

    out = []
    for i in range(1, total):
        row = rows.nth(i)
        if not row.is_visible(): continue

        cells = row.locator("td")
        if cells.count() < 5: continue

        title  = cells.nth(1).inner_text().split("\n")[0].strip()
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
        explore_url = (
            "https://trends.google.com/trends/explore"
            f"?q={q}&date=now%201-d&geo=KR&hl=en"
        )

        sport, league = extract_sport_league(browser, title)

        # A B C D E F G H I J (col indexes)
        out.append([
            title, volume, started, ended, explore_url, target_publish, breakdown,
            "", sport, league
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
            batch = extract_table_rows(page, browser)
            if not batch:
                print("⚙️ fallback")
            print(f"→ got {len(batch)} rows")
            all_rows.extend(batch)

            next_btn = page.get_by_role("button", name="Go to next page")
            if not next_btn.count() or next_btn.first.is_disabled():
                print("✅ No more pages")
                break
            next_btn.first.click()
            print("⏳ Waiting 3s")
            time.sleep(3)
            page_num += 1

        browser.close()
    return all_rows

def main():
    sheet = connect_to_sheet("Trends")
    rows = scrape_all_pages()
    sheet.clear()
    sheet.append_rows(rows, value_input_option="RAW")
    print(f"✅ {len(rows)} total trends saved to Google Sheet (cols A–J)")

if __name__ == "__main__":
    main()
