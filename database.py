import logging
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.collection import Collection
from pymongo.database import Database
import config

logger = logging.getLogger("tezkhabar.db")

client: MongoClient = None
db: Database = None
news_collection: Collection = None

def init_db():
    global client, db, news_collection
    try:
        client = MongoClient(
            config.MONGO_URI,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000
        )
        db = client[config.MONGO_DB_NAME]
        news_collection = db[config.MONGO_COLLECTION_NAME]
        
        # Create indexes safely
        news_collection.create_index([("source_url", ASCENDING)], unique=True, background=True)
        news_collection.create_index([("slug", ASCENDING)], unique=True, background=True)
        news_collection.create_index([("published_at", DESCENDING)], background=True)
        news_collection.create_index([("created_at", DESCENDING)], background=True)
        news_collection.create_index([("category", ASCENDING), ("published_at", DESCENDING)], background=True)
        news_collection.create_index([("story_cluster_id", ASCENDING)], background=True)
        news_collection.create_index([("source_name", ASCENDING)], background=True)
        news_collection.create_index([("title", "text"), ("summary", "text"), ("content", "text")], background=True)
        
        logger.info("[DB] MongoDB connected and indexes verified successfully.")
    except Exception as e:
        logger.error(f"[DB] MongoDB initialization warning (degraded mode): {e}")

def get_db_health() -> str:
    global client
    if not client:
        return "disconnected"
    try:
        client.admin.command("ping")
        return "connected"
    except Exception:
        return "degraded"
