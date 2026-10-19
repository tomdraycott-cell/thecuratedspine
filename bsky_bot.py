import os
import json
import urllib.request
from atproto import Client, client_utils, models

def upload_image(client, image_url):
    """Downloads an image from a URL and uploads it as a Bluesky blob."""
    if not image_url:
        return None
    try:
        req = urllib.request.Request(image_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            image_data = response.read()
        return client.upload_blob(image_data).blob
    except Exception as e:
        print(f"Could not upload image {image_url}: {e}")
        return None

def post_to_bluesky():
    try:
        with open('latest_issue.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
            books = data.get('books', [])
            if not books:
                print("No books found for today.")
                return
    except Exception as e:
        print(f"Error loading issue data: {e}")
        return

    client = Client()
    client.login(os.environ["BSKY_HANDLE"], os.environ["BSKY_PASSWORD"])

    for book in books:
        genre = book.get('genre', 'Fiction')
        title = book.get('title', 'Featured Title')
        raw_hook = book.get('hook', '').strip().strip('"')
        amazon_url = book.get('amazon_url', 'https://thecuratedspine.com')
        website_display = "thecuratedspine.com"
        website_url = "https://thecuratedspine.com"

        kindle_blob = upload_image(client, book.get('cover'))

        kindle_tags = ["BookSky", "FreeKindleBooks"]
        if 'Sci' in genre:
            kindle_tags.append("SciFi")
        elif 'Historical' in genre:
            kindle_tags.append("HistoricalFiction")
        elif 'Romance' in genre:
            kindle_tags.append("Romancelandia")
        elif 'Literary' in genre:
            kindle_tags.append("LiteraryFiction")

        prefix = f"📚 Today's featured {genre} on The Curated Spine:\n{title}\n\n"
        # "🔗 Get on Amazon • thecuratedspine.com\n\n" is 40 characters
        link_section_len = 42
        tags_section = " ".join([f"#{t}" for t in kindle_tags])

        overhead_len = len(prefix) + link_section_len + len(tags_section) + 4
        available_for_hook = 290 - overhead_len

        if available_for_hook > 20 and raw_hook:
            hook_text = (raw_hook[:available_for_hook - 3].rstrip() + "...") if len(raw_hook) > available_for_hook else raw_hook
            hook_section = f"\"{hook_text}\"\n\n"
        else:
            hook_section = ""

        tb = client_utils.TextBuilder()
        tb.text(prefix)
        if hook_section:
            tb.text(hook_section)

        # Dual clickable links: Direct to Amazon + Newsletter Site
        tb.text("🔗 ")
        tb.link("Get on Amazon", amazon_url)
        tb.text(" • ")
        tb.link(website_display, website_url)
        tb.text("\n\n")

        for i, tag in enumerate(kindle_tags):
            tb.tag(f"#{tag}", tag)
            if i < len(kindle_tags) - 1:
                tb.text(" ")

        kindle_embed = None
        if kindle_blob:
            kindle_embed = models.AppBskyEmbedImages.Main(
                images=[models.AppBskyEmbedImages.Image(alt=f"Cover of {title}", image=kindle_blob)]
            )

        try:
            client.send_post(tb, embed=kindle_embed)
            print(f"Posted Kindle pick: {title}")
        except Exception as e:
            print(f"Failed to post Kindle pick {title}: {e}")

if __name__ == "__main__":
    post_to_bluesky()
