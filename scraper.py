import time
import threading
import logging
import urllib.parse
from datetime import datetime, timezone
import feedparser
import config
from database import get_news_collection
from extractor import (
    extract_article,
    is_valid_news_title,
    strip_html_tags,
    clean_source_url
)
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
    "articles_saved": 0,
    "articles_skipped": 0,
}

RSS_FEEDS = [
    {"name": "India News", "category": "india", "url": "https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en"},
    {"name": "Politics", "category": "politics", "url": "https://news.google.com/rss/headlines/section/topic/POLITICS?hl=en-IN&gl=IN&ceid=IN:en"},
    {"name": "Business", "category": "business", "url": "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-IN&gl=IN&ceid=IN:en"},
    {"name": "Technology", "category": "technology", "url": "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-IN&gl=IN&ceid=IN:en"},
    {"name": "Sports", "category": "sports", "url": "https://news.google.com/rss/headlines/section/topic/SPORTS?hl=en-IN&gl=IN&ceid=IN:en"},
    {"name": "Entertainment", "category": "entertainment", "url": "https://news.google.com/rss/headlines/section/topic/ENTERTAINMENT?hl=en-IN&gl=IN&ceid=IN:en"},
    {"name": "World", "category": "world", "url": "https://news.google.com/rss/headlines/section/topic/WORLD?hl=en-IN&gl=IN&ceid=IN:en"},
]

