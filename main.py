import os
import time
import json
import threading
import xml.etree.ElementTree as ET
from xml.dom import minidom
import feedparser
import requests
from bs4 import BeautifulSoup
from groq import Groq
from pymongo import MongoClient
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from tenacity import retry, stop_after_attempt, wait_exponential
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession

MONGO_URI = os.getenv("MONGO_URI")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON")  # Service Account JSON String
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "https://tezkhabar.onrender.com")

app = FastAPI(title="TezKhabar Ultimate Master Engine v5.0")
groq_client = Groq(api_key=GROQ_API_KEY)

# --- DATABASE CONNECTION MONITOR ---
try:
    db_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db = db_client["tezkhabar_db"]
    posts_collection = db["news_posts"]
    db_client.server_info()
    print("✅ MongoDB Connection Established Successfully!")
except Exception as e:
    print(f"❌ CRITICAL: MongoDB Connection Failed: {e}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- INSTANT GOOGLE INDEXING ENGINE ---
def push_to_google_instant_index(target_url):
    """Fires a high-priority notify alert directly to Google Bots to index pages in minutes"""
    try:
        if not GOOGLE_CREDS_JSON:
            print("⚠️ Google Indexing Alert: GOOGLE_CREDS_JSON variable missing. Skipping ping.")
            return
            
        creds_info = json.loads(GOOGLE_CREDS_JSON)
        scoped_creds = service_account.Credentials.from_service_account_info(
            creds_info, 
            scopes=["https://www.googleapis.com/auth/indexing"]
        )
        
        authed_session = AuthorizedSession(scoped_creds)
        endpoint = "https://indexing.googleapis.com/v3/urlNotifications:publish"
        
        payload = {
            "url": target_url,
            "type": "URL_UPDATED"
        }
        
        response = authed_session.post(endpoint, json=payload, timeout=10)
        if response.status_code == 200:
            print(f"🔥 GOOGLE CRAWLER PINGED SUCCESSFULLY! Target URL is now Live-Queued: {target_url}")
        else:
            print(f"❌ Indexing API Refusal: {response.status_code} -> {response.text}")
    except Exception as e:
        print(f"❌ Webmaster Authorization Crash: {e}")

# --- CORE PARSING & CONTENT GENERATION SYSTEM ---
def scrape_article_data(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        r = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(r.content, 'html.parser')
        
        paragraphs = soup.find_all('p')
        text = " ".join([p.get_text() for p in paragraphs[:10]])
        
        img_url = None
        img_tag = soup.find('meta', property='og:image') or soup.find('meta', attrs={'name': 'twitter:image'})
        if img_tag:
            img_url = img_tag.get('content')
        else:
            first_img = soup.find('img')
            if first_img and first_img.get('src') and first_img.get('src').startswith('http'):
                img_url = first_img.get('src')
                
        return {"text": text if len(text) > 200 else None, "image": img_url}
    except Exception as e:
        print(f"❌ Scraping fail: {url} -> {e}")
        return {"text": None, "image": None}

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def call_groq_api(prompt):
    chat_completion = groq_client.chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        model="llama-3.3-70b-versatile",
        temperature=0.7,
    )
    return chat_completion.choices[0].message.content

def rewrite_to_hinglish_groq(raw_text):
    prompt = f"""
    You are a viral Indian News Editor. Rewrite the following news into highly engaging, viral Hinglish (Roman script mix of Hindi and English).
    
    CRITICAL RULES:
    1. TONE: Energetic, youth-centric, spicy, and extremely catchy.
    2. OUTPUT STRUCTURE: Strictly output in this exact schema format:
       [TITLE] Put the viral title here
       [TAG] Single category tag (Politics, Bollywood, Tech, Sports, Crypto)
       [BADGE] [Breaking 🚨] or [Spicy 🔥] or [Alert ⚠️] or [Trending 🚀]
       [BODY] Put the full news body here
    
    Source Text: {raw_text}
    """
    try:
        return call_groq_api(prompt)
    except Exception as e:
        print(f"❌ Groq System Error: {e}")
        return None

def save_to_mongodb(ai_output, image_url, source_url, fallback_title="Breaking News"):
    try:
        title = fallback_title
        body_content = "News content updates shortly."
        tag = "General"
        badge = "[Breaking 🚨]"

        if ai_output and "[BODY]" in ai_output:
            parts_body = ai_output.split("[BODY]")
            body_content = parts_body[1].strip()
            
            parts_title_segment = parts_body[0].split("[BADGE]")
            if len(parts_title_segment) > 1:
                badge = parts_title_segment[1].strip()
            
            parts_title_tag = parts_title_segment[0].split("[TAG]")
            title = parts_title_tag[0].replace("[TITLE]", "").strip()
            if len(parts_title_tag) > 1:
                tag = parts_title_tag[1].strip()

        if posts_collection.find_one({"source_url": source_url}):
            print(f"箱 Already exists in DB: {title}")
            return

        slug = title.lower().replace(" ", "-").replace("?", "").replace("!", "").replace("'", "")
        slug = "".join([c for c in slug if c.isalnum() or c == '-'])[:60]

        payload = {
            "title": title,
            "slug": slug,
            "content": body_content,
            "category": tag,
            "badge": badge,
            "image_url": image_url,
            "source_url": source_url,
            "created_at": time.time()
        }
        
        insert_res = posts_collection.insert_one(payload)
        generated_url = f"{RENDER_EXTERNAL_URL}/news/{slug}"
        print(f"🚀 INSERT SUCCESSFUL! ID: {insert_res.inserted_id} | Title: {title}")
        
        # Trigger Instant Indexing Worker Immediately after successful MongoDB Write
        push_to_google_instant_index(generated_url)
        
    except Exception as e:
        print(f"❌ MongoDB Custom Insertion Crash: {e}")

def run_core_scraping_engine():
    feeds = [
        "https://news.google.com/rss/search?q=politics+India&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=Bollywood&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=Tech+India&hl=en-IN&gl=IN&ceid=IN:en"
    ]
    scraped_count = 0
    for feed_url in feeds:
        feed = feedparser.parse(feed_url)
        for entry in feed.entries[:3]:
            if not posts_collection.find_one({"source_url": entry.link}):
                print(f"📰 Targeting Fresh Post: {entry.title}")
                article_data = scrape_article_data(entry.link)
                
                text_content = article_data["text"] if article_data["text"] else entry.title + " full updates coming soon."
                ai_content = rewrite_to_hinglish_groq(text_content)
                save_to_mongodb(ai_content, article_data["image"], entry.link, fallback_title=entry.title)
                scraped_count += 1
                time.sleep(3)
    return scraped_count

def news_scrapper_loop():
    print("🔄 TezKhabar Core Engine Background Scraper Loop Online...")
    while True:
        try:
            run_core_scraping_engine()
        except Exception as e:
            print(f"⚠️ Master Loop Exception: {e}")
        
        print("💤 Scraper cooling down. Going to sleep for 10 minutes...")
        time.sleep(600)  # 60 seconds * 10 minutes

# --- PRODUCTION API ENDPOINTS FOR FRONTEND ---

@app.get("/")
def home():
    return {"status": "TezKhabar Master Core Engine v5.0 Live & Operational"}

@app.get("/api/news")
def get_all_news(category: str = None, limit: int = 20):
    query = {}
    if category:
        query["category"] = category
    cursor = posts_collection.find(query, {"_id": 0}).sort("created_at", -1).limit(limit)
    return list(cursor)

@app.get("/api/scrape-now")
def force_scrape():
    """Bypasses cooling timer. Instantly forces an extraction and indexing push query"""
    try:
        count = run_core_scraping_engine()
        return {"status": "Success", "items_processed": count}
    except Exception as e:
        return {"status": "Error", "message": str(e)}

@app.get("/api/sitemap.xml")
def get_dynamic_sitemap():
    urlset = ET.Element("urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")
    home_url = ET.SubElement(urlset, "url")
    ET.SubElement(home_url, "loc").text = RENDER_EXTERNAL_URL
    ET.SubElement(home_url, "priority").text = "1.0"
    
    cursor = posts_collection.find({}, {"slug": 1, "created_at": 1}).sort("created_at", -1).limit(500)
    for post in cursor:
        if "slug" in post:
            url_node = ET.SubElement(urlset, "url")
            ET.SubElement(url_node, "loc").text = f"{RENDER_EXTERNAL_URL}/news/{post['slug']}"
            ET.SubElement(url_node, "priority").text = "0.8"
            
    xml_str = ET.tostring(urlset, encoding='utf-8')
    parsed_xml = minidom.parseString(xml_str)
    return Response(content=parsed_xml.toprettyxml(indent="  "), media_type="application/xml")

if __name__ == "__main__":
    scraper_thread = threading.Thread(target=news_scrapper_loop, daemon=True)
    scraper_thread.start()
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
