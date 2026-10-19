import os
import json
import urllib.request
from atproto import Client, client_utils, models

def to_camel_case(text):
    """Sanitizes text into a clean CamelCase hashtag."""
    clean = "".join(ch for ch in str(text) if ch.isalnum() or ch.isspace())
    return "".join(word.capitalize() for word in clean.split())

def post_to_bluesky():
    # 1. Load latest curated issue
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

    # 3. Iterate over ALL books in the payload
    for featured_book in books:
        # Download & upload cover art
        image_blob = None
        cover_url = featured_book.get('cover')
        if cover_url:
            try:
                req = urllib.request.Request(cover_url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req) as response:
                    image_data = response.read()
                image_blob = client.upload_blob(image_data).blob
            except Exception as e:
                print(f"Could not process image for {featured_book.get('title')}: {e}")

        # Prepare metadata components
        genre = featured_book.get('genre', 'read')
        title = featured_book.get('title', 'Featured Title')
        raw_hook = featured_book.get('hook', '').strip().strip('"')
        website_display = "thecuratedspine.com"
        website_url = "https://thecuratedspine.com"

        # Build tags list
        tags = ["BookSky", "FreeKindleBooks"]
        
        custom_tags = featured_book.get('tags', [])
        if custom_tags and isinstance(custom_tags, list):
            for t in custom_tags[:1]:
                clean_tag = to_camel_case(t)
                if clean_tag and clean_tag not in tags:
                    tags.append(clean_tag)
        else:
            if 'Sci-Fi' in genre:
                tags.append("SciFi")
            elif 'Historical' in genre:
                tags.append("HistoricalFiction")
            elif 'Romance' in genre:
                tags.append("Romancelandia")
            elif 'Literature' in genre:
                tags.append("LiteraryFiction")

        author = featured_book.get('author')
        if author:
            author_tag = to_camel_case(author)
            if author_tag and len(author_tag) < 20:
                tags.append(author_tag)

        # Calculate fixed overhead to guarantee < 300 characters
        prefix = f"📚 Today's featured {genre} on The Curated Spine:\n{title}\n\n"
        link_section = f"Read free: {website_display}\n\n"
        tags_section = " ".join([f"#{t}" for t in tags])

        # Bluesky hard limit is 300; use 290 as a safety margin
        overhead_len = len(prefix) + len(link_section) + len(tags_section) + 4
        available_for_hook = 290 - overhead_len

        if available_for_hook > 20 and raw_hook:
            if len(raw_hook) > available_for_hook:
                hook_text = raw_hook[:available_for_hook - 3].rstrip() + "..."
            else:
                hook_text = raw_hook
            hook_section = f"\"{hook_text}\"\n\n"
        else:
            hook_section = ""

        # Construct Rich Text with clickable links and facets
        tb = client_utils.TextBuilder()
        tb.text(prefix)
        if hook_section:
            tb.text(hook_section)
        
        tb.text("Read free: ")
        tb.link(website_display, website_url)
        tb.text("\n\n")

        for i, tag in enumerate(tags):
            tb.tag(f"#{tag}", tag)
            if i < len(tags) - 1:
                tb.text(" ")

        # Attach cover embed if available
        post_embed = None
        if image_blob:
            post_embed = models.AppBskyEmbedImages.Main(
                images=[models.AppBskyEmbedImages.Image(alt=f"Cover of {title}", image=image_blob)]
            )

        # Send post
        try:
            client.send_post(tb, embed=post_embed)
            print(f"Successfully posted {title} to Bluesky!")
        except Exception as e:
            print(f"Failed to post {title}: {e}")

if __name__ == "__main__":
    post_to_bluesky()
