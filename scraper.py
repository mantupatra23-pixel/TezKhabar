import time
import threading
import logging
import urllib.parse
from datetime import datetime, timezone
import feedparser
import config
from database import news_collection
from extractor import clean_source_url, extract_article
from ai_pipeline import create_slug, generate_cluster_id, process_article_with_ai

logger = logging.getLogger("tezkhabar.scraper")

scrape_lock = threading.Lock()

scraper_stats = {
    "last_scrape_started": None,
    "last_scrape_finished": None,
    "last_scrape_success": True,
    "articles_discovered": 0,
    "articles_saved": 0,
    "articles_skipped": 0,
}

# Controlled Indian & Global Editorial Feeds
RSS_FEEDS = [
    {"name": "Google News India", "category": "india", "url": "https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en"},
    {"name": "Google News Politics", "category": "politics", "url": "https://news.google.com/rss/headlines/section/topic/POLITICS?hl=en-IN&gl=IN&ceid=IN:en"},
    {"name": "Google News Business", "category": "business", "url": "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-IN&gl=IN&ceid=IN:en"},
    {"name": "Google News Technology", "category": "technology", "url": "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-IN&gl=IN&ceid=IN:en"},
    {"name": "Google News Sports", "category": "sports", "url": "https://news.google.com/rss/headlines/section/topic/SPORTS?hl=en-IN&gl=IN&ceid=IN:en"},
    {"name": "Google News Entertainment", "category": "entertainment", "url": "https://news.google.com/rss/headlines/section/topic/ENTERTAINMENT?hl=en-IN&gl=IN&ceid=IN:en"},
    {"name": "Google News World", "category": "world", "url": "https://news.google.com/rss/headlines/section/topic/WORLD?hl=en-IN&gl=IN&ceid=IN:en"},
]

def run_news_scraper() -> dict:
    if not scrape_lock.acquire(blocking=False):
        logger.warning("[Scraper] Scraper is already active. Skipping concurrent run.")
        return {"status": "already_running"}

    start_iso = datetime.now(timezone.utc).isoformat()
    scraper_stats["last_scrape_started"] = start_iso
    discovered = 0
    saved = 0
    skipped = 0

    logger.info(f"[Scraper] News ingestion pipeline started at {start_iso}")

    try:
        if news_collection is None:
            logger.error("[Scraper] Database not connected. Aborting run.")
            return {"status": "db_not_connected"}

        for feed in RSS_FEEDS:
            feed_name = feed["name"]
            feed_cat = feed["category"]
            feed_url = feed["url"]

            try:
                parsed = feedparser.parse(feed_url)
                entries = parsed.entries[:config.MAX_ARTICLES_PER_FEED]
            except Exception as e:
                logger.error(f"[Scraper] Failed to fetch feed {feed_name}: {e}")
                continue

            for entry in entries:
                discovered += 1
                raw_url = getattr(entry, "link", None)
                raw_title = getattr(entry, "title", "").strip()

                if not raw_url or not raw_title:
                    skipped += 1
                    continue

                source_url = clean_source_url(raw_url)
                domain = urllib.parse.urlparse(source_url).netloc

                # Duplicate Check by Source URL
                if news_collection.find_one({"source_url": source_url}):
                    skipped += 1
                    continue

                # Extract Article Content
                extracted = extract_article(source_url)
                if not extracted["success"]:
                    skipped += 1
                    continue

                article_title = extracted["title"] or raw_title
                article_body = extracted["body"]
                cluster_id = generate_cluster_id(article_title, feed_cat)

                # Check if cluster already exists (Multi-source aggregation)
                existing_cluster = news_collection.find_one({"story_cluster_id": cluster_id})
                now_iso = datetime.now(timezone.utc).isoformat()

                if existing_cluster:
                    # Append source to existing cluster story
                    new_source = {
                        "name": feed_name,
                        "url": source_url,
                        "published_at": extracted["published_at"] or now_iso,
                        "domain": domain
                    }
                    news_collection.update_one(
                        {"_id": existing_cluster["_id"]},
                        {
                            "$inc": {"source_count": 1},
                            "$addToSet": {"sources": new_source},
                            "$set": {
                                "updated_at": now_iso,
                                "confidence": "multi_source" if existing_cluster.get("source_count", 1) >= 1 else "high_confidence"
                            }
                        }
                    )
                    saved += 1
                    continue

                # Process new story through AI
                ai_data = process_article_with_ai(article_title, article_body, feed_name, feed_cat)

                # Generate Unique Slug
                base_slug = create_slug(ai_data.get("title", article_title))
                slug = base_slug
                counter = 1
                while news_collection.find_one({"slug": slug}):
                    slug = f"{base_slug}-{counter}"
                    counter += 1

                pub_time = extracted["published_at"] or getattr(entry, "published", now_iso)
                canonical_url = f"{config.FRONTEND_URL}/news/{slug}"

                doc = {
                    "slug": slug,
                    "title": ai_data.get("title", article_title),
                    "dek": ai_data.get("dek", ""),
                    "summary": ai_data.get("summary", ""),
                    "content": ai_data.get("content", f"<p>{article_body[:500]}</p>"),
                    "category": ai_data.get("category", feed_cat),
                    "subcategory": ai_data.get("subcategory", "India"),
                    "badge": "Breaking" if feed_cat in ["politics", "india"] else None,
                    "image_url": extracted.get("image_url"),
                    "source_url": source_url,
                    "source_name": feed_name,
                    "source_domain": domain,
                    "author": extracted.get("author") or feed_name,
                    "published_at": pub_time,
                    "updated_at": now_iso,
                    "created_at": now_iso,
                    "language": "en",
                    "region": "IN",
                    "story_cluster_id": cluster_id,
                    "source_count": 1,
                    "sources": [{
                        "name": feed_name,
                        "url": source_url,
                        "published_at": pub_time,
                        "domain": domain
                    }],
                    "key_facts": ai_data.get("key_facts", []),
                    "why_it_matters": ai_data.get("why_it_matters", ""),
                    "timeline": [],
                    "ai_summary": ai_data.get("summary", ""),
                    "ai_generated": True,
                    "content_status": "published",
                    "confidence": "developing",
                    "canonical_source_url": source_url,
                    "canonical_url": canonical_url,
                    "word_count": extracted.get("word_count", 0),
                }

                news_collection.insert_one(doc)
                saved += 1

        scraper_stats["last_scrape_success"] = True
    except Exception as e:
        logger.error(f"[Scraper] Unhandled error during scraper loop: {e}")
        scraper_stats["last_scrape_success"] = False
    finally:
        end_iso = datetime.now(timezone.utc).isoformat()
        scraper_stats["last_scrape_finished"] = end_iso
        scraper_stats["articles_discovered"] = discovered
        scraper_stats["articles_saved"] = saved
        scraper_stats["articles_skipped"] = skipped
        scrape_lock.release()
        logger.info(f"[Scraper] Finished: Discovered={discovered}, Saved={saved}, Skipped={skipped}")

    return {
        "status": "success" if scraper_stats["last_scrape_success"] else "error",
        "discovered": discovered,
        "saved": saved,
        "skipped": skipped,
    }

def background_scraper_loop():
    while True:
        try:
            run_news_scraper()
        except Exception as e:
            logger.error(f"[Scheduler] Loop exception: {e}")
        time.sleep(config.SCRAPER_INTERVAL_SECONDS)
