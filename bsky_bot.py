import os
import json
from atproto import Client, client_utils

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

    # 3. Build the rich text post
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

    # 4. Publish
    client.send_post(text_builder)
    print(f"Successfully posted to Bluesky about {featured_book.get('title')}!")

if __name__ == "__main__":
    post_to_bluesky()
