import os
import re
import json
import math
import time
import requests
from io import BytesIO
from PIL import Image
from playwright.sync_api import sync_playwright
from atproto import Client, client_utils, models

# --- CONFIGURATION ---
HISTORY_FILE = "posted_kindle_books.json"
MIN_RATING = 4.2
MIN_REVIEWS = 40

# The 4 daily genres (Romance shifted slightly upmarket toward Contemporary/Women's Fiction)
GENRES = [
    {
        "name": "Sci-Fi",
        "tag": "SciFi",
        "url": "https://www.amazon.com/Best-Sellers-Kindle-Store-Science-Fiction-eBooks/zgbs/digital-text/158591011?tf=1"
    },
    {
        "name": "Mystery & Thriller",
        "tag": "MysteryThriller",
        "url": "https://www.amazon.com/Best-Sellers-Kindle-Store-Mystery-Thriller-Suspense-eBooks/zgbs/digital-text/157305011?tf=1"
    },
    {
        "name": "Literary Fiction",
        "tag": "BookSky",
        "url": "https://www.amazon.com/Best-Sellers-Kindle-Store-Literature-Fiction-eBooks/zgbs/digital-text/157028011?tf=1"
    },
    {
        "name": "Romance",
        "tag": "Romancelandia",
        "url": "https://www.amazon.com/Best-Sellers-Kindle-Store-Contemporary-Romance-eBooks/zgbs/digital-text/158568011?tf=1"
    }
]

# Downmarket / pulp tropes to automatically disqualify
PULP_BLACKLIST = [
    "countess", "duke", "viscount", "earl", "marquess", "highlander", "laird",
    "rake", "billionaire", "millionaire", "tycoon", "ceo", "alpha", "omega",
    "mafia", "cartel", "bratva", "mc ", "motorcycle club", "biker", "bad boy",
    "secret baby", "accidental pregnancy", "virgin", "daddy", "silver fox",
    "harem", "reverse harem", "why choose", "shifter", "werewolf", "vampire clan",
    "mail order bride", "amish", "stepbrother", "bully", "dark romance"
]

# Upmarket / bestseller trust signals that receive a massive Quality Score boost
PRESTIGE_SIGNALS = [
    "usa today", "new york times", "nyt bestselling", "wall street journal",
    "sunday times", "award winner", "award-winning", "pulitzer", "booker",
    "hugo award", "nebula award", "edgar award", "book club", "national bestseller"
]


def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_history(posted_set):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(list(posted_set), f, indent=2)


def is_pulp_title(title):
    t_lower = title.lower()
    return any(bad_word in t_lower for bad_word in PULP_BLACKLIST)


def clean_amazon_cover_url(img_url):
    """Strips Amazon thumbnail resizing modifiers to fetch the crisp, unpadded full-res cover."""
    if not img_url:
        return ""
    return re.sub(r"\._[A-Za-z0-9,_\-]+_\.", ".", img_url)


def calculate_quality_score(book):
    """
    Calculates a weighted Quality Score:
    - Base score = rating * log10(reviews)
    - +10.0 bonus for USA Today / NYT / Award Winner signals
    - -5.0 penalty if pulp keywords appear in the extended blurb
    """
    reviews = book.get("reviews", 0)
    rating = book.get("rating", 0.0)
    if reviews < MIN_REVIEWS or rating < MIN_RATING:
        return 0.0

    score = rating * math.log10(max(reviews, 10))

    combined_text = f"{book.get('title', '')} {book.get('description', '')}".lower()
    for signal in PRESTIGE_SIGNALS:
        if signal in combined_text:
            score += 10.0
            book["prestige_badge"] = signal.upper()
            break

    # Secondary check on the blurb for downmarket tropes
    if any(bad in combined_text for bad in PULP_BLACKLIST):
        score -= 6.0

    return round(score, 2)


