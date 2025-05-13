#!/usr/bin/env python3
import os
import json
from json import JSONDecodeError
import time
import re
from urllib.parse import quote
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from openai import OpenAI
from deep_translator import GoogleTranslator

# --- CONFIGURATION ---
client = OpenAI(api_key=os.environ.get("GPT_AI"))

# --- GOOGLE SHEETS SETUP ---
def connect_to_sheet(sheet_name):
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds_dict = json.loads(os.environ["GOOGLE_SA_JSON"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client_sheet = gspread.authorize(creds)
    return client_sheet.open(sheet_name).get_worksheet(0)

# --- SCRAPING LOGIC ---
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

def extract_table_rows(page):
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
        explore_url = (
            "https://trends.google.com/trends/explore"
            f"?q={q}&date=now%201-d&geo=KR&hl=en"
        )

        out.append([title, volume, started, ended, explore_url, target_publish, breakdown])
    return out

def extract_card_rows(page):
    try:
        page.wait_for_selector("div.mZ3RIc", timeout=5000)
    except PlaywrightTimeoutError:
        return []
    cards = page.locator("div.mZ3RIc")
    total = cards.count()
    print(f"🃏 [card] found {total} cards – skipping the first one")
    out = []
    for i in range(1, total):
        c = cards.nth(i)
        title = c.locator("span.mUIrbf-vQzf8d").all_inner_texts()[0].strip()
        volume = c.locator("div.search-count-title").inner_text().strip()

        raw = c.locator("div.vdw3Ld").locator("xpath=..").inner_text().split("\n")
        parts = [l for l in raw if l and l.lower() not in ("trending_up", "timelapse")]
        started = parts[0].strip() if parts else ""
        ended = parts[1].strip() if len(parts) > 1 else ""
        target_publish = ended
        try:
            toggle = c.locator("div.vdw3Ld")
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

        q = quote(title)
        explore_url = (
            "https://trends.google.com/trends/explore"
            f"?q={q}&date=now%201-d&geo=KR&hl=en"
        )

        out.append([title, volume, started, ended, explore_url, target_publish, breakdown])
    return out

def scrape_all_pages():
    all_rows = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
        page = browser.new_page()
        page.goto("https://trends.google.com/trending?geo=KR&category=17&hl=en", timeout=60000)
        page.wait_for_load_state("networkidle")
        print("First page loaded")

        dismiss_cookie_banner(page)

        page_num = 1
        while True:
            print(f"📄 Scraping page {page_num}")
            batch = extract_table_rows(page) or extract_card_rows(page)
            print(f"  → got {len(batch)} rows")
            all_rows.extend(batch)

            next_btn = page.get_by_role("button", name="Go to next page")
            if not next_btn.count() or next_btn.first.is_disabled():
                print("No more pages")
                break

            next_btn.first.scroll_into_view_if_needed()
            next_btn.first.click()
            print("waiting 3 s…")
            time.sleep(3)
            page_num += 1

        browser.close()
    return all_rows

# --- GPT CLASSIFICATION WITH TRANSLATION ---
def translate_title(text):
    try:
        cleaned = re.sub(r"[^\w\s가-힣一-龯ぁ-ゔァ-ヴー々〆〤]", "", text).strip()
        translated = GoogleTranslator(source='auto', target='en').translate(cleaned)
        return translated
    except Exception as e:
        print(f"⚠️ Translation failed for '{text}': {e}")
        return text

def is_probably_name(text):
    return bool(re.match(r"^[A-Z][a-z]+( [A-Z][a-z]+)?$", text.strip()))

def classify_sport_only(titles, pause=0.5):
    results = []
    for original in titles:
        translated = translate_title(original)
        print(f"🔤 Translated '{original}' → '{translated}'")

        if is_probably_name(translated):
            prompt = (
                f"'{translated}' is a trending name. Determine which sport this person is most likely associated with. "
                "Only return the sport name, or 'Unknown' if clearly unrelated."
            )
            try:
                resp = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.5
                )
                sport = resp.choices[0].message.content.strip().splitlines()[0]
            except Exception as e:
                print(f"❌ GPT error for name: {e}")
                sport = "Unknown"
        else:
            batch_prompt = (
                "You will be given a Google Trends title. "
                "It may refer to a team, athlete, coach, stadium, tournament, or sports event. "
                "Your task is to identify the most likely sport. "
                "Return only the sport name, like 'Soccer', 'Basketball', etc., or 'Unknown'.\n\n"
                f"Title: {translated}"
            )
            try:
                resp = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": batch_prompt}],
                    temperature=0.5
                )
                sport = resp.choices[0].message.content.strip().splitlines()[0]
            except Exception as e:
                print(f"❌ GPT error for general title: {e}")
                sport = "Unknown"

        results.append({"sport": sport})
        time.sleep(pause)
    return results

# --- MAIN ENTRYPOINT ---
def main():
    sheet = connect_to_sheet("Trends")
    rows = scrape_all_pages()
    if not rows:
        print("No trends scraped; check selectors.")
        return
    titles = [r[0] for r in rows]
    classified = classify_sport_only(titles)
    enriched = [row + [info.get('sport', '')] for row, info in zip(rows, classified)]
    sheet.clear()
    sheet.append_rows(enriched, value_input_option="RAW")
    print(f"✅ Wrote {len(enriched)} rows (Sport➞Col H)")

if __name__ == "__main__":
    main()
