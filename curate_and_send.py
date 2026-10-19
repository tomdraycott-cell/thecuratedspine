import os
import json
import urllib.parse
import random
from playwright.sync_api import sync_playwright
import google.generativeai as genai
import requests

# Configure Gemini AI
genai.configure(api_key=os.environ["GEMINI_API_KEY"])
model = genai.GenerativeModel('gemini-1.5-flash')

CATEGORIES = [
    {"name": "Science Fiction", "url": "https://www.amazon.com/Best-Sellers-Kindle-Store-Science-Fiction/zgbs/digital-text/158566011"},
    {"name": "Historical Fiction", "url": "https://www.amazon.com/Best-Sellers-Kindle-Store-Historical-Fiction/zgbs/digital-text/158566011"},
    {"name": "Literary Fiction", "url": "https://www.amazon.com/Best-Sellers-Kindle-Store-Literary-Fiction/zgbs/digital-text/158566011"},
    {"name": "Romance", "url": "https://www.amazon.com/Best-Sellers-Kindle-Store-Romance/zgbs/digital-text/158566011"}
]

def generate_hook_and_filter(title, author, genre):
    """Uses Gemini to filter off-brand books and write a custom hook."""
    prompt = f"""
    You are an editorial assistant for a premium book curation newsletter called 'The Curated Spine'.
    Evaluate this book: "{title}" by {author}. Genre: {genre}.
    
    1. If the title sounds like cheap erotica, low-quality spam, or a wildly off-brand subgenre, reply with exactly: REJECT
    2. If it is a quality book, write a compelling, one-sentence editorial hook (under 95 characters) to entice readers. Do not use quotes.
    """
    try:
        response = model.generate_content(prompt)
        result = response.text.strip()
        if "REJECT" in result.upper():
            return None
        return result
    except Exception as e:
        print(f"Gemini API error: {e}")
        return "An exciting new read to add to your digital library."

def fetch_kindle_books(page, category_url):
    """Scrapes Amazon for the top free Kindle books in a category."""
    page.goto(category_url, wait_until="domcontentloaded")
    books = []
    
    try:
        page.wait_for_selector(".zg-grid-general-faceout", timeout=5000)
        items = page.locator(".zg-grid-general-faceout").all()
        
        for item in items[:15]:  # Pull top 15 to allow for AI filtering
            try:
                title = item.locator("div._cDEzb_p13n-sc-css-line-clamp-1_1Fn1y").nth(0).inner_text()
                author = item.locator("div._cDEzb_p13n-sc-css-line-clamp-1_1Fn1y").nth(1).inner_text()
                cover = item.locator("img").get_attribute("src")
                link = item.locator("a.a-link-normal").nth(0).get_attribute("href")
                
                if title and link:
                    books.append({
                        "title": title,
                        "author": author,
                        "cover": cover,
                        "amazon_url": f"https://www.amazon.com{link}"
                    })
            except Exception:
                continue
    except Exception as e:
        print(f"Amazon scraping error: {e}")
        
    return books

def fetch_free_audiobook(page, genre_keyword):
    """Scrapes Kobo for a free audiobook and generates cross-platform links."""
    print(f"   -> Searching Kobo for free {genre_keyword} audiobooks...")
    
    # URL encoded search for free audiobooks
    query = urllib.parse.quote(genre_keyword)
    kobo_url = f"https://www.kobo.com/us/en/search?query={query}&fcmedia=AudioBook&fclanguages=en&fcprice=Free"
    
    page.goto(kobo_url, wait_until="domcontentloaded")
    
    try:
        page.wait_for_selector(".item-detail", timeout=5000)
        items = page.locator(".item-detail").all()
        
        if not items:
            return None
            
        # Pick a random book from the top 5 free results to keep the newsletter fresh
        item = random.choice(items[:5])
        
        title = item.locator(".title").inner_text().strip()
        author = item.locator(".contributor-name").inner_text().strip()
        cover = item.locator(".item-image img").get_attribute("src")
        link = item.locator(".title").get_attribute("href")
        
        search_term = urllib.parse.quote(f"{title} {author}")
        
        return {
            "title": title,
            "author": author,
            "cover": cover.replace("180/180/False", "300/300/False") if cover else "", # Attempt to get higher res square
            "kobo_url": f"https://www.kobo.com{link}",
            "spotify_url": f"https://open.spotify.com/search/{search_term}/audiobooks",
            "audible_url": f"https://www.audible.com/search?keywords={search_term}"
        }
    except Exception as e:
        print(f"   Audiobook scraping error: {e}")
        return None

