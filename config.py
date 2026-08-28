import os
from typing import List, Dict
from dotenv import load_dotenv

load_dotenv()

# URLs
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://tezkhabar-frontend.onrender.com").rstrip("/")
BACKEND_URL = os.getenv("BACKEND_URL", "https://tezkhabar.onrender.com").rstrip("/")

# Database
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "tezkhabar_db")
MONGO_COLLECTION_NAME = os.getenv("MONGO_COLLECTION_NAME", "news_posts")
MONGO_SOURCES_COLLECTION = os.getenv("MONGO_SOURCES_COLLECTION", "article_sources")
MONGO_REVISIONS_COLLECTION = os.getenv("MONGO_REVISIONS_COLLECTION", "article_revisions")

# AI Engine
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")

# Security & Admin
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "tezkhabar-secret-admin-key-2026")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON", "")

# Ingestion Settings
SCRAPER_INTERVAL_SECONDS = int(os.getenv("SCRAPER_INTERVAL_SECONDS", "600"))
MAX_ARTICLES_PER_FEED = int(os.getenv("MAX_ARTICLES_PER_FEED", "10"))
REQUEST_TIMEOUT_SECONDS = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "8"))
MIN_QUALITY_SCORE = int(os.getenv("MIN_QUALITY_SCORE", "65"))

# Editorial Attribution Policy: "none" | "based_on_multiple_reports" | "source_names"
PUBLIC_ATTRIBUTION_STYLE = os.getenv("PUBLIC_ATTRIBUTION_STYLE", "based_on_multiple_reports")
DEFAULT_AUTHOR = "TezKhabar Editorial Desk"
DEFAULT_PUBLISHER = "TezKhabar"

# Controlled Categories
CONTROLLED_CATEGORIES = [
    "india",
    "politics",
    "business",
    "technology",
    "ai",
    "finance",
    "sports",
    "cricket",
    "entertainment",
    "bollywood",
    "education",
    "jobs",
    "startups",
    "automobile",
    "world",
    "science",
    "health"
]

CATEGORY_ALIAS_MAP = {
    "tech": "technology",
    "movies": "entertainment",
    "cinema": "entertainment",
    "economy": "business",
    "markets": "finance",
    "national": "india",
    "global": "world",
    "international": "world",
}

# Domain to Publisher Known Mapping
DOMAIN_PUBLISHER_MAP: Dict[str, str] = {
    "ndtv.com": "NDTV",
    "thehindu.com": "The Hindu",
    "timesofindia.indiatimes.com": "The Times of India",
    "indianexpress.com": "The Indian Express",
    "hindustantimes.com": "Hindustan Times",
    "moneycontrol.com": "Moneycontrol",
    "livemint.com": "Mint",
    "economictimes.indiatimes.com": "The Economic Times",
    "indiatoday.in": "India Today",
    "reuters.com": "Reuters",
    "bbc.com": "BBC News",
    "bbc.co.uk": "BBC News",
    "deccanherald.com": "Deccan Herald",
    "theprint.in": "ThePrint",
    "scroll.in": "Scroll.in",
    "thewire.in": "The Wire",
    "news18.com": "News18",
    "zeenews.india.com": "Zee News",
    "business-standard.com": "Business Standard",
    "financialexpress.com": "Financial Express",
    "espncricinfo.com": "ESPNcricinfo",
    "cricbuzz.com": "Cricbuzz",
    "livelaw.in": "Live Law",
    "barandbench.com": "Bar and Bench",
}

# Production CORS
ALLOWED_ORIGINS: List[str] = [
    "https://tezkhabar-frontend.onrender.com",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
