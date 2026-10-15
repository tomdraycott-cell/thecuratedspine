import os
import json
import smtplib
import urllib.request
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright
from playwright_stealth import stealth_sync

HISTORY_FILE = "sent_history.json"
LATEST_ISSUE_FILE = "latest_issue.json"
DAYS_TO_REMEMBER = 30

# Map each Amazon scrape category to its parent Genre on the website/MailerLite
GENRES = {
    "Science Fiction & Fantasy": {
        "url": "https://www.amazon.com/Best-Sellers-Kindle-Store-Science-Fiction-Fantasy/zgbs/digital-text/158591011?tf=1",
        "base_genre": "Sci-Fi",
        "ml_group_id": "201220938717463907"
    },
    "Hot New Sci-Fi Releases": {
        "url": "https://www.amazon.com/gp/new-releases/digital-text/158591011",
        "base_genre": "Sci-Fi",
        "ml_group_id": "201220938717463907"
    },
    "Historical Fiction": {
        "url": "https://www.amazon.com/Best-Sellers-Kindle-Store-Historical-Fiction/zgbs/digital-text/157059011?tf=1",
        "base_genre": "Historical Fiction",
        "ml_group_id": "201220954450298498"
    },
    "Hot New Historical Fiction": {
        "url": "https://www.amazon.com/gp/new-releases/digital-text/157059011",
        "base_genre": "Historical Fiction",
        "ml_group_id": "201220954450298498"
    },
    "Literature & Fiction": {
        "url": "https://www.amazon.com/Best-Sellers-Kindle-Store-Literature-Fiction/zgbs/digital-text/157028011?tf=1",
        "base_genre": "Literary Fiction",
        "ml_group_id": "201220964201006584"
    },
    "Hot New Lit & Fiction": {
        "url": "https://www.amazon.com/gp/new-releases/digital-text/157028011",
        "base_genre": "Literary Fiction",
        "ml_group_id": "201220964201006584"
    },
    "Romance": {
        "url": "https://www.amazon.com/Best-Sellers-Kindle-Store-Romance/zgbs/digital-text/158566011?tf=1",
        "base_genre": "Romance",
        "ml_group_id": "201220945613948200"
    },
    "Hot New Romance": {
        "url": "https://www.amazon.com/gp/new-releases/digital-text/158566011",
        "base_genre": "Romance",
        "ml_group_id": "201220945613948200"
    },
}


def load_and_clean_history():
    if not os.path.exists(HISTORY_FILE):
        return {}
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            history = json.load(f)
    except Exception:
        return {}

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=DAYS_TO_REMEMBER)

    cleaned = {}
    for link, date_str in history.items():
        try:
            sent_date = datetime.fromisoformat(date_str)
            if sent_date > cutoff:
                cleaned[link] = date_str
        except ValueError:
            continue
    return cleaned


def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)


