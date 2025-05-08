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
        except:
            pass

def detect_sport_league_from_page(context, url):
    sport, league = "", ""
    page = context.new_page()
    try:
        page.goto(url, timeout=15000)
        page.wait_for_load_state("domcontentloaded")
        breadcrumb = page.locator("div[role='navigation']").inner_text(timeout=5000)
        lines = breadcrumb.split("\n")
        if len(lines) >= 2:
            sport = lines[-2].strip()
            league = lines[-1].strip()
    except Exception as e:
        print("⚠️ Could not extract sport/league for:", url)
    finally:
        page.close()
    return sport, league

def extract_table_rows(page, browser):
    try:
        page.wait_for_selector("table tbody tr", timeout=5000)
    except PlaywrightTimeoutError:
        return []

    rows = page.locator("table tbody tr")
    total = rows.count()
    out = []

    for i in range(1, total):
        row = rows.nth(i)
        if not row.is_visible(): continue
        cells = row.locator("td")
        if cells.count() < 5: continue

        title  = cells.nth(1).inner_text().split("\n")[0].strip()
        volume = cells.nth(2).inner_text().split("\n")[0].strip()

        raw = cells.nth(3).inner_text().split("\n")
        parts = [l for l in raw if l and l.lower() not in ("trending_up","timelapse")]
        started = parts[0].strip() if parts else ""
        ended = parts[1].strip() if len(parts)>1 else ""

        toggle = cells.nth(3).locator("div.vdw3Ld")
        target_publish = ended
        try:
            toggle.click(); time.sleep(0.2)
            raw2 = cells.nth(3).inner_text().split("\n")
            p2 = [l for l in raw2 if l and l.lower() not in ("trending_up","timelapse")]
            if p2:
                target_publish = p2[0].strip()
        finally:
            try: toggle.click(); time.sleep(0.1)
            except: pass

        spans = cells.nth(4).locator("span.mUIrbf-vQzf8d, span.Gwdjic")
        breakdown = ", ".join(s.strip() for s in spans.all_inner_texts() if s.strip())

        q = quote(title)
        explore_url = f"https://trends.google.com/trends/explore?q={q}&date=now%201-d&geo=KR&hl=en"

        # 🆕 Create isolated browser context to fetch sport & league
        context = browser.new_context()
        sport, league = detect_sport_league_from_page(context, explore_url)
        context.close()

        out.append([title, volume, started, ended, explore_url, target_publish, breakdown, sport, league])

    return out

def extract_card_rows(page, browser):
    try:
        page.wait_for_selector("div.mZ3RIc", timeout=5000)
    except PlaywrightTimeoutError:
        return []

    cards = page.locator("div.mZ3RIc")
    total = cards.count()
    out = []

    for i in range(1, total):
        c = cards.nth(i)
        try:
            title = c.locator("span.mUIrbf-vQzf8d").all_inner_texts()[0].strip()
        except IndexError:
            continue

        volume = c.locator("div.search-count-title").inner_text().strip()

        raw = c.locator("div.vdw3Ld").locator("xpath=..").inner_text().split("\n")
        parts = [l for l in raw if l and l.lower() not in ("trending_up","timelapse")]
        started = parts[0].strip() if parts else ""
        ended = parts[1].strip() if len(parts)>1 else ""

        toggle = c.locator("div.vdw3Ld")
        target_publish = ended
        try:
            toggle.click(); time.sleep(0.2)
            raw2 = c.locator("div.vdw3Ld").locator("xpath=..").inner_text().split("\n")
            p2 = [l for l in raw2 if l and l.lower() not in ("trending_up","timelapse")]
            if p2:
                target_publish = p2[0].strip()
        finally:
            try: toggle.click(); time.sleep(0.1)
            except: pass

        spans = c.locator("div.lqv0Cb span.mUIrbf-vQzf8d, div.lqv0Cb span.Gwdjic")
        breakdown = ", ".join(s.strip() for s in spans.all_inner_texts() if s.strip())

        q = quote(title)
        explore_url = f"https://trends.google.com/trends/explore?q={q}&date=now%201-d&geo=KR&hl=en"

        context = browser.new_context()
        sport, league = detect_sport_league_from_page(context, explore_url)
        context.close()

        out.append([title, volume, started, ended, explore_url, target_publish, breakdown, sport, league])

    return out

def scrape_all_pages():
    all_rows = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox","--disable-setuid-sandbox"])
        page = browser.new_page()
        page.goto("https://trends.google.com/trending?geo=KR&category=17&hl=en", timeout=60000)
        page.wait_for_load_state("networkidle")
        print("✅ First page loaded")

        dismiss_cookie_banner(page)

        page_num = 1
        while True:
            print(f"📄 Scraping page {page_num}")
            batch = extract_table_rows(page, browser)
            if not batch:
                print("⚙️  table extractor returned 0 → falling back to cards")
                batch = extract_card_rows(page, browser)

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
    print(f"✅ {len(rows)} total trends saved to Google Sheet (1st tab)")

if __name__ == "__main__":
    main()
