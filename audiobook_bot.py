import os
import re
import json
import requests
from io import BytesIO
from PIL import Image
from playwright.sync_api import sync_playwright
from atproto import Client, client_utils, models

# --- CONFIGURATION ---
TARGET_URLS = [
    "https://elevenreader.io/explore",
    "https://elevenreader.io/free-audiobooks"
]
HISTORY_FILE = "posted_audiobooks.json"
MIN_HOURS = 3.5        # Minimum 3.5 hours runtime
MIN_RATING = 4.0       # Minimum 4.0 stars (if rated)

# Words in titles to skip so the feed feels like great fiction/narrative finds
SKIP_KEYWORDS = [
    "volume 1", "volume 2", "vol.", "decline and fall", "treatise",
    "textbook", "manual", "dictionary", "encyclopedia", "bible", "sermons"
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


def parse_duration_minutes(duration_str):
    if not duration_str:
        return 0
    h = re.search(r"(\d+)\s*h", duration_str, re.IGNORECASE)
    m = re.search(r"(\d+)\s*m", duration_str, re.IGNORECASE)
    return (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0)


def parse_card_metadata(raw_text, href, img_src):
    if "Included in Ultra" in raw_text or "$" in raw_text:
        return None
    if not re.search(r"\bFree\b", raw_text, re.IGNORECASE):
        return None

    dur_match = re.search(r"\b(\d+h(?:\s*\d+m)?|\d+m)\b", raw_text)
    duration_str = dur_match.group(1) if dur_match else ""
    total_minutes = parse_duration_minutes(duration_str)
    if total_minutes < (MIN_HOURS * 60):
        return None

    rating_match = re.search(r"\b([345]\.\d)\s*\(([\d\.K]+)\)", raw_text)
    rating = float(rating_match.group(1)) if rating_match else None
    reviews = rating_match.group(2) if rating_match else None

    if rating is not None and rating < MIN_RATING:
        return None

    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    clean_lines = [
        l for l in lines
        if l.lower() != "free" and not re.match(r"^(\d+h|\d+m|[345]\.\d)", l)
    ]
    if len(clean_lines) < 2:
        return None

    title = clean_lines[0]
    author = re.split(r"\d+h\b", clean_lines[1])[0].strip()

    # Filter out dry academic volumes or entries without cover art
    if any(k in title.lower() for k in SKIP_KEYWORDS):
        return None

    full_url = href if href.startswith("http") else f"https://elevenreader.io{href}"

    return {
        "title": title,
        "author": author,
        "duration": duration_str,
        "minutes": total_minutes,
        "rating": rating,
        "reviews": reviews,
        "cover": img_src or "",
        "url": full_url
    }


def find_best_audiobook(posted_urls):
    discovered = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            locale="en-US"
        )
        page = context.new_page()

        for url in TARGET_URLS:
            print(f"Scanning {url}...")
            try:
                page.goto(url, wait_until="networkidle", timeout=25000)
                for _ in range(5):
                    page.mouse.wheel(0, 1200)
                    page.wait_for_timeout(600)

                for a in page.locator("a[href*='/audiobooks/']").all():
                    try:
                        href = a.get_attribute("href") or ""
                        raw_text = a.inner_text(timeout=1000).strip()
                        if "Free" not in raw_text:
                            continue

                        img_loc = a.locator("img").first
                        img_src = img_loc.get_attribute("src") if img_loc.count() > 0 else ""

                        book = parse_card_metadata(raw_text, href, img_src)
                        if book and book["url"] not in posted_urls:
                            discovered[book["url"]] = book
                    except Exception:
                        continue
            except Exception as e:
                print(f"Warning scanning {url}: {e}")

        candidates = list(discovered.values())
        if not candidates:
            browser.close()
            return None

        # Prioritize books that have cover images and high ratings
        candidates.sort(
            key=lambda x: (
                1 if x["cover"] else 0,
                1 if x["rating"] is not None else 0,
                x["rating"] or 0,
                x["minutes"]
            ),
            reverse=True
        )

        chosen = candidates[0]

        # Visit the chosen book's detail page to grab the description & high-res cover
        try:
            page.goto(chosen["url"], wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(1500)
            og_desc = page.locator("meta[property='og:description']").first
            if og_desc.count() > 0:
                chosen["description"] = og_desc.get_attribute("content") or ""
            else:
                chosen["description"] = ""

            if not chosen["cover"]:
                og_img = page.locator("meta[property='og:image']").first
                if og_img.count() > 0:
                    chosen["cover"] = og_img.get_attribute("content") or ""
        except Exception:
            chosen["description"] = ""

        browser.close()
        return chosen


def generate_audio_hook(book):
    """Generates a punchy 1-sentence hook using Gemini Flash (minimal tokens)."""
    api_key = os.environ.get("GEMINI_API_KEY")
    fallback = f"Settle in for {book['duration']} of immersive, studio-voiced storytelling."
    if not api_key:
        return f'"{fallback}"'

    prompt = (
        f"Write a single, intriguing 1-sentence book hook (maximum 22 words) for the audiobook "
        f"'{book['title']}' by {book['author']}. "
        f"Description: {book.get('description', '')}. "
        f"Do not use clichés or exclamation marks. Return ONLY the single sentence inside quotes."
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
    """Downloads the cover image and returns (bytes, width, height) under 950KB for Bluesky."""
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


def post_to_bluesky(book, hook):
    handle = os.environ["BSKY_HANDLE"]
    password = os.environ["BSKY_PASSWORD"]

    client = Client()
    client.login(handle, password)

    rating_str = f" ⭐ {book['rating']}" if book.get("rating") else ""
    header = f"🎧 Evening Audio Find: {book['title']} by {book['author']} ({book['duration']}{rating_str})\n\n"

    tb = client_utils.TextBuilder()
    tb.text(header)
    tb.text(f"{hook}\n\n")
    tb.text("🔊 ")
    tb.link("Listen Free on ElevenReader", book["url"])
    tb.text(" • ")
    tb.link("thecuratedspine.com", "https://thecuratedspine.com")
    tb.text("\n\n")
    tb.tag("#BookSky", "BookSky")
    tb.text(" ")
    tb.tag("#Audiobooks", "Audiobooks")
    tb.text(" ")
    tb.tag("#FreeAudiobooks", "FreeAudiobooks")

    img_bytes, w, h = download_and_prep_image(book.get("cover"))
    embed = None
    if img_bytes and w and h:
        upload = client.upload_blob(img_bytes)
        embed = models.AppBskyEmbedImages.Main(
            images=[
                models.AppBskyEmbedImages.Image(
                    alt=f"Audiobook cover for {book['title']} by {book['author']}",
                    image=upload.blob,
                    aspect_ratio=models.AppBskyEmbedDefs.AspectRatio(width=w, height=h)
                )
            ]
        )

    client.send_post(tb, embed=embed)
    print(f"Successfully posted audiobook to Bluesky: {book['title']}")


if __name__ == "__main__":
    posted = load_history()
    book = find_best_audiobook(posted)
    if not book:
        print("No new qualifying free audiobooks found today.")
        exit(0)

    hook = generate_audio_hook(book)
    post_to_bluesky(book, hook)

    posted.add(book["url"])
    save_history(posted)
