import os
import re
import json
import urllib.parse
import urllib.request
from playwright.sync_api import sync_playwright

# Each category now uses its true Amazon Node ID + "?_encoding=UTF8&tf=1" to force the Top 100 FREE chart
CATEGORIES = [
    {"name": "Science Fiction", "url": "https://www.amazon.com/Best-Sellers-Kindle-Store-Science-Fiction/zgbs/digital-text/158591011?_encoding=UTF8&tf=1"},
    {"name": "Historical Fiction", "url": "https://www.amazon.com/Best-Sellers-Kindle-Store-Historical-Fiction/zgbs/digital-text/157056011?_encoding=UTF8&tf=1"},
    {"name": "Literary Fiction", "url": "https://www.amazon.com/Best-Sellers-Kindle-Store-Literary-Fiction/zgbs/digital-text/157028011?_encoding=UTF8&tf=1"},
    {"name": "Romance", "url": "https://www.amazon.com/Best-Sellers-Kindle-Store-Romance/zgbs/digital-text/158566011?_encoding=UTF8&tf=1"}
]

def http_request(url, method="GET", headers=None, payload=None, timeout=15):
    """Native Python HTTP helper using urllib.request—no external packages needed."""
    if headers is None:
        headers = {}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        if "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        print(f"HTTP {e.code} error on {url}: {err_body}")
        return e.code, {}
    except Exception as e:
        print(f"Request error on {url}: {e}")
        return 0, {}

def clean_book_title(raw_title):
    """Strips series tags and subtitles so audiobook search engines match accurately."""
    cleaned = re.split(r"[:\(\-\[]", raw_title)[0].strip()
    return cleaned if len(cleaned) > 2 else raw_title.strip()