def dispatch_mailerlite(campaign_html):
    """Creates and sends the MailerLite campaign."""
    url = "https://connect.mailerlite.com/api/campaigns"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {os.environ['MAILERLITE_API_KEY']}"
    }
    
    payload = {
        "type": "regular",
        "emails": [{"subject": "📚 Your Daily Curated Spine: Free Kindle & Audiobooks", "from_name": "The Curated Spine", "from": os.environ['MAILERLITE_SENDER_EMAIL']}],
        "groups": ["INSERT_YOUR_GROUP_ID_HERE"] # Ensure your active subscriber group ID is here
    }
    
    # 1. Create Campaign
    response = requests.post(url, json=payload, headers=headers)
    if response.status_code not in [200, 201]:
        print(f"Failed to create campaign: {response.text}")
        return
        
    campaign_id = response.json()["data"]["id"]
    
    # 2. Add Content
    content_payload = {"html": campaign_html, "plain_text": "Please view this email in an HTML compatible client."}
    requests.put(f"{url}/{campaign_id}/content", json=content_payload, headers=headers)
    
    # 3. Schedule/Send
    schedule_payload = {"delivery": "instant"}
    requests.post(f"{url}/{campaign_id}/schedule", json=schedule_payload, headers=headers)
    print(f"SUCCESS: MailerLite campaign dispatched! ID: {campaign_id}")

def main():
    today_picks = []
    
    with sync_playwright() as p:
        # Use stealth browser to avoid bot detection
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        
        for category in CATEGORIES:
            print(f"-> Fetching {category['name']}...")
            raw_books = fetch_kindle_books(page, category['url'])
            
            selected_kindle = None
            for book in raw_books:
                hook = generate_hook_and_filter(book['title'], book['author'], category['name'])
                if hook:
                    book['hook'] = hook
                    book['genre'] = category['name']
                    selected_kindle = book
                    break
            
            if selected_kindle:
                audio_bonus = fetch_free_audiobook(page, category['name'])
                selected_kindle['audiobook'] = audio_bonus
                today_picks.append(selected_kindle)
                
        browser.close()

    # Save to JSON for Website & Bluesky Bot
    with open('latest_issue.json', 'w', encoding='utf-8') as f:
        json.dump({"books": today_picks}, f, indent=4)

    # Build HTML Email
    html_content = """
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: auto; padding: 20px;">
        <h1 style="text-align: center; color: #2c3e50;">The Curated Spine</h1>
        <p style="text-align: center; color: #7f8c8d;">Top-quality free & discounted books in your inbox.</p>
        <hr style="border: 0; height: 1px; background: #eee; margin-bottom: 30px;">
    """
    
    for pick in today_picks:
        html_content += f"""
        <div style="margin-bottom: 40px;">
            <h2 style="color: #34495e; margin-bottom: 5px;">{pick['genre']}</h2>
            <div style="display: flex; gap: 20px; align-items: flex-start;">
                <img src="{pick['cover']}" width="120" style="border-radius: 4px; box-shadow: 0 4px 8px rgba(0,0,0,0.1);">
                <div>
                    <h3 style="margin: 0; color: #2980b9;">{pick['title']}</h3>
                    <p style="margin: 5px 0; color: #555;">by {pick['author']}</p>
                    <p style="font-style: italic; color: #666;">"{pick['hook']}"</p>
                    <a href="{pick['amazon_url']}" style="display: inline-block; background: #2980b9; color: white; padding: 8px 16px; text-decoration: none; border-radius: 4px; font-weight: bold;">Get it on Kindle</a>
                </div>
            </div>
        """
        
        if pick.get('audiobook'):
            audio = pick['audiobook']
            html_content += f"""
            <!-- AUDIOBOOK BONUS SECTION -->
            <div style="margin-top: 20px; padding-top: 15px; border-top: 1px solid #eeeeee;">
              <table width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td width="80" valign="top">
                    <img src="{audio['cover']}" width="80" height="80" style="border-radius: 4px; display: block;" alt="Audiobook Cover">
                  </td>
                  <td valign="middle" style="padding-left: 15px; font-family: Arial, sans-serif;">
                    <p style="margin: 0; font-size: 12px; color: #888888; text-transform: uppercase;">🎧 Audio Bonus</p>
                    <p style="margin: 4px 0; font-size: 14px; font-weight: bold; color: #333333;">{audio['title']}</p>
                    <p style="margin: 0; font-size: 13px;">
                      <a href="{audio['kobo_url']}" style="color: #000000; text-decoration: none; font-weight: bold;">Kobo (Free)</a> • 
                      <a href="{audio['spotify_url']}" style="color: #1DB954; text-decoration: none;">Spotify (Premium)</a> • 
                      <a href="{audio['audible_url']}" style="color: #F5A623; text-decoration: none;">Audible</a>
                    </p>
                  </td>
                </tr>
              </table>
            </div>
            """
        html_content += "</div>"
        
    html_content += """
        <hr style="border: 0; height: 1px; background: #eee; margin-top: 40px;">
        <p style="text-align: center; font-size: 12px; color: #999;">You received this because you subscribed at thecuratedspine.com</p>
    </div>
    """
    
    dispatch_mailerlite(html_content)

if __name__ == "__main__":
    main()
