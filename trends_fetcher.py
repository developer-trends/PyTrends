#!/usr/bin/env python3
import os
import json
import time
import requests
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
                print("Cookie banner dismissed")
                return
        except Exception:
            pass


def extract_table_rows(page):
    try:
        page.wait_for_selector("table tbody tr", state="attached", timeout=5000)
    except PlaywrightTimeoutError:
        return []

    rows = page.locator("table tbody tr")
    total = rows.count()
    print(f"[Table] Found {total} rows")

    extracted = []
    for i in range(1, total):
        row = rows.nth(i)
        if not row.is_visible():
            continue

        cells = row.locator("td")
        if cells.count() < 5:
            continue

        title = cells.nth(1).inner_text().split("\n")[0].strip()
        volume = cells.nth(2).inner_text().split("\n")[0].strip()

        raw = cells.nth(3).inner_text().split("\n")
        parts = [line for line in raw if line and line.lower() not in ("trending_up", "timelapse")]
        started = parts[0].strip() if parts else ""
        ended = parts[1].strip() if len(parts) > 1 else ""

        toggle = cells.nth(3).locator("div.vdw3Ld")
        target_publish = ended
        try:
            toggle.click()
            time.sleep(0.2)
            raw2 = cells.nth(3).inner_text().split("\n")
            p2 = [line for line in raw2 if line and line.lower() not in ("trending_up", "timelapse")]
            if p2:
                target_publish = p2[0].strip()
        finally:
            try:
                toggle.click()
                time.sleep(0.1)
            except Exception:
                pass

        spans = cells.nth(4).locator("span.mUIrbf-vQzf8d, span.Gwdjic")
        breakdown = ", ".join(span.strip() for span in spans.all_inner_texts() if span.strip())

        query = quote(title)
        explore_url = f"https://trends.google.com/trends/explore?q={query}&date=now%201-d&geo=KR&hl=en"

        extracted.append([title, volume, started, ended, explore_url, target_publish, breakdown])

    return extracted


def extract_card_rows(page):
    try:
        page.wait_for_selector("div.mZ3RIc", timeout=5000)
    except PlaywrightTimeoutError:
        return []

    cards = page.locator("div.mZ3RIc")
    total = cards.count()
    print(f"[Card] Found {total} cards")

    extracted = []
    for i in range(1, total):
        card = cards.nth(i)
        title = card.locator("span.mUIrbf-vQzf8d").all_inner_texts()[0].strip()
        volume = card.locator("div.search-count-title").inner_text().strip()

        raw = card.locator("div.vdw3Ld").locator("xpath=..").inner_text().split("\n")
        parts = [line for line in raw if line and line.lower() not in ("trending_up", "timelapse")]
        started = parts[0].strip() if parts else ""
        ended = parts[1].strip() if len(parts) > 1 else ""

        toggle = card.locator("div.vdw3Ld")
        target_publish = ended
        try:
            toggle.click()
            time.sleep(0.2)
            raw2 = card.locator("div.vdw3Ld").locator("xpath=..").inner_text().split("\n")
            p2 = [line for line in raw2 if line and line.lower() not in ("trending_up", "timelapse")]
            if p2:
                target_publish = p2[0].strip()
        finally:
            try:
                toggle.click()
                time.sleep(0.1)
            except Exception:
                pass

        spans = card.locator("div.lqv0Cb span.mUIrbf-vQzf8d, div.lqv0Cb span.Gwdjic")
        breakdown = ", ".join(span.strip() for span in spans.all_inner_texts() if span.strip())

        query = quote(title)
        explore_url = f"https://trends.google.com/trends/explore?q={query}&date=now%201-d&geo=KR&hl=en"

        extracted.append([title, volume, started, ended, explore_url, target_publish, breakdown])

    return extracted


def scrape_all_pages():
    all_rows = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        page = browser.new_page()
        page.goto("https://trends.google.com/trending?geo=KR&category=17&hl=en", timeout=60000)
        page.wait_for_load_state("networkidle")
        print("Initial page loaded")

        dismiss_cookie_banner(page)

        page_number = 1
        while True:
            print(f"Scraping page {page_number}")
            batch = extract_table_rows(page)
            if not batch:
                print("No table rows found, using card layout instead")
                batch = extract_card_rows(page)

            print(f"Collected {len(batch)} rows")
            all_rows.extend(batch)

            next_btn = page.get_by_role("button", name="Go to next page")
            if not next_btn.count() or next_btn.first.is_disabled():
                print("No more pages available")
                break

            next_btn.first.scroll_into_view_if_needed()
            next_btn.first.click()
            time.sleep(3)
            page_number += 1

        browser.close()

    return all_rows


def trigger_google_apps_script():
    url = "https://script.google.com/macros/s/AKfycbwkwMbeRjPElzoR6pJeZowa3xCmUpN17qUEX_pulrBTIankAAYe8ZDJFd5VolgU79ZinA/exec"
    try:
        response = requests.post(url, timeout=180)
        if response.status_code == 200:
            print("Trigger successfully.")
        else:
            print(f"Trigger failed: {response.status_code} — {response.text}")
    except Exception as e:
        print(f"Error triggering Apps Script: {e}")


def main():
    sheet = connect_to_sheet("Trends")
    rows = scrape_all_pages()

    sheet.clear()
    sheet.append_rows(rows, value_input_option="RAW")
    print(f"{len(rows)} total trends saved to Google Sheet")

    trigger_google_apps_script()


if __name__ == "__main__":
    main()
