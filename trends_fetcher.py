#!/usr/bin/env python3
import os
import json
import time
import gspread
from openai import OpenAI
from oauth2client.service_account import ServiceAccountCredentials

# --- CONFIGURATION ---
client = OpenAI(api_key=os.environ.get("GPT_AI"))

# --- GOOGLE SHEETS SETUP ---
def connect_to_sheet(sheet_name):
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds_dict = json.loads(os.environ["GOOGLE_SA_JSON"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    return gspread.authorize(creds).open(sheet_name).get_worksheet(0)

# --- TRANSLATE + CLASSIFY SPORT VIA GPT ---
def translate_and_classify(titles, batch_size=10, pause=0.5):
    results = []

    for i in range(0, len(titles), batch_size):
        batch = titles[i:i + batch_size]

        prompt = (
            "You will be given a list of Korean trend titles. For each one:\n"
            "1. Translate it into English as accurately as possible.\n"
            "2. Determine what sport it most likely belongs to (e.g. Soccer, Basketball, MMA, Baseball).\n"
            "If it is unrelated to sports, return: 'Not a sport'.\n\n"
            "Return a JSON array with the structure:\n"
            "[{\"translation\": \"<English text>\", \"sport\": \"<Sport>\"}, ...]\n\n"
            f"Trend titles:\n{json.dumps(batch, ensure_ascii=False)}"
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
            json_str = text[start:end + 1] if start != -1 and end != -1 else text

            try:
                data = json.loads(json_str)
            except json.JSONDecodeError:
                data = [{"translation": "", "sport": "Unknown"} for _ in batch]

        except Exception as e:
            print(f"❌ GPT Error: {e}")
            data = [{"translation": "", "sport": "Unknown"} for _ in batch]

        if len(data) != len(batch):
            print("⚠️ Mismatched count. Filling missing entries with Unknown.")
            data = data[:len(batch)] + [{"translation": "", "sport": "Unknown"}] * (len(batch) - len(data))

        results.extend(data)
        time.sleep(pause)

    return results

# --- MAIN ---
def main():
    sheet = connect_to_sheet("Trends")
    rows = sheet.get_all_values()

    if not rows or len(rows) <= 1:
        print("⚠️ Sheet is empty or has only headers.")
        return

    titles = [row[0] for row in rows[1:]]  # Skip header
    classified = translate_and_classify(titles)

    # Prepare full output rows
    updated_rows = []
    for original_row, result in zip(rows[1:], classified):
        sport = result.get("sport", "Unknown")
        extended_row = original_row + [""] * (7 - len(original_row))  # pad if needed
        extended_row.append(sport)
        updated_rows.append(extended_row)

    sheet.clear()
    sheet.append_row(rows[0] + ["Sport"])  # re-add header with 'Sport'
    sheet.append_rows(updated_rows, value_input_option="RAW")
    print(f"✅ Wrote {len(updated_rows)} rows with Sport (Col H)")

if __name__ == "__main__":
    main()