def find_top_book_for_genre(page, genre_cfg, posted_asins):
    print(f"\nScanning {genre_cfg['name']}: {genre_cfg['url']}")
    candidates = []

    try:
        page.goto(genre_cfg["url"], wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(2000)
        # Scroll to load the full Top 30–50 chart items
        for _ in range(3):
            page.mouse.wheel(0, 1500)
            page.wait_for_timeout(700)

        cards = page.locator("div[id^='p13n-asin-index-']").all()
        for card in cards:
            try:
                raw_text = card.inner_text(timeout=1000)
                # Confirm it's genuinely $0.00
                if "$0.00" not in raw_text and "Free" not in raw_text:
                    continue

                link_loc = card.locator("a[href*='/dp/']").first
                if link_loc.count() == 0:
                    continue
                href = link_loc.get_attribute("href") or ""
                asin_match = re.search(r"/dp/([A-Z0-9]{10})", href)
                if not asin_match:
                    continue
                asin = asin_match.group(1)

                if asin in posted_asins:
                    continue

                # Extract title & author from card lines
                lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
                lines = [l for l in lines if not re.match(r"^#\d+$", l)]
                if len(lines) < 2:
                    continue

                title = lines[0]
                author = lines[1]

                if is_pulp_title(title):
                    print(f"  [Skipped Pulp Title]: {title}")
                    continue

                # Extract rating & review count (e.g. "4.5 out of 5 stars" and "11,417")
                rating_match = re.search(r"([345]\.\d)\s*out of 5", raw_text)
                if not rating_match:
                    rating_match = re.search(r"\b([345]\.\d)\b", raw_text)
                rating = float(rating_match.group(1)) if rating_match else 0.0

                # Look for review count next to star rating
                reviews = 0
                rev_matches = re.findall(r"\b(\d{1,3}(?:,\d{3})+|\d{2,6})\b", raw_text)
                for rm in rev_matches:
                    val = int(rm.replace(",", ""))
                    if val >= MIN_REVIEWS and val != 0:
                        reviews = val
                        break

                if rating < MIN_RATING or reviews < MIN_REVIEWS:
                    continue

                img_loc = card.locator("img").first
                img_src = img_loc.get_attribute("src") if img_loc.count() > 0 else ""
                high_res_cover = clean_amazon_cover_url(img_src)

                prelim_book = {
                    "asin": asin,
                    "title": title,
                    "author": author,
                    "rating": rating,
                    "reviews": reviews,
                    "cover": high_res_cover,
                    "url": f"https://www.amazon.com/dp/{asin}",
                    "genre_name": genre_cfg["name"],
                    "genre_tag": genre_cfg["tag"],
                    "description": ""
                }
                prelim_book["prelim_score"] = calculate_quality_score(prelim_book)
                candidates.append(prelim_book)

            except Exception:
                continue

        if not candidates:
            return None

        # Sort by preliminary score (Rating * log10(Reviews)) and inspect the Top 5 product pages
        candidates.sort(key=lambda x: x["prelim_score"], reverse=True)
        finalists = candidates[:5]

        for book in finalists:
            try:
                page.goto(book["url"], wait_until="domcontentloaded", timeout=18000)
                page.wait_for_timeout(1200)

                # Grab product description/blurb to check for "USA Today" / "New York Times"
                desc_loc = page.locator("#bookDescription_feature_div").first
                if desc_loc.count() > 0:
                    book["description"] = desc_loc.inner_text(timeout=1500).strip()
                else:
                    meta_desc = page.locator("meta[name='description']").first
                    if meta_desc.count() > 0:
                        book["description"] = meta_desc.get_attribute("content") or ""

                # Grab crisp cover from product page if needed
                main_img = page.locator("#ebooksImgBlkFront, #imgBlkFront, #landingImage").first
                if main_img.count() > 0:
                    src = main_img.get_attribute("src") or ""
                    if src:
                        book["cover"] = clean_amazon_cover_url(src)

                book["final_score"] = calculate_quality_score(book)
                badge_info = f" [+{book.get('prestige_badge')}]" if book.get("prestige_badge") else ""
                print(
                    f"  -> Finalist: {book['title'][:42]} | "
                    f"⭐ {book['rating']} ({book['reviews']:,} revs){badge_info} | "
                    f"Score: {book['final_score']}"
                )
            except Exception:
                book["final_score"] = book["prelim_score"]

        # Sort finalists by final Quality Score descending and pick #1
        finalists.sort(key=lambda x: x.get("final_score", 0), reverse=True)
        winner = finalists[0]
        print(f"🏆 WINNER ({genre_cfg['name']}): {winner['title']} (Score: {winner['final_score']})")
        return winner

    except Exception as e:
        print(f"Error scanning {genre_cfg['name']}: {e}")
        return None


def generate_book_hook(book):
    """Generates a custom 1-sentence editorial hook via Gemini Flash."""
    api_key = os.environ.get("GEMINI_API_KEY")
    fallback = f"A standout {book['genre_name'].lower()} pick boasting {book['reviews']:,} reader ratings."
    if not api_key:
        return f'"{fallback}"'

    prompt = (
        f"Write a sharp, intriguing 1-sentence editorial book hook (maximum 22 words) for "
        f"'{book['title']}' by {book['author']} ({book['genre_name']}). "
        f"Synopsis: {book.get('description', '')[:700]}. "
        f"Avoid cheesy clichés and exclamation marks. Return ONLY the single sentence inside quotes."
    )
    try:
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}",
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=12
        )
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip().strip('"')
        return f'"{text}"'
    except Exception:
        return f'"{fallback}"'