def load_sent_history():
    """Loads previously sent book identifiers from sent_history.json."""
    if os.path.exists("sent_history.json"):
        try:
            with open("sent_history.json", "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
                elif isinstance(data, dict):
                    return data.get("sent", [])
        except Exception as e:
            print(f"Could not read sent_history.json: {e}")
    return []

def save_sent_history(history_list):
    """Saves updated book identifiers back to sent_history.json."""
    try:
        with open("sent_history.json", "w", encoding="utf-8") as f:
            json.dump(history_list[-500:], f, indent=4)
    except Exception as e:
        print(f"Could not save sent_history.json: {e}")

def pop_override_asin():
    """Grabs the top ASIN from override_asins.txt if available and removes it from the queue."""
    if not os.path.exists("override_asins.txt"):
        return None
    try:
        with open("override_asins.txt", "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f.readlines() if line.strip() and not line.strip().startswith("#")]
        if not lines:
            return None
        chosen_asin = lines[0]
        with open("override_asins.txt", "w", encoding="utf-8") as f:
            for line in lines[1:]:
                f.write(line + "\n")
        return chosen_asin
    except Exception as e:
        print(f"Error reading override_asins.txt: {e}")
        return None

def generate_hook_and_filter(title, author, genre):
    """Calls the Gemini REST API using Python's built-in urllib.request."""
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return "An exciting new read to add to your digital library."

    prompt = f"""
    You are an editorial assistant for a premium book curation newsletter called 'The Curated Spine'.
    Evaluate this book: "{title}" by {author}. Genre: {genre}.
    
    1. If the title sounds like cheap erotica, low-quality spam, or a wildly off-brand subgenre, reply with exactly: REJECT
    2. If it is a quality book, write a compelling, one-sentence editorial hook (under 95 characters) to entice readers. Do not use quotes.
    """

    for model_name in ["gemini-1.5-flash", "gemini-2.0-flash"]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        status, data = http_request(
            url,
            method="POST",
            payload={"contents": [{"parts": [{"text": prompt}]}]}
        )
        if status == 200 and "candidates" in data:
            try:
                result = data["candidates"][0]["content"]["parts"][0]["text"].strip().strip('"')
                if "REJECT" in result.upper():
                    return None
                return result
            except (KeyError, IndexError):
                continue

    return "An exciting new read to add to your digital library."

def fetch_override_book(page, asin, genre_name):
    """Fetches book details directly from an Amazon ASIN page."""
    url = f"https://www.amazon.com/dp/{asin}"
    print(f"   -> Fetching override ASIN {asin}...")
    try:
        page.goto(url, wait_until="domcontentloaded")
        title = page.locator("#productTitle").inner_text(timeout=5000).strip()
        author_loc = page.locator(".author a").first
        author = author_loc.inner_text(timeout=3000).strip() if author_loc.count() > 0 else "Featured Author"
        cover = page.locator("#landingImage, #ebooksImgBlkFront").first.get_attribute("src")
        hook = generate_hook_and_filter(title, author, genre_name) or "A standout featured selection for your Kindle."
        return {
            "asin": asin,
            "genre": genre_name,
            "title": title,
            "author": author,
            "cover": cover,
            "hook": hook,
            "amazon_url": url
        }
    except Exception as e:
        print(f"   Override ASIN fetch error: {e}")
        return None

def fetch_kindle_books(page, category_url):
    """Scrapes Amazon for the top free Kindle books and strictly verifies $0.00 pricing."""
    page.goto(category_url, wait_until="domcontentloaded")
    books = []

    try:
        page.wait_for_selector(".zg-grid-general-faceout", timeout=8000)
        items = page.locator(".zg-grid-general-faceout").all()

        for item in items[:20]:
            try:
                card_text = item.inner_text()
                # Strictly require $0.00 or Free to purchase (reject $5.99 paid books)
                if "$0.00" not in card_text and "Free" not in card_text:
                    continue

                clamped = item.locator("div._cDEzb_p13n-sc-css-line-clamp-1_1Fn1y, div._cDEzb_p13n-sc-css-line-clamp-2_1Fn1y").all()
                if len(clamped) >= 2:
                    title = clamped[0].inner_text().strip()
                    author = clamped[1].inner_text().strip()
                else:
                    title = item.locator("img").get_attribute("alt") or ""
                    author = "Indie Author"

                cover = item.locator("img").first.get_attribute("src")
                link = item.locator("a.a-link-normal").first.get_attribute("href")

                if title and link:
                    full_url = f"https://www.amazon.com{link.split('?')[0]}"
                    asin_match = re.search(r"/dp/([A-Z0-9]{10})", full_url)
                    asin = asin_match.group(1) if asin_match else title

                    books.append({
                        "asin": asin,
                        "title": title,
                        "author": author,
                        "cover": cover,
                        "amazon_url": full_url
                    })
            except Exception:
                continue
    except Exception as e:
        print(f"Amazon scraping error: {e}")

    print(f"   -> Found {len(books)} verified $0.00 Kindle books in this category.")
    return books

def find_vetted_audiobook(page, candidate_books):
    """
    Checks if any of the top-rated Amazon Kindle books have a matching audiobook 
    edition on Kobo/Spotify (which share Findaway Voices distribution).
    """
    print("   -> Cross-checking top Amazon books for wide audiobook editions...")

    for candidate in candidate_books[:6]:
        short_title = clean_book_title(candidate["title"])
        author_last = candidate["author"].split()[-1] if candidate.get("author") else ""
        query = urllib.parse.quote(f"{short_title} {author_last}".strip())
        kobo_search_url = f"https://www.kobo.com/us/en/search?query={query}&fcmedia=AudioBook&fclanguages=en"

        try:
            page.goto(kobo_search_url, wait_until="domcontentloaded")
            if page.locator(".item-detail").count() == 0:
                continue

            first_item = page.locator(".item-detail").first
            found_title = first_item.locator(".title").inner_text(timeout=3000).strip()
            author_loc = first_item.locator(".contributor-name").first
            found_author = author_loc.inner_text(timeout=2000).strip() if author_loc.count() > 0 else candidate["author"]

            if short_title.lower()[:8] not in found_title.lower():
                continue

            cover = first_item.locator(".item-image img").first.get_attribute("src") or candidate["cover"]
            if cover.startswith("//"):
                cover = "https:" + cover
            cover = cover.replace("180/180/False", "300/300/False")

            link = first_item.locator(".title a, a.title").first.get_attribute("href") or ""
            full_kobo_url = link if link.startswith("http") else f"https://www.kobo.com{link}"

            search_term = urllib.parse.quote(f"{short_title} {found_author}".strip())
            print(f"   -> Matched vetted audiobook: {found_title} by {found_author}")

            return {
                "title": found_title,
                "author": found_author,
                "cover": cover,
                "kobo_url": full_kobo_url,
                "spotify_url": f"https://open.spotify.com/search/{search_term}/audiobooks",
                "audible_url": f"https://www.audible.com/search?keywords={search_term}"
            }
        except Exception:
            continue

    print("   -> No matching wide audiobook found for this genre's top picks today.")
    return None

def dispatch_mailerlite(campaign_html):
    """Creates and sends the MailerLite campaign using built-in urllib.request."""
    api_key = os.environ.get("MAILERLITE_API_KEY")
    sender_email = os.environ.get("MAILERLITE_SENDER_EMAIL", "newsletter@thecuratedspine.com")
    if not api_key:
        print("No MAILERLITE_API_KEY found; skipping email dispatch.")
        return

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "TheCuratedSpineBot/1.0"
    }

    group_ids = []
    env_group = os.environ.get("MAILERLITE_GROUP_ID")
    if env_group:
        group_ids = [env_group]
    else:
        status, grp_data = http_request("https://connect.mailerlite.com/api/groups", headers=headers)
        if status == 200:
            group_ids = [str(g["id"]) for g in grp_data.get("data", [])]

    url = "https://connect.mailerlite.com/api/campaigns"
    payload = {
        "name": "Daily Curated Spine Dispatch",
        "type": "regular",
        "emails": [{
            "subject": "📚 Your Daily Curated Spine: Free Kindle & Audiobooks",
            "from_name": "The Curated Spine",
            "from": sender_email
        }]
    }
    if group_ids:
        payload["groups"] = group_ids

    status, camp_data = http_request(url, method="POST", headers=headers, payload=payload)
    if status not in [200, 201] or "data" not in camp_data:
        print(f"Failed to create campaign (status {status})")
        return

    campaign_id = camp_data["data"]["id"]

    content_payload = {
        "html": campaign_html,
        "plain_text": "Please view this email in an HTML-compatible email client or visit https://thecuratedspine.com"
    }
    http_request(f"{url}/{campaign_id}/content", method="PUT", headers=headers, payload=content_payload)

    schedule_payload = {"delivery": "instant"}
    sched_status, _ = http_request(f"{url}/{campaign_id}/schedule", method="POST", headers=headers, payload=schedule_payload)
    if sched_status in [200, 201]:
        print(f"SUCCESS: MailerLite campaign dispatched! ID: {campaign_id}")
    else:
        print(f"Campaign created ({campaign_id}), schedule status: {sched_status}")

def main():
    sent_history = load_sent_history()
    today_picks = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Force US English locale headers so Amazon serves US $0.00 pricing
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            locale="en-US"
        )
        page = context.new_page()

        override_asin = pop_override_asin()

        for index, category in enumerate(CATEGORIES):
            print(f"-> Fetching {category['name']}...")
            raw_books = fetch_kindle_books(page, category["url"])
            selected_kindle = None

            if index == 0 and override_asin:
                selected_kindle = fetch_override_book(page, override_asin, category["name"])

            if not selected_kindle:
                for book in raw_books:
                    if book["asin"] in sent_history or book["title"] in sent_history:
                        continue
                    hook = generate_hook_and_filter(book["title"], book["author"], category["name"])
                    if hook:
                        book["hook"] = hook
                        book["genre"] = category["name"]
                        selected_kindle = book
                        break

            if selected_kindle:
                sent_history.append(selected_kindle.get("asin", selected_kindle["title"]))
                candidates = [selected_kindle] + [b for b in raw_books if b["title"] != selected_kindle["title"]]
                audio_bonus = find_vetted_audiobook(page, candidates)
                if audio_bonus:
                    selected_kindle["audiobook"] = audio_bonus
                today_picks.append(selected_kindle)

        browser.close()

    save_sent_history(sent_history)
    with open("latest_issue.json", "w", encoding="utf-8") as f:
        json.dump({"books": today_picks}, f, indent=4)

    if not today_picks:
        print("No picks found today; aborting email dispatch.")
        return

    html_content = """
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: auto; padding: 20px;">
        <h1 style="text-align: center; color: #2c3e50;">The Curated Spine</h1>
        <p style="text-align: center; color: #7f8c8d;">Top-quality free & discounted books in your inbox.</p>
        <hr style="border: 0; height: 1px; background: #eee; margin-bottom: 30px;">
    """

    for pick in today_picks:
        html_content += f
