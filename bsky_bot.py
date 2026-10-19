import os
import json
import urllib.request
from atproto import Client, client_utils, models
import os
import json
import urllib.request
from atproto import Client, client_utils, models

def to_camel_case(text):
    # Removes spaces and capitalizes each word for hashtags (e.g., "Jane Doe" -> "#JaneDoe")
    return "".join(word.capitalize() for word in str(text).split())

def post_to_bluesky():
    # 1. Load today's curated books
    try:
        with open('latest_issue.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
            books = data.get('books', [])
            if not books:
                print("No books found for today.")
                return
            
            # Grab the top featured book from today's list
            featured_book = books[0]
    except Exception as e:
        print(f"Error loading issue data: {e}")
        return

    # 2. Log in securely
    client = Client()
    client.login(os.environ["BSKY_HANDLE"], os.environ["BSKY_PASSWORD"])

    # 3. Fetch the book cover image from your JSON data
    image_blob = None
    cover_url = featured_book.get('cover')
    if cover_url:
        try:
            req = urllib.request.Request(cover_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response:
                image_data = response.read()
            image_blob = client.upload_blob(image_data).blob
        except Exception as e:
            print(f"Could not process image: {e}")

    # 4. Build the rich text post (with a 300-character safety limit)
    text_builder = client_utils.TextBuilder()
    
    text_builder.text(f"📚 Today's featured {featured_book.get('genre', 'read')} on The Curated Spine:\n")
    text_builder.text(f"{featured_book.get('title', 'A Great New Book')}\n\n")
    
    # We truncate the hook to ensure we don't hit Bluesky's 300-char limit
    hook = featured_book.get('hook', '')
    if len(hook) > 100:
        hook = hook[:97] + "..."
    text_builder.text(f"\"{hook}\"\n\n")
    
    text_builder.text("Read it free today: ")
    text_builder.link("thecuratedspine.com", "https://thecuratedspine.com")
    text_builder.text("\n\n")

    # 5. Dynamic Hashtags
    text_builder.tag("#BookSky", "BookSky")
    text_builder.text(" ")
    text_builder.tag("#FreeKindleBooks", "FreeKindleBooks")
    text_builder.text(" ")
    
    # Automatic Author Hashtag (CamelCase)
    author_name = featured_book.get('author')
    if author_name:
        author_hashtag = to_camel_case(author_name)
        text_builder.tag(f"#{author_hashtag}", author_hashtag)
        text_builder.text(" ")

    # Dynamic Genre Hashtag
    genre = featured_book.get('genre', '')
    if 'Sci-Fi' in genre:
        text_builder.tag("#SciFi", "SciFi")
    elif 'Historical' in genre:
        text_builder.tag("#HistoricalFiction", "HistoricalFiction")
    elif 'Romance' in genre:
        text_builder.tag("#Romancelandia", "Romancelandia")
    elif 'Literature' in genre:
        text_builder.tag("#LiteraryFiction", "LiteraryFiction")

    # 6. Attach the image (if we successfully downloaded it)
    post_embed = None
    if image_blob:
        post_embed = models.AppBskyEmbedImages.Main(
            images=[models.AppBskyEmbedImages.Image(alt=featured_book.get('title', 'Book Cover'), image=image_blob)]
        )

    # 7. Publish the post
    client.send_post(text_builder, embed=post_embed)
    print(f"Successfully posted to Bluesky about {featured_book.get('title')} with cover art and tags!")

if __name__ == "__main__":
    post_to_bluesky()
def post_to_bluesky():
    # 1. Load today's curated books
    try:
        with open('latest_issue.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
            books = data.get('books', [])
            if not books:
                print("No books found for today.")
                return
            
            # Grab the top featured book from today's list
            featured_book = books[0]
    except Exception as e:
        print(f"Error loading issue data: {e}")
        return

    # 2. Log in securely
    client = Client()
    client.login(os.environ["BSKY_HANDLE"], os.environ["BSKY_PASSWORD"])

    # 3. Fetch the book cover image from your JSON data
    image_blob = None
    cover_url = featured_book.get('cover')
    if cover_url:
        try:
            # Download the image into memory
            req = urllib.request.Request(cover_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response:
                image_data = response.read()
            
            # Upload the image to Bluesky's servers
            image_blob = client.upload_blob(image_data).blob
        except Exception as e:
            print(f"Could not process image: {e}")

    # 4. Build the rich text post
    text_builder = client_utils.TextBuilder()
    
    text_builder.text(f"📚 Today's featured {featured_book.get('genre', 'read')} on The Curated Spine:\n")
    text_builder.text(f"{featured_book.get('title', 'A Great New Book')}\n\n")
    
    text_builder.text(f"\"{featured_book.get('hook', '')}\"\n\n")
    
    text_builder.text("Read it free in today's dispatch: ")
    text_builder.link("thecuratedspine.com", "https://thecuratedspine.com")
    text_builder.text("\n\n")

    text_builder.tag("#Booksky", "Booksky")
    text_builder.text(" ")
    text_builder.tag("#FreeKindleBooks", "FreeKindleBooks")
    text_builder.text(" ")
    text_builder.tag("#AmReading", "AmReading")

    # 5. Attach the image (if we successfully downloaded it)
    post_embed = None
    if image_blob:
        post_embed = models.AppBskyEmbedImages.Main(
            images=[models.AppBskyEmbedImages.Image(alt=featured_book.get('title', 'Book Cover'), image=image_blob)]
        )

    # 6. Publish the post with the embedded image
    client.send_post(text_builder, embed=post_embed)
    print(f"Successfully posted to Bluesky about {featured_book.get('title')} with cover art!")

if __name__ == "__main__":
    post_to_bluesky()
