import re
import logging
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.collection import Collection
from pymongo.database import Database
import config
from extractor import strip_html_tags, is_valid_article_image

logger = logging.getLogger("tezkhabar.db")

_mongo_client: MongoClient = None
_db: Database = None
_news_collection: Collection = None

def get_mongo_client() -> MongoClient:
    global _mongo_client
    if _mongo_client is None:
        _mongo_client = MongoClient(
            config.MONGO_URI,
            serverSelectionTimeoutMS=8000,
            connectTimeoutMS=8000
        )
    return _mongo_client

def get_db() -> Database:
    global _db
    if _db is None:
        client = get_mongo_client()
        _db = client[config.MONGO_DB_NAME]
    return _db

def get_news_collection() -> Collection:
    global _news_collection
    if _news_collection is None:
        db_instance = get_db()
        _news_collection = db_instance[config.MONGO_COLLECTION_NAME]
    return _news_collection

def normalize_slug_string(text: str) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", text).lower().strip()
    return re.sub(r"[-\s]+", "-", cleaned)[:90].rstrip("-")

def migrate_and_clean_existing_documents():
    """
    Normalizes existing records without deleting any document.
    """
    try:
        col = get_news_collection()
        cursor = col.find({})
        updated_count = 0
        seen_slugs = set()

        for doc in cursor:
            doc_id = doc["_id"]
            title = doc.get("title", "News Story")
            raw_slug = doc.get("slug")
            
            # Generate valid slug if missing
            if not raw_slug or not str(raw_slug).strip():
                base_slug = normalize_slug_string(title) or f"story-{str(doc_id)[:8]}"
            else:
                base_slug = normalize_slug_string(str(raw_slug))

            # Ensure uniqueness
            slug = base_slug
            idx = 2
            while slug in seen_slugs:
                slug = f"{base_slug}-{idx}"
                idx += 1
            seen_slugs.add(slug)

            # Clean raw HTML from dek / summary
            clean_summary = strip_html_tags(doc.get("summary") or doc.get("dek") or title)
            clean_dek = strip_html_tags(doc.get("dek") or clean_summary[:140])

            # Validate image
            raw_img = doc.get("image_url") or doc.get("image")
            image_url = raw_img if is_valid_article_image(raw_img) else None

            # Fix source name if it says Google News
            source_name = doc.get("source_name") or doc.get("source") or "TezKhabar Wire"
            if source_name.lower() == "google news" and doc.get("source_domain"):
                source_name = doc.get("source_domain").replace("www.", "")

            col.update_one(
                {"_id": doc_id},
                {
                    "$set": {
                        "slug": slug,
                        "title": strip_html_tags(title),
                        "summary": clean_summary,
                        "description": clean_summary,
                        "dek": clean_dek,
                        "image": image_url,
                        "image_url": image_url,
                        "source": source_name,
                        "source_name": source_name,
                        "content_status": "published",
                        "canonical_url": f"{config.FRONTEND_URL}/news/{slug}"
                    }
                }
            )
            updated_count += 1

        logger.info(f"[DB Migration] Successfully verified and normalized {updated_count} articles.")
    except Exception as e:
        logger.error(f"[DB Migration Error]: {e}")

def init_db() -> bool:
    try:
        client = get_mongo_client()
        client.admin.command("ping")
        col = get_news_collection()
        
        # Run safe normalization first
        migrate_and_clean_existing_documents()

        # Create indexes
        col.create_index([("slug", ASCENDING)], unique=True, background=True)
        col.create_index([("source_url", ASCENDING)], unique=True, background=True)
        col.create_index([("published_at", DESCENDING)], background=True)
        col.create_index([("created_at", DESCENDING)], background=True)
        col.create_index([("category", ASCENDING), ("published_at", DESCENDING)], background=True)
        col.create_index([("content_status", ASCENDING)], background=True)
        
        count = col.count_documents({})
        logger.info(f"[DB] Connected to MongoDB. Database: '{config.MONGO_DB_NAME}', Verified Articles: {count}")
        return True
    except Exception as e:
        logger.error(f"[DB] MongoDB initialization warning: {e}")
        return False

def get_db_health() -> str:
    try:
        client = get_mongo_client()
        client.admin.command("ping")
        return "connected"
    except Exception:
        return "disconnected"
