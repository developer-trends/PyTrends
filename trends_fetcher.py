#!/usr/bin/env python3
import os
import json
from json import JSONDecodeError
import time
from urllib.parse import quote
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from openai import OpenAI

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
        parts = [l for l in raw if l and l.lower() not in ("trending_up","timelapse")]
        started = parts[0].strip() if parts else ""
        ended = parts[1].strip() if len(parts) > 1 else ""

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
        explore_url = (
            "https://trends.google.com/trends/explore"
            f"?q={q}&date=now%201-d&geo=KR&hl=en"
        )

        out.append([title, volume, started, ended, explore_url, target_publish, breakdown])
    return out

# --- RELATED TOPICS SCRAPER ---
def get_related_topics_from_url(browser, url):
    try:
        page = browser.new_page()
        page.goto(url, timeout=30000)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1000)
        dismiss_cookie_banner(page)

        page.wait_for_selector("div.feed-item", timeout=5000)
        topics = page.locator("div.feed-item").all_inner_texts()
        topics = [t.strip() for t in topics if t.strip()]
        return topics[:10]  # Limit to top 10
    except Exception as e:
        print(f"❌ Failed to load Related Topics from {url}: {e}")
        return []
    finally:
        try:
            page.close()
        except:
            pass

# --- GPT CLASSIFICATION FROM RELATED TOPICS ---
def classify_sport_from_topics(all_topic_lists, batch_size=10, pause=0.5):
    results = []
    for i in range(0, len(all_topic_lists), batch_size):
        batch = all_topic_lists[i:i+batch_size]

        # Build prompt from batch
        formatted = json.dumps(batch, ensure_ascii=False)
        prompt = (
            "You will be given a list of lists. Each sublist contains topics from Google Trends' 'Related Topics' for a trend.\n"
            "For each sublist, determine what sport it most likely relates to (e.g. Soccer, Basketball, MMA, Baseball).\n"
            "If it doesn't relate to sports, respond with \"Not a sport\".\n\n"
            "Return a JSON array of strings like: [\"Soccer\", \"Basketball\", \"Not a sport\", ...]\n\n"
            f"Topics: {formatted}"
        )

        try:
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0
            )
            text = resp.choices[0].message.content.strip()
            print("🔎 RAW GPT RESPONSE:\n", text)

            if "```" in text:
                text = text.split("```")[-1].strip()
            start, end = text.find("["), text.rfind("]")
            json_str = text[start:end+1] if start != -1 and end != -1 else text
            try:
                data = json.loads(json_str)
            except JSONDecodeError:
                data = ["Unknown"] * len(batch)
        except Exception as e:
            print(f"❌ OpenAI error: {e}")
            data = ["Unknown"] * len(batch)

        # Align result count
        if len(data) != len(batch):
            data = data[:len(batch)] + ["Unknown"] * (len(batch) - len(data))

        results.extend(data)
        time.sleep(pause)
    return results

# --- MAIN ---
def main():
    sheet = connect_to_sheet("Trends")
    rows = scrape_all_pages()
    if not rows:
        print("No trends scraped; check selectors.")
        return

    explore_urls = [r[4] for r in rows]  # Col E
    all_topics = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        for url in explore_urls:
            topics = get_related_topics_from_url(browser, url)
            all_topics.append(topics)
        browser.close()

    sports = classify_sport_from_topics(all_topics)

    enriched = [row + [sport] for row, sport in zip(rows, sports)]
    sheet.clear()
    sheet.append_rows(enriched, value_input_option="RAW")
    print(f"✅ Wrote {len(enriched)} rows (Sport ⇢ Col H)")

if __name__ == "__main__":
    main()
