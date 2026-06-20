import os
import time
import feedparser
import requests
from bs4 import BeautifulSoup
from groq import Groq

# Render Environment Groups se variables read ho rahe hain
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
BLOG_ID = os.getenv("BLOG_ID")
BLOGGER_API_KEY = os.getenv("BLOGGER_API_KEY")

# Groq Client Initialization
groq_client = Groq(api_key=GROQ_API_KEY)

# Duplicate check ke liye set
processed_urls = set()

def scrape_article_text(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        r = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(r.content, 'html.parser')
        paragraphs = soup.find_all('p')
        # Full content ke liye paragraphs collect kar rahe hain
        text = " ".join([p.get_text() for p in paragraphs[:10]])
        return text if len(text) > 200 else None
    except Exception as e:
        print(f"❌ Scraping fail: {url} -> {e}")
        return None

def rewrite_to_hinglish_groq(raw_text):
    prompt = f"""
    You are a viral Indian News Editor for a Gen-Z and Millennial audience. 
    Rewrite the following raw news text into a highly engaging, click-worthy news update in casual Hinglish (natural Roman script mix of Hindi and English).
    
    CRITICAL RULES:
    1. TONE: Energetic and spicy. Use slangs like 'Bawaal', 'Dhamaka', 'Ude hosh', 'Tana-tani' naturally.
    2. FORMAT: Keep it highly scannable. Use proper headings (##) and bold keywords. 
    3. OUTPUT STRUCTURE: Strictly provide output in this exact format:
       [TITLE] Put the viral title here
       [BODY] Put the full rewritten news content here
    
    Source Text: {raw_text}
    """
    try:
        chat_completion = groq_client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
            temperature=0.7,
        )
        return chat_completion.choices[0].message.content
    except Exception as e:
        print(f"❌ Groq API Error: {e}")
        return None

def post_to_blogger(ai_output):
    if not ai_output or "[TITLE]" not in ai_output or "[BODY]" not in ai_output:
        print("❌ AI output format is incomplete.")
        return
    
    try:
        # Title aur Body separation logic
        parts = ai_output.split("[BODY]")
        title = parts[0].replace("[TITLE]", "").strip()
        body = parts[1].strip()
        
        # Newlines ko Blogger HTML compatibility ke liye convert kar rahe hain
        formatted_body = body.replace("\n", "<br>")
        
        # Blogger API Endpoint URL
        url = f"https://www.googleapis.com/blogger/v3/blogs/{BLOG_ID}/posts/?key={BLOGGER_API_KEY}"
        
        payload = {
            "kind": "blogger#post",
            "title": title,
            "content": formatted_body
        }
        
        res = requests.post(url, json=payload, timeout=15)
        if res.status_code == 200:
            print(f"🎉 Blogger post published successfully: {title}")
        else:
            print(f"❌ Blogger REST API Error: {res.text}")
    except Exception as e:
        print(f"❌ Posting runtime error: {e}")

def start_engine():
    print("🔄 TezKhabar engine checking for new stories...")
    
    # Politics + Bollywood Live Streams Feeds
    feeds = [
        "https://news.google.com/rss/search?q=politics+India&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=Bollywood&hl=en-IN&gl=IN&ceid=IN:en"
    ]
    
    for feed_url in feeds:
        feed = feedparser.parse(feed_url)
        for entry in feed.entries[:2]: # Top 2 breaking topics filter
            if entry.link not in processed_urls:
                processed_urls.add(entry.link)
                print(f"📰 Found target story: {entry.title}")
                
                raw_text = scrape_article_text(entry.link)
                if raw_text:
                    ai_content = rewrite_to_hinglish_groq(raw_text)
                    post_to_blogger(ai_content)
                    time.sleep(5) # API Rate Limit protection delay

if __name__ == "__main__":
    # Infinite loop handler (Runs every 30 minutes seamlessly)
    while True:
        try:
            start_engine()
        except Exception as global_error:
            print(f"⚠️ Loop Warning: {global_error}")
        
        time.sleep(1800)
