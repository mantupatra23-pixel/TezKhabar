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
_news_col: Collection = None
_sources_col: Collection = None
_revisions_col: Collection = None

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
    global _news_col
    if _news_col is None:
        db_instance = get_db()
        _news_col = db_instance[config.MONGO_COLLECTION_NAME]
    return _news_col

def get_sources_collection() -> Collection:
    global _sources_col
    if _sources_col is None:
        db_instance = get_db()
        _sources_col = db_instance[config.MONGO_SOURCES_COLLECTION]
    return _sources_col

def get_revisions_collection() -> Collection:
    global _revisions_col
    if _revisions_col is None:
        db_instance = get_db()
        _revisions_col = db_instance[config.MONGO_REVISIONS_COLLECTION]
    return _revisions_col

def migrate_and_clean_database():
    """
    Cleans previous aggregator records: unpublishes fake Google News wrappers,
    assigns TezKhabar Editorial Desk as default public author, and normalizes public fields.
    """
    try:
        col = get_news_collection()

        # 1. Unpublish generic feed items
        for title in FORBIDDEN_TITLES:
            col.update_many(
                {"title": {"$regex": f"^{re.escape(title)}$", "$options": "i"}},
                {"$set": {"status": "rejected", "content_status": "rejected"}}
            )

        col.update_many(
            {
                "$or": [
                    {"title": {"$regex": "^Google News", "$options": "i"}},
                    {"title": {"$regex": "Google News$", "$options": "i"}},
                    {"content": {"$regex": "News content updates shortly", "$options": "i"}}
                ]
            },
            {"$set": {"status": "rejected", "content_status": "rejected"}}
        )

        # 2. Update remaining records to editorial attribution standards
        cursor = col.find({"content_status": "published"})
        for doc in cursor:
            raw_img = doc.get("image_url") or doc.get("image")
            valid_img = validate_and_normalize_image(raw_img)
            title = strip_html_tags(doc.get("title", ""))

            col.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {
                        "author": config.DEFAULT_AUTHOR,
                        "publisher": config.DEFAULT_PUBLISHER,
                        "source_name": config.DEFAULT_AUTHOR,
                        "source": config.DEFAULT_AUTHOR,
                        "image": valid_img,
                        "image_url": valid_img,
                        "title": title,
                        "status": "published",
                        "content_status": "published",
                    }
                }
            )

        valid_count = col.count_documents({"content_status": "published"})
        logger.info(f"[DB Migration] Successfully verified {valid_count} published TezKhabar editorial records.")
    except Exception as e:
        logger.error(f"[DB Migration Error]: {e}")

def init_db() -> bool:
    try:
        client = get_mongo_client()
        client.admin.command("ping")
        news_col = get_news_collection()
        sources_col = get_sources_collection()
        revisions_col = get_revisions_collection()

        # Indexes
        news_col.create_index([("slug", ASCENDING)], unique=True, background=True)
        news_col.create_index([("published_at", DESCENDING)], background=True)
        news_col.create_index([("category", ASCENDING), ("published_at", DESCENDING)], background=True)
        news_col.create_index([("status", ASCENDING)], background=True)
        news_col.create_index([("story_cluster_id", ASCENDING)], background=True)

        sources_col.create_index([("article_id", ASCENDING)], background=True)
        sources_col.create_index([("source_url", ASCENDING)], unique=True, background=True)
        sources_col.create_index([("canonical_source_url", ASCENDING)], background=True)

        revisions_col.create_index([("article_id", ASCENDING)], background=True)

        migrate_and_clean_database()
        return True
    except Exception as e:
        logger.error(f"[DB] Initialization exception: {e}")
        return False

def get_db_health() -> str:
    try:
        client = get_mongo_client()
        client.admin.command("ping")
        return "connected"
    except Exception:
        return "disconnected"
