import re
import logging
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.collection import Collection
from pymongo.database import Database
import config
from extractor import FORBIDDEN_TITLES, is_valid_news_title, strip_html_tags, validate_and_normalize_image

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

def cleanup_invalid_google_news_records():
    """
    Purges/rejects Google News feed wrapper records and strips generic placeholder images.
    """
    try:
        col = get_news_collection()
        
        # 1. Reject records with generic feed titles
        for title in FORBIDDEN_TITLES:
            col.update_many(
                {"title": {"$regex": f"^{re.escape(title)}$", "$options": "i"}},
                {"$set": {"content_status": "rejected"}}
            )

        # 2. Reject records where source is generic feed and title is invalid
        col.update_many(
            {
                "$or": [
                    {"title": {"$regex": "^Google News", "$options": "i"}},
                    {"title": {"$regex": "Google News$", "$options": "i"}},
                ],
                "content_status": "published"
            },
            {"$set": {"content_status": "rejected"}}
        )

        # 3. Clean remaining valid records
        cursor = col.find({"content_status": "published"})
        for doc in cursor:
            title = doc.get("title", "")
            if not is_valid_news_title(title):
                col.update_one({"_id": doc["_id"]}, {"$set": {"content_status": "rejected"}})
                continue

            raw_img = doc.get("image_url") or doc.get("image")
            valid_img = validate_and_normalize_image(raw_img)

            source_name = doc.get("source_name") or doc.get("source") or "TezKhabar Wire"
            if source_name.lower().startswith("google news"):
                source_name = doc.get("source_domain", "TezKhabar Wire").replace("www.", "")

            col.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {
                        "image": valid_img,
                        "image_url": valid_img,
                        "source": source_name,
                        "source_name": source_name,
                    }
                }
            )

        valid_count = col.count_documents({"content_status": "published"})
        logger.info(f"[DB Cleanup] Completed. Verified published articles in database: {valid_count}")
    except Exception as e:
        logger.error(f"[DB Cleanup Error]: {e}")

def init_db() -> bool:
    try:
        client = get_mongo_client()
        client.admin.command("ping")
        col = get_news_collection()
        
        col.create_index([("slug", ASCENDING)], unique=True, background=True)
        col.create_index([("source_url", ASCENDING)], unique=True, background=True)
        col.create_index([("canonical_source_url", ASCENDING)], background=True)
        col.create_index([("published_at", DESCENDING)], background=True)
        col.create_index([("category", ASCENDING), ("published_at", DESCENDING)], background=True)
        col.create_index([("content_status", ASCENDING)], background=True)

        cleanup_invalid_google_news_records()
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