def vet_and_summarize_with_gemini(candidates, genre_name):
    """
    Uses the Gemini API to:
    1. Filter out disguised romance/erotica (like 'My Human Pet') from non-Romance genres.
    2. Write a 1-sentence 'Literary Sommelier' hook for each approved book.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key or not candidates:
        # Fallback if GEMINI_API_KEY isn't set yet
        for c in candidates:
            c["hook"] = "An acclaimed selection hand-picked from today's top Kindle deals."
        return candidates[:3]

    allow_romance = "Romance" in genre_name
    books_payload = [
        {"index": i, "title": c["title"], "author_info": c.get("author", ""), "category": genre_name}
        for i, c in enumerate(candidates)
    ]

    prompt = f"""
    You are the editorial curator for 'The Curated Spine', an upmarket literary daily book newsletter.
    Review these candidate books for the category '{genre_name}'.
    Allow Romance in this category: {allow_romance}.

    Tasks:
    1. If Allow Romance is False, set "keep": false for any book that appears to be steamy romance, erotica, harems, alpha/billionaire/alien/monster romance tropes (e.g. 'My Human Pet'), or low-effort spam disguised in this category.
    2. For every kept book, write a 1-sentence (max 24 words) intriguing, warm, independent-bookshop style "hook" based on the title and genre. Never use cheesy words like 'page-turner', 'thrilling', or 'delve'.

    Respond ONLY with a valid JSON array of objects in this exact format:
    [
      {{"index": 0, "keep": true, "hook": "A taut speculative mystery exploring humanity's first quiet contact with the unknown."}}
    ]

    Candidate Books:
    {json.dumps(books_payload)}
    """

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    req_body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"}
    }

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(req_body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            raw_text = result["candidates"][0]["content"]["parts"][0]["text"]
            ai_decisions = json.loads(raw_text)

        vetted = []
        decision_map = {item["index"]: item for item in ai_decisions if isinstance(item, dict)}
        for idx, c in enumerate(candidates):
            decision = decision_map.get(idx, {"keep": True, "hook": ""})
            # Always keep priority authors!
            if c.get("is_priority_author") or decision.get("keep", True):
                c["hook"] = decision.get("hook") or "A standout deal climbing the Kindle charts today."
                vetted.append(c)
            else:
                print(f"   [AI Bouncer] Filtered out disguised romance/spam: {c['title']}")

        return vetted[:3]
    except Exception as e:
        print(f"   [AI Warning] Gemini check skipped ({e}). Using top mathematical picks.")
        for c in candidates:
            c["hook"] = "Selected from today's highest-rated Kindle promotions."
        return candidates[:3]


def scrape_genre(page, genre_name, url, sent_links):
    print(f"\n-> Fetching {genre_name}...")
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)
    except Exception as e:
        print(f"   [Error] Failed to load {url}: {e}")
        return []

    page_title = page.title()
    print(f"   Page Title: '{page_title}' | Status: {response.status if response else 'Unknown'}")

    if "robot check" in page_title.lower() or "captcha" in page_title.lower():
        print("   [BLOCKED] Amazon presented a CAPTCHA / Robot Check.")
        return []

    page.mouse.wheel(0, 1000)
    page.wait_for_timeout(2000)

    book_cards = page.query_selector_all("div#gridItemRoot")
    print(f"   Raw product cards detected: {len(book_cards)}")

    candidates = []
    is_new_release = "Hot New" in genre_name

    for card in book_cards:
        try:
            title = ""
            title_elem = card.query_selector("div[class*='line-clamp'], span.a-size-medium, div.p13n-sc-truncate")
            if title_elem:
                title = title_elem.inner_text().strip()

            img_elem = card.query_selector("img")
            img_url = ""
            if img_elem:
                img_url = img_elem.get_attribute("src") or ""
                if not title:
                    title = img_elem.get_attribute("alt") or ""

            title = title.strip()

            # Fast Keyword Quarantine before AI check
            if "Romance" not in genre_name and any(
                kw in title.lower() for kw in ["romance", "harem", "billionaire", "alpha's", "human pet", "reverse harem"]
            ):
                continue

            link_elem = card.query_selector("a.a-link-normal")
            price_elem = card.query_selector("span._cDEzb_p13n-sc-price_3mJ9Z, span.p13n-sc-price")
            rating_elem = card.query_selector("span.a-icon-alt")
            reviews_elem = card.query_selector("span.a-size-small")

            if not (title and link_elem and price_elem):
                continue

            if not is_new_release and not (rating_elem and reviews_elem):
                continue

            href = link_elem.get_attribute("href") or ""
            link = "https://www.amazon.com" + href.split("/ref=")[0] if href.startswith("/") else href

            if link in sent_links:
                continue

            price = price_elem.inner_text().strip()

            try:
                clean_price_str = ''.join(c for c in price if c.isdigit() or c == '.')
                if clean_price_str:
                    if float(clean_price_str) > 2.99:
                        continue
                elif "free" not in price.lower():
                    continue
            except Exception:
                continue

            rating = 0.0
            reviews = 0
            if rating_elem and reviews_elem:
                rating_text = rating_elem.inner_text().strip()
                if rating_text:
                    try:
                        rating = float(rating_text.split()[0])
                        reviews = int(reviews_elem.inner_text().replace(",", "").strip())
                    except Exception:
                        pass

            if is_new_release:
                score = 1000 - len(candidates)
            else:
                score = (reviews / (reviews + 50)) * rating + (50 / (reviews + 50)) * 4.2

            card_text = card.inner_text().lower()
            is_priority = False
            if "tom draycott" in card_text or "livia huntington jones" in card_text:
                score += 10000
                is_priority = True

            candidates.append({
                "title": title,
                "link": link,
                "image": img_url,
                "rating": rating,
                "reviews": reviews,
                "price": price,
                "score": score,
                "is_priority_author": is_priority
            })
        except Exception:
            continue

    print(f"   Parsed candidates (after deduplication): {len(candidates)}")
    candidates.sort(key=lambda b: b["score"], reverse=True)

    # Pass top 8 candidates to Gemini so we still have 3 left if it filters out disguised romance
    return vet_and_summarize_with_gemini(candidates[:8], genre_name)


def build_bohemian_email_html(recommendations):
    """Generates the Dark Academia / Warm Parchment HTML email with book covers and hooks."""
    date_str = datetime.now().strftime('%A, %d %B %Y')
    html = f"""
    <!DOCTYPE html>
    <html>
    <body style="margin:0; padding:0; background-color:#1A1715; font-family:'Georgia', serif; color:#2C2725;">
      <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#1A1715; padding: 30px 12px;">
        <tr>
          <td align="center">
            <table width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width: 600px; background-color:#FAF7F2; border-radius: 12px; overflow: hidden; border: 1px solid #CBBAB3;">
              
              <!-- Header -->
              <tr>
                <td style="background-color:#231E1B; padding: 32px 24px; text-align: center; border-bottom: 2px solid #8B3A2B;">
                  <p style="margin: 0 0 6px 0; font-size: 11px; letter-spacing: 2px; text-transform: uppercase; color: #CBBAB3;">{date_str}</p>
                  <h1 style="margin: 0; font-size: 30px; color: #FAF7F2; font-weight: normal; font-family: 'Georgia', serif;">The Curated Spine</h1>
                  <p style="margin: 8px 0 0 0; font-size: 14px; color: #EBE5DF; font-style: italic;">Your morning shelf of hand-picked Kindle deals</p>
                </td>
              </tr>

              <!-- Body Content -->
              <tr>
                <td style="padding: 24px 24px 12px 24px;">
    """

    for genre, books in recommendations.items():
        html += f"""
                  <h2 style="font-size: 19px; color: #1A1715; border-bottom: 1px solid #D8CFC8; padding-bottom: 8px; margin-top: 20px; margin-bottom: 16px; font-family: 'Georgia', serif;">
                    {genre}
                  </h2>
        """
        for b in books:
            rating_display = f"<span style='color:#B45309;'>★ {b['rating']}</span> ({b['reviews']:,} ratings)" if b['reviews'] > 0 else "<span style='color:#8B3A2B; font-style:italic;'>★ Hot New Release</span>"
            cover_html = ""
            if b.get("image"):
                cover_html = f"""
                <td width="75" valign="top" style="padding-right: 16px;">
                  <a href="{b['link']}" style="text-decoration:none;">
                    <img src="{b['image']}" alt="Cover" width="75" style="width:75px; border-radius:4px; border:1px solid #D8CFC8; display:block;" />
                  </a>
                </td>
                """

            html += f"""
                  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#FFFFFF; border: 1px solid #EBE5DF; border-radius: 8px; padding: 14px; margin-bottom: 14px;">
                    <tr>
                      {cover_html}
                      <td valign="top">
                        <a href="{b['link']}" style="font-size: 16px; font-weight: bold; color: #8B3A2B; text-decoration: none; line-height: 1.3;">
                          {b['title']}
                        </a>
                        <div style="font-size: 13px; color: #5A524D; margin: 5px 0;">
                          <strong style="color: #1A1715; background-color: #F3EDE4; padding: 2px 6px; border-radius: 4px;">{b['price']}</strong>
                          &nbsp;&bull;&nbsp; {rating_display}
                        </div>
                        <p style="font-size: 13.5px; color: #3D3633; font-style: italic; margin: 6px 0 10px 0; line-height: 1.45;">
                          "{b.get('hook', '')}"
                        </p>
                        <a href="{b['link']}" style="display:inline-block; font-size: 12px; font-weight: bold; color: #FFFFFF; background-color: #8B3A2B; padding: 6px 12px; border-radius: 4px; text-decoration: none;">
                          View on Amazon &rarr;
                        </a>
                      </td>
                    </tr>
                  </table>
            """

    html += """
                </td>
              </tr>

              <!-- Footer -->
              <tr>
                <td style="background-color:#F3EDE4; padding: 20px 24px; text-align: center; border-top: 1px solid #EBE5DF; font-size: 12px; color: #786E68;">
                  <p style="margin: 0 0 6px 0;">Prices and availability are accurate as of 06:20 UTC and may change quickly on Amazon.</p>
                  <p style="margin: 0;">&copy; 2026 The Curated Spine &bull; <a href="https://thecuratedspine.com" style="color:#8B3A2B;">thecuratedspine.com</a></p>
                </td>
              </tr>

            </table>
          </td>
        </tr>
      </table>
    </body>
    </html>
    """
    return html


def save_latest_issue_for_website(recommendations):
    """Exports a clean latest_issue.json so index.html automatically shows today's top picks."""
    featured = []
    for genre_label, books in recommendations.items():
        base_genre = GENRES.get(genre_label, {}).get("base_genre", genre_label)
        for i, b in enumerate(books):
            if i == 0 and len(featured) < 4:  # Take the #1 pick from each main category for the homepage
                featured.append({
                    "genre": base_genre,
                    "title": b["title"],
                    "author": "Featured Daily Pick",
                    "cover": b.get("image") or "https://images.unsplash.com/photo-1544947950-fa07a98d237f?q=80&w=300&auto=format&fit=crop",
                    "price_uk": "FREE" if "0.00" in b["price"] else "£0.99",
                    "price_us": "FREE" if "0.00" in b["price"] else b["price"],
                    "rating": b["rating"] if b["rating"] > 0 else "New",
                    "reviews": f"{b['reviews']:,}" if b["reviews"] > 0 else "New Release",
                    "ctr_badge": f"★ #1 in {base_genre} Today",
                    "hook": b.get("hook", ""),
                    "link_uk": b["link"].replace("amazon.com", "amazon.co.uk"),
                    "link_us": b["link"]
                })
    with open(LATEST_ISSUE_FILE, "w", encoding="utf-8") as f:
        json.dump({"updated_at": datetime.now(timezone.utc).isoformat(), "books": featured[:4]}, f, indent=2)
    print(f"-> Saved {len(featured[:4])} showcase books to {LATEST_ISSUE_FILE} for the website!")