def run_news_scraper() -> dict:
    if not scrape_lock.acquire(blocking=False):
        logger.warning("[Scraper] Scraper is already active. Skipping concurrent run.")
        return {"status": "already_running"}

    start_iso = datetime.now(timezone.utc).isoformat()
    scraper_stats["last_scrape_started"] = start_iso
    feeds_seen = 0
    rss_entries_seen = 0
    new_candidates = 0
    articles_saved = 0
    articles_skipped = 0

    try:
        col = get_news_collection()
        if col is None:
            logger.error("[Scraper] Database collection is None. Aborting run.")
            return {"status": "db_not_connected"}

        for feed in RSS_FEEDS:
            feeds_seen += 1
            feed_cat = feed["category"]
            feed_url = feed["url"]

            try:
                parsed = feedparser.parse(feed_url)
                entries = parsed.entries[:config.MAX_ARTICLES_PER_FEED]
            except Exception as e:
                logger.error(f"[RSS] Failed to fetch feed {feed['name']}: {e}")
                continue

            for entry in entries:
                rss_entries_seen += 1
                raw_url = getattr(entry, "link", None)
                raw_title = getattr(entry, "title", "").strip()
                raw_summary = getattr(entry, "summary", "").strip()

                if not raw_url or not raw_title:
                    articles_skipped += 1
                    continue

                if not is_valid_news_title(raw_title):
                    articles_skipped += 1
                    continue

                source_url = clean_source_url(raw_url)

                if col.find_one({"$or": [{"source_url": source_url}, {"canonical_source_url": source_url}]}):
                    articles_skipped += 1
                    continue

                new_candidates += 1

                # Resolve original destination URL & extract metadata
                extracted = extract_article(source_url, fallback_title=raw_title, fallback_summary=raw_summary)
                if not extracted["success"]:
                    articles_skipped += 1
                    continue

                article_title = extracted["title"]
                resolved_url = extracted["resolved_url"]
                canonical_source_url = extracted["canonical_url"]
                publisher_name = extracted["publisher_name"]
                publisher_image = extracted["image_url"]
                article_body = extracted["body"]
                domain = extracted["source_domain"]
                has_source_content = extracted["has_source_content"]

                # Deduplicate by resolved publisher URL
                if col.find_one({"$or": [{"canonical_source_url": canonical_source_url}, {"source_url": resolved_url}]}):
                    articles_skipped += 1
                    continue

                cluster_id = generate_cluster_id(article_title, feed_cat)
                now_iso = datetime.now(timezone.utc).isoformat()
                existing_cluster = col.find_one({"story_cluster_id": cluster_id})

                if existing_cluster:
                    new_source = {
                        "name": publisher_name,
                        "url": resolved_url,
                        "published_at": extracted.get("published_at") or now_iso,
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

                # AI Enrichment (Only on real source body)
                ai_data = process_article_with_ai(article_title, article_body, publisher_name, feed_cat)

                base_slug = create_slug(article_title)
                slug = base_slug
                counter = 2
                while col.find_one({"slug": slug}):
                    slug = f"{base_slug}-{counter}"
                    counter += 1

                pub_time = extracted.get("published_at") or getattr(entry, "published", now_iso)
                clean_summary = ai_data.get("summary") or extracted["description"] or article_title

                doc = {
                    "slug": slug,
                    "title": article_title,
                    "dek": ai_data.get("dek", clean_summary[:140]),
                    "summary": clean_summary,
                    "description": clean_summary,
                    "content": ai_data.get("content") or (f"<p>{article_body}</p>" if article_body else None),
                    "source_content": article_body,
                    "category": ai_data.get("category", feed_cat),
                    "subcategory": "India",
                    "badge": "Breaking" if feed_cat in ["politics", "india"] else None,
                    "image": publisher_image,
                    "image_url": publisher_image,
                    "source": publisher_name,
                    "source_name": publisher_name,
                    "source_domain": domain,
                    "source_url": resolved_url,
                    "canonical_source_url": canonical_source_url,
                    "canonical_url": f"{config.FRONTEND_URL}/news/{slug}",
                    "author": extracted.get("author") or publisher_name,
                    "published_at": pub_time,
                    "updated_at": now_iso,
                    "created_at": now_iso,
                    "language": "en",
                    "region": "IN",
                    "story_cluster_id": cluster_id,
                    "source_count": 1,
                    "sources": [{
                        "name": publisher_name,
                        "url": resolved_url,
                        "published_at": pub_time,
                        "domain": domain
                    }],
                    "key_facts": ai_data.get("key_facts", []),
                    "why_it_matters": ai_data.get("why_it_matters"),
                    "timeline": [],
                    "ai_summary": clean_summary if ai_data.get("ai_generated") else None,
                    "ai_generated": ai_data.get("ai_generated", False),
                    "ai_status": ai_data.get("ai_status", "skipped"),
                    "content_status": "published" if has_source_content else "source_unavailable",
                    "confidence": ai_data.get("confidence", "developing"),
                    "word_count": extracted.get("word_count", 0),
                }

                col.insert_one(doc)
                articles_saved += 1
                logger.info(f"[Scraper] Saved: '{article_title[:42]}' | Publisher: {publisher_name} | Image: {'OK' if publisher_image else 'NONE'}")

        scraper_stats["last_scrape_success"] = True
    except Exception as e:
        logger.error(f"[Scraper] Ingestion loop exception: {e}")
        scraper_stats["last_scrape_success"] = False
    finally:
        end_iso = datetime.now(timezone.utc).isoformat()
        scraper_stats["last_scrape_finished"] = end_iso
        scraper_stats["feeds_seen"] = feeds_seen
        scraper_stats["rss_entries_seen"] = rss_entries_seen
        scraper_stats["new_candidates"] = new_candidates
        scraper_stats["articles_saved"] = articles_saved
        scraper_stats["articles_skipped"] = articles_skipped
        scrape_lock.release()

    return {
        "status": "completed" if scraper_stats["last_scrape_success"] else "error",
        "feeds": feeds_seen,
        "entries": rss_entries_seen,
        "new": new_candidates,
        "saved": articles_saved,
        "skipped": articles_skipped,
    }

def background_scraper_loop():
    time.sleep(3)
    try:
        run_news_scraper()
    except Exception as e:
        logger.error(f"[Scheduler] Initial scrape exception: {e}")

    while True:
        time.sleep(config.SCRAPER_INTERVAL_SECONDS)
        try:
            run_news_scraper()
        except Exception as e:
            logger.error(f"[Scheduler] Recurring scrape exception: {e}")
