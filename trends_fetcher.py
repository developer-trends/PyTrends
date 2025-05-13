#!/usr/bin/env python3
import os
import json
import time
import requests
from urllib.parse import quote
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from openai import OpenAI

# --- CONFIGURATION ---
# Initialize OpenAI client using GitHub secret 'GPT_AI'
client = OpenAI(api_key=os.environ.get("GPT_AI"))

# --- GOOGLE SHEETS SETUP ---
def connect_to_sheet(sheet_name):
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds_dict = json.loads(os.environ["GOOGLE_SA_JSON"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    return gspread.authorize(creds).open(sheet_name).sheet1

# --- PLAYWRIGHT SCRAPERS ---
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


def extract_table_rows(page):
    try:
        page.wait_for_selector("table tbody tr", state="attached", timeout=5000)
    except PlaywrightTimeoutError:
        return []
    rows = page.locator("table tbody tr")
    total = rows.count()
    out = []
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
        parts = [l for l in raw if l and l.lower() not in ("trending_up","timelapse")]
        started = parts[0].strip() if parts else ""
        ended = parts[1].strip() if len(parts)>1 else ""
        explore_url = (
            "https://trends.google.com/trends/explore"
            f"?q={quote(title)}&date=now%201-d&geo=KR&hl=en"
        )
        breakdown = ", ".join(
            s.strip() for s in cells.nth(4)
                .locator("span.mUIrbf-vQzf8d, span.Gwdjic")
                .all_inner_texts() if s.strip()
        )
        out.append([title, volume, started, ended, explore_url, volume, breakdown])
    return out


def extract_card_rows(page):
    try:
        page.wait_for_selector("div.mZ3RIc", timeout=5000)
    except PlaywrightTimeoutError:
        return []
    cards = page.locator("div.mZ3RIc")
    total = cards.count()
    out = []
    for i in range(1, total):
        c = cards.nth(i)
        title = c.locator("span.mUIrbf-vQzf8d").all_inner_texts()[0].strip()
        volume = c.locator("div.search-count-title").inner_text().strip()
        raw = c.locator("div.vdw3Ld").locator("xpath=..").inner_text().split("\n")
        parts = [l for l in raw if l and l.lower() not in ("trending_up","timelapse")]
        started = parts[0].strip() if parts else ""
        ended = parts[1].strip() if len(parts)>1 else ""
        explore_url = (
            "https://trends.google.com/trends/explore"
            f"?q={quote(title)}&date=now%201-d&geo=KR&hl=en"
        )
        breakdown = ", ".join(
            s.strip() for s in c
                .locator("div.lqv0Cb span.mUIrbf-vQzf8d, div.lqv0Cb span.Gwdjic")
                .all_inner_texts() if s.strip()
        )
        out.append([title, volume, started, ended, explore_url, volume, breakdown])
    return out


def scrape_all_pages():
    all_rows = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox","--disable-setuid-sandbox"])
        page = browser.new_page()
        page.goto("https://trends.google.com/trending?geo=KR&category=17&hl=en", timeout=60000)
        page.wait_for_load_state("networkidle")
        dismiss_cookie_banner(page)

        while True:
            batch = extract_table_rows(page) or extract_card_rows(page)
            all_rows.extend(batch)
            next_btn = page.get_by_role("button", name="Go to next page")
            if not next_btn.count() or next_btn.first.is_disabled():
                break
            next_btn.first.scroll_into_view_if_needed()
            next_btn.first.click()
            time.sleep(3)

        browser.close()
    return all_rows

# --- GPT-BASED ENRICHMENT ---
from json import JSONDecodeError

def classify_sport_league(titles, batch_size=20, pause=0.5):
    results = []
    for i in range(0, len(titles), batch_size):
        batch = titles[i : i + batch_size]
        system = (
            "You are a JSON generator. Given an array of esports team names, "
            "reply ONLY with a JSON array of objects { 'team': string, 'sport': string, 'league': string }. "
            "If unsure, use 'Unknown'."
        )
        user = f"Teams: {json.dumps(batch, ensure_ascii=False)}"
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user}
            ],
            temperature=0.0
        )
        text = resp.choices[0].message.content.strip()
        # Strip code fences and extract JSON substring
        if text.startswith("```"):
            # remove fences
            parts = text.split("```")
            text = parts[-1] if len(parts) > 2 else parts[1]
        # Extract first JSON array
        start = text.find('[')
        end = text.rfind(']')
        json_str = text[start:end+1] if start != -1 and end != -1 else text
        try:
            data = json.loads(json_str)
        except JSONDecodeError:
            print("Failed to parse JSON from GPT response:", json_str)
            data = []
        results.extend(data)
        time.sleep(pause)
    return results

# --- MAIN ENTRYPOINT ---
def main():
    sheet = connect_to_sheet("Trends")
    rows = scrape_all_pages()
    titles = [r[0] for r in rows]
    classified = classify_sport_league(titles)
    enriched = [row + [info.get('sport',''), info.get('league','')] for row, info in zip(rows, classified)]
    sheet.clear()
    sheet.append_rows(enriched, value_input_option="RAW")
    print(f"✅ Wrote {len(enriched)} rows (with Sport in H, League in I)")

if __name__ == "__main__":
    main()
