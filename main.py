import os
import time
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

MONGO_URI = os.getenv("MONGO_URI")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "https://tezkhabar.onrender.com")

app = FastAPI(title="TezKhabar Force Engine v4.1")
groq_client = Groq(api_key=GROQ_API_KEY)

# Force connection check
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
    1. TONE: Energetic, youth-centric, spicy.
    2. OUTPUT STRUCTURE: Strictly output in this exact schema format:
       [TITLE] Put the viral title here
       [TAG] Single category tag (Politics, Bollywood, Tech, Sports, Crypto)
       [BADGE] [Breaking 🚨] or [Spicy 🔥] or [Alert ⚠️]
       [BODY] Put the full news body here
    
    Source Text: {raw_text}
    """
    try:
        return call_groq_api(prompt)
    except Exception as e:
        print(f"❌ Groq API Failure: {e}")
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
            print(f"⏭️ Already in DB: {title}")
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
        
        # Force Insert Execution
        insert_res = posts_collection.insert_one(payload)
        print(f"🚀 FORCED INSERT SUCCESSFUL! ID: {insert_res.inserted_id} | Title: {title}")
    except Exception as e:
        print(f"❌ MongoDB Force Insertion Crash: {e}")

def news_scrapper_loop():
    print("🔄 TezKhabar Core Engine v4.1 Active...")
    feeds = [
        "https://news.google.com/rss/search?q=politics+India&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=Bollywood&hl=en-IN&gl=IN&ceid=IN:en"
    ]
    
    while True:
        try:
            for feed_url in feeds:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:2]:
                    if not posts_collection.find_one({"source_url": entry.link}):
                        print(f"📰 Scrape Target: {entry.title}")
                        article_data = scrape_article_data(entry.link)
                        if article_data["text"]:
                            ai_content = rewrite_to_hinglish_groq(article_data["text"])
                            save_to_mongodb(ai_content, article_data["image"], entry.link, fallback_title=entry.title)
                            time.sleep(5)
        except Exception as e:
            print(f"⚠️ Loop Error: {e}")
        time.sleep(1800)

@app.get("/")
def home():
    return {"status": "TezKhabar Backend Server Running Successfully"}

@app.get("/api/news")
def get_all_news(category: str = None, limit: int = 20):
    query = {}
    if category:
        query["category"] = category
    cursor = posts_collection.find(query, {"_id": 0}).sort("created_at", -1).limit(limit)
    return list(cursor)

if __name__ == "__main__":
    scraper_thread = threading.Thread(target=news_scrapper_loop, daemon=True)
    scraper_thread.start()
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
