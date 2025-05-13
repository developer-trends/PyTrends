#!/usr/bin/env python3
import os
import json
import time
from json import JSONDecodeError
from urllib.parse import quote
import gspread
from openai import OpenAI
from oauth2client.service_account import ServiceAccountCredentials
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

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
    return gspread.authorize(creds).open(sheet_name).get_worksheet(0)

# --- SCRAPING LOGIC ---
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
    out = []
    for i in range(1, rows.count()):
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
            if p2: target_publish = p2[0].strip()
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
    out = []
    for i in range(1, cards.count()):
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
            raw2 = toggle.locator("xpath=..").inner_text().split("\n")
            p2 = [l for l in raw2 if l and l.lower() not in ("trending_up", "timelapse")]
            if p2: target_publish = p2[0].strip()
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
        page.wait_for_load_state("domcontentloaded", timeout=60000)
        page.wait_for_timeout(2000)
        print("First page loaded")
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

# --- GPT STEP 1: Translate Titles ---
def translate_titles(titles, batch_size=10):
    translated = []
    for i in range(0, len(titles), batch_size):
        batch = titles[i:i+batch_size]
        prompt = (
            "Translate the following Korean phrases to natural English, one for each. Return only JSON array:\n"
            f"{json.dumps(batch, ensure_ascii=False)}\n\n"
            "Format: [\"translation1\", \"translation2\", ...]"
        )
        try:
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0
            )
            text = resp.choices[0].message.content.strip()
            if "```" in text:
                text = text.split("```")[-1].strip()
            start, end = text.find("["), text.rfind("]")
            raw_json = text[start:end + 1] if start != -1 and end != -1 else "[]"
            parsed = json.loads(raw_json)
            translated.extend(parsed)
        except Exception as e:
            print(f"❌ Translation failed: {e}")
            translated.extend([""] * len(batch))
    return translated

# --- GPT STEP 2: Classify Translated Titles ---
def classify_translated_titles(english_titles, batch_size=10):
    results = []
    for i in range(0, len(english_titles), batch_size):
        batch = english_titles[i:i+batch_size]
        prompt = (
            "You will be given a list of English phrases that refer to trending topics. "
            "Each might refer to an athlete, player, team, stadium, event, or competition. "
            "Your task is to determine which sport it is most associated with (e.g. Soccer, Basketball, MMA, Baseball, Tennis, etc).\n\n"
            "Only return \"Not a sport\" if it's clearly unrelated.\n\n"
            f"Input: {json.dumps(batch, ensure_ascii=False)}\n\n"
            "Return JSON: [{\"sport\": \"Basketball\"}, ...]"
        )
        try:
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0
            )
            text = resp.choices[0].message.content.strip()
            if "```" in text:
                text = text.split("```")[-1].strip()
            start, end = text.find("["), text.rfind("]")
            raw_json = text[start:end + 1] if start != -1 and end != -1 else "[]"
            parsed = json.loads(raw_json)
            results.extend(parsed[:len(batch)])
        except Exception as e:
            print(f"❌ Classification failed: {e}")
            results.extend([{"sport": "Unknown"} for _ in batch])
    return results

# --- MAIN ENTRYPOINT ---
def main():
    sheet = connect_to_sheet("Trends")
    rows = scrape_all_pages()
    if not rows:
        print("No trends scraped.")
        return
    original_titles = [r[0] for r in rows]
    translated = translate_titles(original_titles)
    classified = classify_translated_titles(translated)
    enriched = [row + [info.get("sport", "Unknown")] for row, info in zip(rows, classified)]
    sheet.clear()
    sheet.append_rows(enriched, value_input_option="RAW")
    print(f"✅ Wrote {len(enriched)} rows (Sport ⇢ Col H)")

if __name__ == "__main__":
    main()
