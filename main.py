import os
import time
import feedparser
import requests
from bs4 import BeautifulSoup
from openai import OpenAI

# Environment Variables (Render par configure karenge)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WP_URL = os.getenv("WP_URL")  # Example: https://yourwebsite.com/wp-json/wp/v2/posts
WP_USER = os.getenv("WP_USER")
WP_PASSWORD = os.getenv("WP_PASSWORD")  # 16-digit application password

client = OpenAI(api_key=OPENAI_API_KEY)

# Sentinal list duplicate posts se bachne ke liye
processed_urls = set()

def scrape_article_text(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        r = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(r.content, 'html.parser')
        paragraphs = soup.find_all('p')
        # Pehle 8 paragraphs extract kar rahe hain full body context ke liye
        text = " ".join([p.get_text() for p in paragraphs[:8]])
        return text if len(text) > 200 else None
    except Exception as e:
        print(f"Error scraping {url}: {e}")
        return None

def rewrite_to_hinglish(raw_text):
    prompt = f"""
    You are a viral Indian News Editor. Take this raw news and rewrite it into a highly engaging, click-worthy blog post in natural Hinglish (Roman script).
    
    Rules:
    1. Tone: Energetic, spicy, conversational (Use words like 'Bawaal', 'Dhamaka', 'Tana-tani' naturally).
    2. Format: Clear subheadings (##), bold keywords, and short paragraphs.
    3. Output structure:
       [TITLE] Put a viral clickbait title here
       [BODY] Put the full blog post content here
    
    Source Text: {raw_text}
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"OpenAI Error: {e}")
        return None

def post_to_wordpress(ai_output):
    if not ai_output or "[TITLE]" not in ai_output:
        return
    
    try:
        # Title aur Body ko split karna
        parts = ai_output.split("[BODY]")
        title = parts[0].replace("[TITLE]", "").strip()
        body = parts[1].strip()
        
        # Markdown to simple clean format processing
        formatted_body = body.replace("\n", "<br>")
        
        payload = {
            "title": title,
            "content": formatted_body,
            "status": "publish"
        }
        
        res = requests.post(WP_URL, json=payload, auth=(WP_USER, WP_PASSWORD))
        if res.status_code == 201:
            print(f"🎉 Successfully Published: {title}")
        else:
            print(f"❌ WP Error: {res.text}")
    except Exception as e:
        print(f"Posting system failed: {e}")

def job():
    print("🔄 Checking for new breaking news...")
    # Politics + Bollywood RSS Feeds
    feeds = [
        "https://news.google.com/rss/search?q=politics+India&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=Bollywood&hl=en-IN&gl=IN&ceid=IN:en"
    ]
    
    for url in feeds:
        feed = feedparser.parse(url)
        for entry in feed.entries[:2]: # Top 2 articles per feed
            if entry.link not in processed_urls:
                processed_urls.add(entry.link)
                print(f"Found new story: {entry.title}")
                
                raw_content = scrape_article_text(entry.link)
                if raw_content:
                    ai_content = rewrite_to_hinglish(raw_content)
                    post_to_wordpress(ai_content)
                    time.sleep(5) # Rate limiting safe buffer

if __name__ == "__main__":
    # Render loop handler (Har 30 mins me check karega)
    while True:
        job()
        time.sleep(1800)
