import os
from typing import List
from dotenv import load_dotenv

load_dotenv()

# Service URLs
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://tezkhabar-frontend.onrender.com").rstrip("/")
BACKEND_URL = os.getenv("BACKEND_URL", "https://tezkhabar.onrender.com").rstrip("/")

# Database
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "tezkhabar_db")
MONGO_COLLECTION_NAME = os.getenv("MONGO_COLLECTION_NAME", "news_posts")

# AI Engine
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# Security & Admin
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "tezkhabar-secret-admin-key-2026")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON", "")

# Scraper Settings
SCRAPER_INTERVAL_SECONDS = int(os.getenv("SCRAPER_INTERVAL_SECONDS", "600"))
MAX_ARTICLES_PER_FEED = int(os.getenv("MAX_ARTICLES_PER_FEED", "10"))
REQUEST_TIMEOUT_SECONDS = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "12"))

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
    "viral",
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

# CORS Allowed Origins
ALLOWED_ORIGINS: List[str] = [
    "https://tezkhabar-frontend.onrender.com",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
