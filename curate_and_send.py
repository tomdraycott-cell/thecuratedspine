import os
import re
import json
import smtplib
import urllib.request
import urllib.error
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright
from playwright_stealth import stealth_sync

HISTORY_FILE = "sent_history.json"
LATEST_ISSUE_FILE = "latest_issue.json"
DAYS_TO_REMEMBER = 30
AFFILIATE_TAG = os.environ.get("AMAZON_AFFILIATE_TAG", "curatedspine-20")
MIN_REVIEWS_BESTSELLERS = 50

# House Priority Authors (+10,000 score boost & bypass minimum review filters)
PRIORITY_AUTHORS = [
    "tom draycott",
    "livia huntingdon-jones",
    "livia huntingdon jones"
]

# Acclaimed & Upmarket Authors Whitelist (+500 score boost when discounted under $2.99)
CURATED_AUTHORS = [
    # Literary & Upmarket Fiction
    "kazuo ishiguro", "margaret atwood", "hilary mantel", "ian mcewan", "zadie smith",
    "cormac mccarthy", "donna tartt", "ann patchett", "amor towles", "anthony doerr",
    "celeste ng", "colson whitehead", "maggie o'farrell", "barbara kingsolver",
    "richard powers", "elizabeth strout", "sally rooney", "david mitchell",
    "hanya yanagihara", "jhumpa lahiri", "chimamanda ngozi adichie", "salman rushdie",
    "julian barnes", "kate atkinson", "william boyd", "sebastian faulks",
    # Historical Fiction
    "ken follett", "bernard cornwell", "philippa gregory", "robert harris", "c. j. sansom",
    "cj sansom", "conn iggulden", "kristin hannah", "paula mclain", "geraldine brooks",
    "sarah waters", "kate quinn", "martha hall kelly", "ariel lawhon", "madeline miller",
    "jennifer saint", "elodie harper", "stacy halls", "jessie burton", "edward rutherfurd",
    # Science Fiction & Speculative
    "adrian tchaikovsky", "andy weir", "blake crouch", "ted chiang", "liu cixin",
    "cixin liu", "ursula k. le guin", "octavia e. butler", "william gibson",
    "neal stephenson", "kim stanley robinson", "dan simmons", "alastair reynolds",
    "iain m. banks", "peter f. hamilton", "john scalzi", "martha wells", "arkady martine",
    "ann leckie", "jeff vandermeer", "emily st. john mandel", "susanna clarke",
    "n. k. jemisin", "nk jemisin", "robin hobb", "guy gavriel kay", "lois mcmaster bujold",
    # Upmarket & Acclaimed Romance
    "emily henry", "beth o'leary", "mhairi mcfarlane", "abby jimenez", "talia hibbert",
    "casey mcquiston", "helen hoang", "jasmine guillory", "aliazalea", "carley fortune",
    "katherine center", "lisa kleypas", "julia quinn", "tessa dare", "courtney milan",
    "evie dunmore", "cat sebastian", "kennedy ryan", "tia williams"
]

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
    "Literary Fiction": {
        "url": "https://www.amazon.com/Best-Sellers-Kindle-Store-Literary-Fiction/zgbs/digital-text/157053011?tf=1",
        "base_genre": "Literary Fiction",
        "ml_group_id": "201220964201006584"
    },
    "Hot New Literary Fiction": {
        "url": "https://www.amazon.com/gp/new-releases/digital-text/157053011",
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


def format_store_links(raw_url):
    """Strips bloated Amazon URLs down to the fast /dp/ASIN format and attaches curatedspine-20."""
    match = re.search(r"/dp/([A-Z0-9]{10})", raw_url)
    tag_suffix = f"?tag={AFFILIATE_TAG}" if AFFILIATE_TAG else ""

    if match:
        asin = match.group(1)
        us_url = f"https://www.amazon.com/dp/{asin}{tag_suffix}"
        uk_url = f"https://www.amazon.co.uk/dp/{asin}{tag_suffix}"
        return us_url, uk_url

    clean_us = raw_url.split("/ref=")[0]
    clean_uk = clean_us.replace("amazon.com", "amazon.co.uk")
    if tag_suffix and "?" not in clean_us:
        clean_us += tag_suffix
        clean_uk += tag_suffix
    return clean_us, clean_uk


def get_high_res_vertical_cover(img_url):
    """Strips Amazon's horizontal letterbox padding (._AC_UL...SR...) and requests a crisp 300px vertical cover."""
    if not img_url:
        return ""
    return re.sub(r'\._[^.]+_\.', '._SL300_.', img_url)


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
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key or not candidates:
        for c in candidates:
            c["hook"] = "An acclaimed selection hand-picked from today's top Kindle deals."
        return candidates[:3]

    allow_romance = "Romance" in genre_name
    books_payload = [
        {"index": i, "title": c["title"], "category": genre_name}
        for i, c in enumerate(candidates)
    ]

    prompt = f"""
    You are the strict literary editor for 'The Curated Spine', an upmarket, Dark Academia daily book newsletter.
    Review these candidate books for the category '{genre_name}'.
    Allow Romance in this category: {allow_romance}.

    Tasks:
    1. STRICT GENRE & QUALITY VETTING:
       - If Allow Romance is False, set "keep": false for any book that appears to be steamy romance, erotica, harems, alpha/billionaire/alien/monster romance tropes (e.g. 'My Human Pet'), or low-effort spam.
       - If the category contains 'Literary Fiction', set "keep": false for supernatural thrillers, generic crime/mystery pulp series ('Book 1', 'A Thriller'), horror, or low-brow commercial fluff. Only keep books that sound like genuine, character-driven, book-club or literary fiction.
       - If the category contains 'Historical Fiction', set "keep": false for supernatural/paranormal/time-travel mashups (e.g. 'A Da Vinci Supernatural Thriller'), fantasy, or Amish/inspirational pulp. Keep only authentic historical fiction.
    2. For every kept book, write a 1-sentence (max 24 words) intelligent, warm, independent-bookshop style "hook" based on the title and genre. Never use clichés like 'page-turner', 'thrilling', 'captivating', 'embark', 'unravel', or 'delve'.

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
            if c.get("is_priority_author") or decision.get("keep", True):
                c["hook"] = decision.get("hook") or "A standout deal climbing the Kindle charts today."
                vetted.append(c)
            else:
                print(f"   [AI Literary Editor] Filtered out off-brand title: {c['title']}")

        # Fallback if the AI editor rejected too many and left 0 picks
        if not vetted and candidates:
            fallback = candidates[0]
            fallback["hook"] = "Selected from today's highest-rated Kindle promotions."
            vetted = [fallback]

        print(f"   [AI Curated] Kept {len(vetted[:3])} books with custom hooks.")
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
                raw_img = img_elem.get_attribute("src") or ""
                img_url = get_high_res_vertical_cover(raw_img)
                if not title:
                    title = img_elem.get_attribute("alt") or ""

            title = title.strip()
            title_lower = title.lower()

            if "Romance" not in genre_name and any(
                kw in title_lower for kw in [
                    "romance", "harem", "billionaire", "alpha's", "human pet",
                    "reverse harem", "litrpg", "supernatural thriller", "paranormal"
                ]
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
            raw_link = "https://www.amazon.com" + href.split("/ref=")[0] if href.startswith("/") else href

            # Normalize link by ASIN for history deduplication
            asin_match = re.search(r"/dp/([A-Z0-9]{10})", raw_link)
            canonical_link = f"https://www.amazon.com/dp/{asin_match.group(1)}" if asin_match else raw_link

            if canonical_link in sent_links or raw_link in sent_links:
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

            card_text = card.inner_text().lower()
            is_priority = any(author in card_text for author in PRIORITY_AUTHORS)
            is_curated_vip = any(author in card_text for author in CURATED_AUTHORS)

            # Enforce minimum review threshold on Best Seller charts unless Priority or VIP author
            if not is_new_release and not is_priority and not is_curated_vip:
                if reviews < MIN_REVIEWS_BESTSELLERS or rating < 4.1:
                    continue

            if is_new_release:
                score = 1000 - len(candidates)
            else:
                score = (reviews / (reviews + 50)) * rating + (50 / (reviews + 50)) * 4.2

            if is_priority:
                score += 10000
                print(f"   [Priority Author Match!] Boosted: {title}")
            elif is_curated_vip:
                score += 500
                print(f"   [Curated Whitelist Match!] Boosted: {title}")

            candidates.append({
                "title": title,
                "link": canonical_link,
                "image": img_url,
                "rating": rating,
                "reviews": reviews,
                "price": price,
                "score": score,
                "is_priority_author": is_priority
            })
        except Exception:
            continue

    print(f"   Parsed candidates (after deduplication & quality floor): {len(candidates)}")
    candidates.sort(key=lambda b: b["score"], reverse=True)
    return vet_and_summarize_with_gemini(candidates[:10], genre_name)


def build_bohemian_email_html(recommendations):
    date_str = datetime.now().strftime('%A, %d %B %Y')
    html = f"""
    <!DOCTYPE html>
    <html>
    <body style="margin:0; padding:0; background-color:#1A1715; font-family:'Georgia', serif; color:#2C2725;">
      <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#1A1715; padding: 30px 12px;">
        <tr>
          <td align="center">
            <table width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width: 600px; background-color:#FAF7F2; border-radius: 12px; overflow: hidden; border: 1px solid #CBBAB3;">
              <tr>
                <td style="background-color:#231E1B; padding: 32px 24px; text-align: center; border-bottom: 2px solid #8B3A2B;">
                  <p style="margin: 0 0 6px 0; font-size: 11px; letter-spacing: 2px; text-transform: uppercase; color: #CBBAB3;">{date_str}</p>
                  <h1 style="margin: 0; font-size: 30px; color: #FAF7F2; font-weight: normal; font-family: 'Georgia', serif;">The Curated Spine</h1>
                  <p style="margin: 8px 0 0 0; font-size: 14px; color: #EBE5DF; font-style: italic;">Your morning shelf of hand-picked Kindle deals</p>
                </td>
              </tr>
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
            us_link, uk_link = format_store_links(b['link'])
            rating_display = f"<span style='color:#B45309;'>★ {b['rating']}</span> ({b['reviews']:,} ratings)" if b['reviews'] > 0 else "<span style='color:#8B3A2B; font-style:italic;'>★ Hot New Release</span>"
            cover_html = ""
            if b.get("image"):
                cover_html = f"""
                <td width="95" valign="top" style="padding-right: 18px;">
                  <a href="{us_link}" style="text-decoration:none;">
                    <img src="{b['image']}" alt="Cover" width="95" style="width:95px; height:auto; border-radius:4px; border:1px solid #D8CFC8; display:block; box-shadow: 0 2px 4px rgba(0,0,0,0.08);" />
                  </a>
                </td>
                """

            html += f"""
                  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#FFFFFF; border: 1px solid #EBE5DF; border-radius: 8px; padding: 14px; margin-bottom: 14px;">
                    <tr>
                      {cover_html}
                      <td valign="top">
                        <a href="{us_link}" style="font-size: 16px; font-weight: bold; color: #8B3A2B; text-decoration: none; line-height: 1.3;">
                          {b['title']}
                        </a>
                        <div style="font-size: 13px; color: #5A524D; margin: 5px 0;">
                          <strong style="color: #1A1715; background-color: #F3EDE4; padding: 2px 6px; border-radius: 4px;">{b['price']}</strong>
                          &nbsp;&bull;&nbsp; {rating_display}
                        </div>
                        <p style="font-size: 13.5px; color: #3D3633; font-style: italic; margin: 6px 0 10px 0; line-height: 1.45;">
                          "{b.get('hook', '')}"
                        </p>
                        <div>
                          <a href="{us_link}" style="display:inline-block; font-size: 12px; font-weight: bold; color: #FFFFFF; background-color: #8B3A2B; padding: 6px 12px; border-radius: 4px; text-decoration: none;">
                            View on Amazon &rarr;
                          </a>
                          <a href="{uk_link}" style="display:inline-block; font-size: 11px; color: #786E68; text-decoration: underline; margin-left: 10px;">
                            UK (.co.uk)
                          </a>
                        </div>
                      </td>
                    </tr>
                  </table>
            """

    html += """
                </td>
              </tr>
              <tr>
                <td style="background-color:#F3EDE4; padding: 20px 24px; text-align: center; border-top: 1px solid #EBE5DF; font-size: 12px; color: #786E68;">
                  <p style="margin: 0 0 6px 0;">Prices and availability are accurate as of dispatch and may change quickly on Amazon. As an Amazon Associate, The Curated Spine earns from qualifying purchases.</p>
                  <p style="margin: 0;">&copy; 2026 The Curated Spine &bull; <a href="https://thecuratedspine.com" style="color:#8B3A2B;">thecuratedspine.com</a> &bull; <a href="{$unsubscribe}" style="color:#786E68; text-decoration:underline;">Unsubscribe</a></p>
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
    today_picks = []
    recent_picks = []

    # Grab the #1 book from each main genre for "Today's Dispatch", and #2 for "More From Today's Shelf"
    for genre_label, books in recommendations.items():
        base_genre = GENRES.get(genre_label, {}).get("base_genre", genre_label)
        for i, b in enumerate(books):
            us_link, uk_link = format_store_links(b["link"])
            card_data = {
                "genre": base_genre,
                "title": b["title"],
                "cover": b.get("image") or "",
                "price_uk": "Free" if "0.00" in b["price"] or "free" in b["price"].lower() else "£0.99",
                "price_us": "Free" if "0.00" in b["price"] or "free" in b["price"].lower() else b["price"],
                "rating": b["rating"] if b["rating"] > 0 else "New",
                "reviews": f"{b['reviews']:,}" if b["reviews"] > 0 else "Hot Release",
                "badge": f"★ Featured in {base_genre}" if i == 0 else f"Reader Pick • {base_genre}",
                "hook": b.get("hook", ""),
                "link_uk": uk_link,
                "link_us": us_link
            }
            if i == 0 and len(today_picks) < 4:
                today_picks.append(card_data)
            elif i == 1 and len(recent_picks) < 4:
                recent_picks.append(card_data)

    if len(recent_picks) < 4:
        recent_picks = (recent_picks + today_picks)[:4]

    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "display_date": datetime.now(timezone.utc).strftime("%A, %d %B %Y"),
        "books": today_picks[:4],
        "recent_books": recent_picks[:4]
    }
    with open(LATEST_ISSUE_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"-> Saved {len(today_picks[:4])} today picks and {len(recent_picks[:4])} recent picks to {LATEST_ISSUE_FILE}!")


def dispatch_via_mailerlite(html_content):
    ml_key = os.environ.get("MAILERLITE_API_KEY")
    sender_email = os.environ.get("MAILERLITE_SENDER_EMAIL", "hello@thecuratedspine.com")

    if not ml_key:
        print("-> No MAILERLITE_API_KEY found in GitHub Secrets. Skipping MailerLite broadcast.")
        return

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {ml_key}",
        "User-Agent": "TheCuratedSpineBot/1.0"
    }

    campaign_payload = {
        "name": f"Daily Dispatch — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "type": "regular",
        "emails": [{
            "subject": f"The Curated Spine — {datetime.now().strftime('%d %b %Y')}",
            "from_name": "The Curated Spine",
            "from": sender_email,
            "content": html_content
        }],
        "groups": [
            "201220938717463907",  # Sci-Fi
            "201220954450298498",  # Historical Fiction
            "201220964201006584",  # Literary Fiction
            "201220945613948200"   # Romance
        ]
    }

    try:
        print(f"-> Creating MailerLite campaign from '{sender_email}'...")
        req = urllib.request.Request(
            "https://connect.mailerlite.com/api/campaigns",
            data=json.dumps(campaign_payload).encode("utf-8"),
            headers=headers,
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            campaign_data = json.loads(resp.read().decode("utf-8"))
            campaign_id = campaign_data["data"]["id"]
            print(f"   Campaign created! ID: {campaign_id}")

        send_req = urllib.request.Request(
            f"https://connect.mailerlite.com/api/campaigns/{campaign_id}/schedule",
            data=json.dumps({"delivery": "instant"}).encode("utf-8"),
            headers=headers,
            method="POST"
        )
        with urllib.request.urlopen(send_req, timeout=30) as send_resp:
            print("-> SUCCESS: MailerLite campaign dispatched to all subscribers!")

    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="ignore")
        print(f"-> MailerLite HTTP Error {e.code}: {error_body}")
    except Exception as e:
        print(f"-> MailerLite API Error: {e}")


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

    # 1. Update website showcase JSON
    save_latest_issue_for_website(recommendations)

    # 2. Build the Bohemian HTML Email
    html = build_bohemian_email_html(recommendations)

    # 3. Broadcast to all subscribers via MailerLite!
    dispatch_via_mailerlite(html)

    # 4. Optional backup: Send via Gmail SMTP if credentials are set
    sender = os.environ.get("EMAIL_SENDER")
    password = os.environ.get("EMAIL_PASSWORD")
    receiver = os.environ.get("EMAIL_RECEIVER")

    if sender and password and receiver:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[Admin Copy] The Curated Spine — {datetime.now().strftime('%d %b %Y')}"
        msg["From"] = f"The Curated Spine <{sender}>"
        msg["To"] = receiver
        msg.attach(MIMEText(html, "html"))

        try:
            server = smtplib.SMTP("smtp.gmail.com", 587)
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(sender, password)
            server.send_message(msg)
            server.quit()
            print("-> Admin Gmail copy sent successfully!")
        except Exception as e:
            print(f"-> Admin Gmail dispatch failed: {e}")


if __name__ == "__main__":
    run_bot()
