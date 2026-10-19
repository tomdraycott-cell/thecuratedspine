import os
import json
import urllib.request
from atproto import Client, client_utils, models

def to_camel_case(text):
    """Sanitizes text into a clean CamelCase hashtag."""
    clean = "".join(ch for ch in str(text) if ch.isalnum() or ch.isspace())
    return "".join(word.capitalize() for word in clean.split())

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
    # 1. Load latest issue data
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

    # 2. Authenticate
    client = Client()
    client.login(os.environ["BSKY_HANDLE"], os.environ["BSKY_PASSWORD"])

    # 3. Post daily picks
    for book in books:
        genre = book.get('genre', 'Fiction')
        website_display = "thecuratedspine.com"
        website_url = "https://thecuratedspine.com"

        # --- A. POST KINDLE FEATURE ---
        title = book.get('title', 'Featured Title')
        raw_hook = book.get('hook', '').strip().strip('"')
        kindle_blob = upload_image(client, book.get('cover'))

        kindle_tags = ["BookSky", "FreeKindleBooks"]
        if 'Sci-Fi' in genre:
            kindle_tags.append("SciFi")
        elif 'Historical' in genre:
            kindle_tags.append("HistoricalFiction")
        elif 'Romance' in genre:
            kindle_tags.append("Romancelandia")
        elif 'Literary' in genre:
            kindle_tags.append("LiteraryFiction")

        prefix = f"📚 Today's featured {genre} on The Curated Spine:\n{title}\n\n"
        link_section = f"Read free: {website_display}\n\n"
        tags_section = " ".join([f"#{t}" for t in kindle_tags])

        overhead_len = len(prefix) + len(link_section) + len(tags_section) + 4
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
        tb.text("Read free: ")
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

        # --- B. POST AUDIOBOOK BONUS ---
        audio = book.get('audiobook')
        if audio and audio.get('title'):
            audio_title = audio.get('title')
            audio_author = audio.get('author', '')
            audio_blob = upload_image(client, audio.get('cover'))

            audio_tags = ["AudioSky", "Audiobooks", "FreeAudiobooks"]

            audio_prefix = f"🎧 Today's free {genre} Audiobook pick:\n{audio_title}\n"
            if audio_author:
                audio_prefix += f"by {audio_author}\n\n"
            else:
                audio_prefix += "\n"

            tb_audio = client_utils.TextBuilder()
            tb_audio.text(audio_prefix)
            tb_audio.text("Listen via Kobo / Spotify: ")
            tb_audio.link(website_display, website_url)
            tb_audio.text("\n\n")

            for i, tag in enumerate(audio_tags):
                tb_audio.tag(f"#{tag}", tag)
                if i < len(audio_tags) - 1:
                    tb_audio.text(" ")

            audio_embed = None
            if audio_blob:
                audio_embed = models.AppBskyEmbedImages.Main(
                    images=[models.AppBskyEmbedImages.Image(alt=f"Audiobook cover of {audio_title}", image=audio_blob)]
                )

            try:
                client.send_post(tb_audio, embed=audio_embed)
                print(f"Posted Audiobook pick: {audio_title}")
            except Exception as e:
                print(f"Failed to post Audiobook pick {audio_title}: {e}")

if __name__ == "__main__":
    post_to_bluesky()
