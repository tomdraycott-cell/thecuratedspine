import os
import re
import json
import urllib.request
from atproto import Client, client_utils, models

def clean_amazon_cover_url(url):
    """
    Strips Amazon's ._AC_UL600_SR600,400_ modifiers so Amazon serves the pure, 
    unpadded vertical book cover without white sidebars or KU banners.
    """
    if not url:
        return ""
    if "amazon.com" in url or "media-amazon.com" in url or "ssl-images-amazon.com" in url:
        return re.sub(r"\._[A-Za-z0-9,\-_]+_\.(jpg|png|jpeg)$", r"._SL1000_.\1", url, flags=re.IGNORECASE)
    return url

def upload_image(client, image_url):
    """Downloads an image from a URL and uploads it as a Bluesky blob."""
    if not image_url:
        return None
    clean_url = clean_amazon_cover_url(image_url)
    try:
        req = urllib.request.Request(clean_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as response:
            image_data = response.read()
        return client.upload_blob(image_data).blob
    except Exception as e:
        print(f"Could not upload image {clean_url}: {e}")
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

        # --- A. POST KINDLE FEATURE ---
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
            # Explicit 2:3 vertical aspect ratio prevents Bluesky from letterboxing the cover
            kindle_embed = models.AppBskyEmbedImages.Main(
                images=[
                    models.AppBskyEmbedImages.Image(
                        alt=f"Cover of {title}",
                        image=kindle_blob,
                        aspect_ratio=models.AppBskyEmbedDefs.AspectRatio(width=2, height=3)
                    )
                ]
            )

        try:
            client.send_post(tb, embed=kindle_embed)
            print(f"Posted Kindle pick: {title}")
        except Exception as e:
            print(f"Failed to post Kindle pick {title}: {e}")

        # --- B. POST AUDIOBOOK COMPANION (IF PRESENT) ---
        audio = book.get('audiobook')
        if audio and audio.get('title'):
            audio_title = audio.get('title')
            audio_author = audio.get('author', '')
            spotify_url = audio.get('spotify_url', website_url)
            kobo_url = audio.get('kobo_url', website_url)
            audio_blob = upload_image(client, audio.get('cover'))

            audio_tags = ["AudioSky", "Audiobooks", "BookSky"]

            audio_prefix = f"🎧 Companion {genre} Audiobook:\n{audio_title}\n"
            if audio_author:
                audio_prefix += f"by {audio_author}\n\n"
            else:
                audio_prefix += "\n"

            tb_audio = client_utils.TextBuilder()
            tb_audio.text(audio_prefix)
            tb_audio.text("🔗 ")
            tb_audio.link("Spotify", spotify_url)
            tb_audio.text(" • ")
            tb_audio.link("Kobo", kobo_url)
            tb_audio.text(" • ")
            tb_audio.link(website_display, website_url)
            tb_audio.text("\n\n")

            for i, tag in enumerate(audio_tags):
                tb_audio.tag(f"#{tag}", tag)
                if i < len(audio_tags) - 1:
                    tb_audio.text(" ")

            audio_embed = None
            if audio_blob:
                # Explicit 1:1 square aspect ratio for audiobook covers
                audio_embed = models.AppBskyEmbedImages.Main(
                    images=[
                        models.AppBskyEmbedImages.Image(
                            alt=f"Audiobook cover of {audio_title}",
                            image=audio_blob,
                            aspect_ratio=models.AppBskyEmbedDefs.AspectRatio(width=1, height=1)
                        )
                    ]
                )

            try:
                client.send_post(tb_audio, embed=audio_embed)
                print(f"Posted Audiobook pick: {audio_title}")
            except Exception as e:
                print(f"Failed to post Audiobook pick {audio_title}: {e}")

if __name__ == "__main__":
    post_to_bluesky()
