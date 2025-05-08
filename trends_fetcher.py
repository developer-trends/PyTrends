#!/usr/bin/env python3
import os, json, time
from urllib.parse import quote
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# ✅ Google Sheets connection
def connect_to_sheet(sheet_name):
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds_dict = json.loads(os.environ["GOOGLE_SA_JSON"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client.open(sheet_name).get_worksheet(0)

# ✅ Cookie banner bypass
def dismiss_cookie_banner(page):
    for label in ("Accept all", "I agree", "AGREE"):
        try:
            btn = page.get_by_role("button", name=label)
            if btn.count():
                btn.first.click()
                page.wait_for_timeout(800)
                return
        except:
            pass

# ✅ Sport & league logic
def extract_sport_league_from_text(text):
    text = text.lower()
    sport = league = ""

    if "soccer" in text or "football" in text:
        sport = "Soccer"
        if "premier league" in text: league = "Premier League"
        elif "la liga" in text: league = "La Liga"
        elif "champions league" in text or "ucl" in text: league = "Champions League"
        elif "k league" in text: league = "K League"

    elif "basketball" in text:
        sport = "Basketball"
        if "nba" in text: league = "NBA"

    elif "baseball" in text:
        sport = "Baseball"
        if "mlb" in text: league = "MLB"
        elif "kbo" in text: league = "KBO"

    elif "ufc" in text or "mma" in text or "one fc" in text or "bellator" in text:
        sport = "MMA"
        if "ufc" in text: league = "UFC"
        elif "one fc" in text: league = "ONE FC"
        elif "bellator" in text: league = "Bellator"

    elif "volleyball" in text:
        sport = "Volleyball"
        if "v-league" in text: league = "V-League"

    elif "esports" in text or "lck" in text or "valorant" in text or "league of legends" in text:
        sport = "E-Sports"
        if "lck" in text: league = "LCK"
        elif "valorant" in text: league = "Valorant"
        elif "league of legends" in text: league = "League of Legends"

    return sport.title(), league

# ✅ Access and scrape the explore page for sport/league
def extract_sport_league(browser, title):
    try:
        q = quote(title)
        url = f"https://trends.google.com/trends/explore?q={q}&date=now%201-d&geo=KR&hl=en"
        context = browser.new_context()
        page = context.new_page()
        page.goto(url, timeout=20000)
        page.wait_for_load_state("networkidle")
        time.sleep(1.5)

        body = page.content().lower()
        sport, league = extract_sport_league_from_text(body)

        page.close()
        context.close()
        return sport, league, url
    except:
        return "", "", ""

# ✅ Extract rows from main table
def extract_table_rows(page, browser):
    try:
        page.wait_for_selector("table tbody tr", timeout=5000)
    except PlaywrightTimeoutError:
        return []

    rows = page.locator("table tbody tr")
    total = rows.count()
    print(f"🔢 Found {total} rows")

    out = []
    for i in range(1, total):  # skip first row
        row = rows.nth(i)
        if not row.is_visible(): continue

        cells = row.locator("td")
        if cells.count() < 5: continue

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

        sport, league, explore_url = extract_sport_league(browser, title)

        out.append([
            title, volume, started, ended,
            explore_url, target_publish, breakdown,
            sport, league
        ])
    return out

# ✅ Fallback for card layout
def extract_card_rows(page, browser):
    try:
        page.wait_for_selector("div.mZ3RIc", timeout=5000)
    except PlaywrightTimeoutError:
        return []

    cards = page.locator("div.mZ3RIc")
    total = cards.count()
    print(f"🃏 Found {total} cards")

    out = []
    for i in range(1, total):  # skip first card
        c = cards.nth(i)
        title = c.locator("span.mUIrbf-vQzf8d").all_inner_texts()[0].strip()
        volume = c.locator("div.search-count-title").inner_text().strip()

        raw = c.locator("div.vdw3Ld").locator("xpath=..").inner_text().split("\n")
        parts = [l for l in raw if l and l.lower() not in ("trending_up", "timelapse")]
        started = parts[0].strip() if parts else ""
        ended = parts[1].strip() if len(parts) > 1 else ""

        toggle = c.locator("div.vdw3Ld")
        target_publish = ended
        try:
            toggle.click(); time.sleep(0.2)
            raw2 = c.locator("div.vdw3Ld").locator("xpath=..").inner_text().split("\n")
            p2 = [l for l in raw2 if l and l.lower() not in ("trending_up", "timelapse")]
            if p2:
                target_publish = p2[0].strip()
        finally:
            try: toggle.click(); time.sleep(0.1)
            except: pass

        spans = c.locator("div.lqv0Cb span.mUIrbf-vQzf8d, div.lqv0Cb span.Gwdjic")
        breakdown = ", ".join(s.strip() for s in spans.all_inner_texts() if s.strip())

        sport, league, explore_url = extract_sport_league(browser, title)

        out.append([
            title, volume, started, ended,
            explore_url, target_publish, breakdown,
            sport, league
        ])
    return out

# ✅ Orchestrate entire scraping
def scrape_all_pages():
    all_rows = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://trends.google.com/trending?geo=KR&category=17&hl=en", timeout=60000)
        page.wait_for_load_state("networkidle")
        dismiss_cookie_banner(page)

        while True:
            batch = extract_table_rows(page, browser)
            if not batch:
                print("⚙️ Fallback to cards")
                batch = extract_card_rows(page, browser)

            all_rows.extend(batch)
            next_btn = page.get_by_role("button", name="Go to next page")
            if not next_btn.count() or next_btn.first.is_disabled():
                break
            next_btn.first.click()
            time.sleep(3)

        browser.close()
    return all_rows

# ✅ Main runner
def main():
    sheet = connect_to_sheet("Trends")
    rows = scrape_all_pages()
    sheet.clear()
    sheet.append_rows(rows, value_input_option="RAW")
    print(f"✅ {len(rows)} saved to Google Sheet")

if __name__ == "__main__":
    main()