def download_and_prep_image(url):
    """Downloads the unpadded vertical cover image and returns (bytes, width, height)."""
    if not url:
        return None, None, None
    try:
        r = requests.get(url, timeout=12)
        r.raise_for_status()
        img = Image.open(BytesIO(r.content)).convert("RGB")
        w, h = img.size
        out = BytesIO()
        img.save(out, format="JPEG", quality=88)
        return out.getvalue(), w, h
    except Exception as e:
        print(f"Cover download failed: {e}")
        return None, None, None


def post_book_to_bluesky(client, book, hook):
    header = (
        f"📚 Today's featured {book['genre_name']} on The Curated Spine:\n"
        f"{book['title']}\n\n"
    )

    tb = client_utils.TextBuilder()
    tb.text(header)
    tb.text(f"{hook}\n\n")
    tb.text("🔗 ")
    tb.link("Get on Amazon", book["url"])
    tb.text(" • ")
    tb.link("thecuratedspine.com", "https://thecuratedspine.com")
    tb.text("\n\n")
    tb.tag("#BookSky", "BookSky")
    tb.text(" ")
    tb.tag("#FreeKindleBooks", "FreeKindleBooks")
    if book["genre_tag"] != "BookSky":
        tb.text(" ")
        tb.tag(f"#{book['genre_tag']}", book["genre_tag"])

    img_bytes, w, h = download_and_prep_image(book.get("cover"))
    embed = None
    if img_bytes and w and h:
        upload = client.upload_blob(img_bytes)
        embed = models.AppBskyEmbedImages.Main(
            images=[
                models.AppBskyEmbedImages.Image(
                    alt=f"Book cover for {book['title']} by {book['author']} (⭐ {book['rating']} from {book['reviews']:,} reviews)",
                    image=upload.blob,
                    aspect_ratio=models.AppBskyEmbedDefs.AspectRatio(width=w, height=h)
                )
            ]
        )

    client.send_post(tb, embed=embed)
    print(f"Posted to Bluesky: {book['title']}")


if __name__ == "__main__":
    posted_asins = load_history()
    selected_books = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="en-US"
        )
        page = context.new_page()

        for genre_cfg in GENRES:
            winner = find_top_book_for_genre(page, genre_cfg, posted_asins)
            if winner:
                selected_books.append(winner)
                posted_asins.add(winner["asin"])

        browser.close()

    if not selected_books:
        print("No qualifying books found today.")
        exit(0)

    handle = os.environ["BSKY_HANDLE"]
    password = os.environ["BSKY_PASSWORD"]
    bsky_client = Client()
    bsky_client.login(handle, password)

    for idx, book in enumerate(selected_books):
        hook = generate_book_hook(book)
        post_book_to_bluesky(bsky_client, book, hook)
        save_history(posted_asins)

        # Wait 60 seconds between posts to keep Bluesky's spam filter completely happy
        if idx < len(selected_books) - 1:
            print("Waiting 60 seconds before next post...")
            time.sleep(60)
