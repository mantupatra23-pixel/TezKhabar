import time
import threading
import logging
import urllib.parse
from datetime import datetime, timezone
import feedparser
import config
from database import get_news_collection
from extractor import clean_source_url, extract_article
from ai_pipeline import create_slug, generate_cluster_id, process_article_with_ai

logger = logging.getLogger("tezkhabar.scraper")

scrape_lock = threading.Lock()

scraper_stats = {
    "last_scrape_started": None,
    "last_scrape_finished": None,
    "last_scrape_success": True,
    "feeds_seen": 0,
    "rss_entries_seen": 0,
    "new_candidates": 0,
    "duplicates": 0,
    "extraction_success": 0,
    "extraction_failed": 0,
    "ai_success": 0,
    "ai_failed": 0,
    "articles_saved": 0,
    "articles_skipped": 0,
}

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
        logger.warning("[Scraper] Scraper is already running. Skipping concurrent run.")
        return {"status": "already_running"}

    start_iso = datetime.now(timezone.utc).isoformat()
    scraper_stats["last_scrape_started"] = start_iso

    feeds_seen = 0
    rss_entries_seen = 0
    new_candidates = 0
    duplicates = 0
    extraction_success = 0
    extraction_failed = 0
    ai_success = 0
    ai_failed = 0
    articles_saved = 0
    articles_skipped = 0

    logger.info(f"[Scraper] News ingestion pipeline started at {start_iso}")

    try:
        col = get_news_collection()
        if col is None:
            logger.error("[Scraper] Database collection is None. Aborting run.")
            return {"status": "db_not_connected"}

        for feed in RSS_FEEDS:
            feeds_seen += 1
            feed_name = feed["name"]
            feed_cat = feed["category"]
            feed_url = feed["url"]

            logger.info(f"[RSS] Fetching: {feed_name}")
            try:
                parsed = feedparser.parse(feed_url)
                entries = parsed.entries[:config.MAX_ARTICLES_PER_FEED]
                logger.info(f"[RSS] {feed_name}: HTTP status 200, Entries: {len(entries)}")
            except Exception as e:
                logger.error(f"[RSS] Failed to fetch feed {feed_name}: {e}")
                continue

            for entry in entries:
                rss_entries_seen += 1
                raw_url = getattr(entry, "link", None)
                raw_title = getattr(entry, "title", "").strip()
                raw_summary = getattr(entry, "summary", "").strip()

                if not raw_url or not raw_title:
                    articles_skipped += 1
                    continue

                source_url = clean_source_url(raw_url)
                domain = urllib.parse.urlparse(source_url).netloc

                # Duplicate check
                if col.find_one({"source_url": source_url}):
                    duplicates += 1
                    articles_skipped += 1
                    continue

                new_candidates += 1

                # Extraction
                extracted = extract_article(source_url, fallback_title=raw_title, fallback_summary=raw_summary)
                if not extracted["success"]:
                    extraction_failed += 1
                    articles_skipped += 1
                    continue

                extraction_success += 1
                article_title = extracted["title"] or raw_title
                article_body = extracted["body"]
                cluster_id = generate_cluster_id(article_title, feed_cat)

                now_iso = datetime.now(timezone.utc).isoformat()
                existing_cluster = col.find_one({"story_cluster_id": cluster_id})

                if existing_cluster:
                    new_source = {
                        "name": feed_name,
                        "url": source_url,
                        "published_at": extracted["published_at"] or now_iso,
                        "domain": domain
                    }
                    col.update_one(
                        {"_id": existing_cluster["_id"]},
                        {
                            "$inc": {"source_count": 1},
                            "$addToSet": {"sources": new_source},
                            "$set": {
                                "updated_at": now_iso,
                                "confidence": "multi_source"
                            }
                        }
                    )
                    articles_saved += 1
                    continue

                # Process through AI
                try:
                    ai_data = process_article_with_ai(article_title, article_body, feed_name, feed_cat)
                    ai_success += 1
                except Exception as e:
                    logger.warning(f"[AI] Failed for '{article_title[:40]}': {e}")
                    ai_failed += 1
                    ai_data = {
                        "title": article_title,
                        "dek": article_body[:140],
                        "summary": article_body[:280],
                        "category": feed_cat,
                        "subcategory": "India",
                        "content": f"<p>{article_body[:500]}</p>",
                        "key_facts": [article_title],
                        "why_it_matters": f"Reported by {feed_name}.",
                        "confidence": "developing"
                    }

                # Generate Unique Slug
                base_slug = create_slug(ai_data.get("title", article_title))
                slug = base_slug
                counter = 1
                while col.find_one({"slug": slug}):
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

                col.insert_one(doc)
                articles_saved += 1
                logger.info(f"[Scraper] Saved: '{doc['title'][:50]}...' -> /news/{slug}")

        scraper_stats["last_scrape_success"] = True
    except Exception as e:
        logger.error(f"[Scraper] Unhandled error: {e}")
        scraper_stats["last_scrape_success"] = False
    finally:
        end_iso = datetime.now(timezone.utc).isoformat()
        scraper_stats["last_scrape_finished"] = end_iso
        scraper_stats["feeds_seen"] = feeds_seen
        scraper_stats["rss_entries_seen"] = rss_entries_seen
        scraper_stats["new_candidates"] = new_candidates
        scraper_stats["duplicates"] = duplicates
        scraper_stats["extraction_success"] = extraction_success
        scraper_stats["extraction_failed"] = extraction_failed
        scraper_stats["ai_success"] = ai_success
        scraper_stats["ai_failed"] = ai_failed
        scraper_stats["articles_saved"] = articles_saved
        scraper_stats["articles_skipped"] = articles_skipped
        scrape_lock.release()

        logger.info(f"[Scraper] Finished | Feeds={feeds_seen} | RSS Entries={rss_entries_seen} | Candidates={new_candidates} | Duplicates={duplicates} | Saved={articles_saved} | Skipped={articles_skipped}")

    return {
        "status": "completed" if scraper_stats["last_scrape_success"] else "error",
        "feeds": feeds_seen,
        "entries": rss_entries_seen,
        "new": new_candidates,
        "duplicates": duplicates,
        "saved": articles_saved,
        "skipped": articles_skipped,
    }

def background_scraper_loop():
    # Initial immediate ingestion run on startup
    time.sleep(3)
    try:
        run_news_scraper()
    except Exception as e:
        logger.error(f"[Scheduler] Initial scrape failed: {e}")

    while True:
        time.sleep(config.SCRAPER_INTERVAL_SECONDS)
        try:
            run_news_scraper()
        except Exception as e:
            logger.error(f"[Scheduler] Recurring scrape failed: {e}")
