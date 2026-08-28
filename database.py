import logging
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.collection import Collection
from pymongo.database import Database
import config

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

def init_db() -> bool:
    try:
        client = get_mongo_client()
        # Verify active connection with ping
        client.admin.command("ping")
        col = get_news_collection()
        
        # Verify and create indexes safely
        col.create_index([("source_url", ASCENDING)], unique=True, background=True)
        col.create_index([("slug", ASCENDING)], unique=True, background=True)
        col.create_index([("published_at", DESCENDING)], background=True)
        col.create_index([("created_at", DESCENDING)], background=True)
        col.create_index([("category", ASCENDING), ("published_at", DESCENDING)], background=True)
        col.create_index([("story_cluster_id", ASCENDING)], background=True)
        col.create_index([("source_name", ASCENDING)], background=True)
        
        count = col.count_documents({})
        logger.info(f"[DB] Ping verified successfully. Database: '{config.MONGO_DB_NAME}', Collection: '{config.MONGO_COLLECTION_NAME}' (Existing Articles: {count})")
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