def run_bot():
    history = load_and_clean_history()
    recommendations = {}
    newly_recommended = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox"
            ]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            locale="en-US",
            timezone_id="America/New_York",
            viewport={"width": 1366, "height": 768},
        )
        page = context.new_page()
        stealth_sync(page)

        for genre, meta in GENRES.items():
            top_picks = scrape_genre(page, genre, meta["url"], set(history.keys()))
            if top_picks:
                recommendations[genre] = top_picks
                for b in top_picks:
                    newly_recommended.append(b["link"])
            page.wait_for_timeout(2000)

        browser.close()

    if not recommendations:
        print("\n-> No new book deals found today. Exiting.")
        save_history(history)
        return

    today_iso = datetime.now(timezone.utc).isoformat()
    for link in newly_recommended:
        history[link] = today_iso
    save_history(history)

    # 1. Save JSON for the website's "Recent Reader Favourites" shelf
    save_latest_issue_for_website(recommendations)

    # 2. Build the Bohemian HTML Email
    html = build_bohemian_email_html(recommendations)

    # 3. Send via Gmail (for your personal test copy)
    sender = os.environ.get("EMAIL_SENDER")
    password = os.environ.get("EMAIL_PASSWORD")
    receiver = os.environ.get("EMAIL_RECEIVER")

    if sender and password and receiver:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"The Curated Spine — {datetime.now().strftime('%d %b %Y')}"
        msg["From"] = f"The Curated Spine <{sender}>"
        msg["To"] = receiver
        msg.attach(MIMEText(html, "html"))

        try:
            print("-> Connecting to SMTP on Port 587...")
            server = smtplib.SMTP("smtp.gmail.com", 587)
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(sender, password)
            server.send_message(msg)
            server.quit()
            print("-> Personal test email sent successfully!")
        except Exception as e:
            print(f"-> Email dispatch failed: {e}")


if __name__ == "__main__":
    run_bot()
