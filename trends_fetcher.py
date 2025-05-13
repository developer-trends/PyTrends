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

# --- GPT CLASSIFICATION ---
def classify_sport_only(titles, batch_size=10, pause=0.5):
    results = []
    for i in range(0, len(titles), batch_size):
        batch = titles[i:i + batch_size]
        user_prompt = (
            "You will be given a list of Korean Google Trends titles. Your task is:\n"
            "1. Translate each to English.\n"
            "2. Identify what it refers to — person, team, match, stadium, etc.\n"
            "3. Based on that, determine the most likely sport it is associated with "
            "(e.g. Soccer, Basketball, MMA, Baseball, Tennis).\n"
            "Only respond with 'Not a sport' if clearly unrelated to sports (e.g. movies, music, tech).\n\n"
            "Return ONLY valid JSON in this format:\n"
            "[{\"sport\": \"Basketball\"}, {\"sport\": \"Soccer\"}, ...]\n\n"
            f"Titles:\n{json.dumps(batch, ensure_ascii=False)}"
        )

        try:
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": user_prompt}],
                temperature=0
            )
            text = resp.choices[0].message.content.strip()
            print("\n🧠 GPT RAW RESPONSE:\n", text, "\n")
            if "```" in text:
                text = text.split("```")[-1].strip()
            start, end = text.find("["), text.rfind("]")
            json_str = text[start:end + 1] if start != -1 and end != -1 else "[]"
            try:
                parsed = json.loads(json_str)
            except JSONDecodeError:
                parsed = []
            aligned = []
            for j in range(len(batch)):
                if j < len(parsed) and isinstance(parsed[j], dict) and "sport" in parsed[j]:
                    aligned.append({"sport": parsed[j]["sport"]})
                else:
                    aligned.append({"sport": "Unknown"})
        except Exception as e:
            print(f"❌ OpenAI API error: {e}")
            aligned = [{"sport": "Unknown"} for _ in batch]
        results.extend(aligned)
        time.sleep(pause)
    return results

# --- MAIN ---
def main():
    sheet = connect_to_sheet("Trends")
    rows = scrape_all_pages()
    if not rows:
        print("No trends scraped.")
        return
    titles = [r[0] for r in rows]
    classified = classify_sport_only(titles)
    enriched = [row + [info.get("sport", "")] for row, info in zip(rows, classified)]
    sheet.clear()
    sheet.append_rows(enriched, value_input_option="RAW")
    print(f"✅ Wrote {len(enriched)} rows (Sport ⇢ Col H)")

if __name__ == "__main__":
    main()
