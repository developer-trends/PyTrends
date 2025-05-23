import schedule
import time
import requests

def trigger_json():
    url = "https://firstplaydev.wpenginepowered.com/wp-content/themes/hello-theme-child/index-json.php"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/html,application/xhtml+xml"
    }
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            print("✅ index.json regenerated")
        else:
            print(f"❌ Failed: HTTP {response.status_code}")
    except Exception as e:
        print(f"❌ Error: {e}")

schedule.every(1).minutes.do(trigger_json)

while True:
    schedule.run_pending()
    time.sleep(1)
