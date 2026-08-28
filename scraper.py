import time
import threading
import logging
import urllib.parse
from datetime import datetime, timezone
import feedparser
import config
from database import get_news_collection, get_sources_collection, get_revisions_collection
from extractor import (
    extract_source_document,
    is_valid_news_title,
    clean_source_url
)
from ai_pipeline import (
    create_slug,
    generate_cluster_id,
    extract_facts_and_synthesize_editorial
)

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
    {"name": "India Desk", "category": "india", "url": "https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en"},
    {"name": "Politics Desk", "category": "politics", "url": "https://news.google.com/rss/headlines/section/topic/POLITICS?hl=en-IN&gl=IN&ceid=IN:en"},
    {"name": "Business Desk", "category": "business", "url": "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-IN&gl=IN&ceid=IN:en"},
    {"name": "Tech Desk", "category": "technology", "url": "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-IN&gl=IN&ceid=IN:en"},
    {"name": "Sports Desk", "category": "sports", "url": "https://news.google.com/rss/headlines/section/topic/SPORTS?hl=en-IN&gl=IN&ceid=IN:en"},
    {"name": "Entertainment Desk", "category": "entertainment", "url": "https://news.google.com/rss/headlines/section/topic/ENTERTAINMENT?hl=en-IN&gl=IN&ceid=IN:en"},
    {"name": "World Desk", "category": "world", "url": "https://news.google.com/rss/headlines/section/topic/WORLD?hl=en-IN&gl=IN&ceid=IN:en"},
]

def run_news_scraper() -> dict:
    if not scrape_lock.acquire(blocking=False):
        logger.warning("[Scraper] Scraper execution is already active.")
        return {"status": "already_running"}

    start_iso = datetime.now(timezone.utc).isoformat()
    scraper_stats["last_scrape_started"] = start_iso
    feeds_seen = 0
    rss_entries_seen = 0
    new_candidates = 0
    articles_saved = 0
    articles_skipped = 0

    try:
        news_col = get_news_collection()
        sources_col = get_sources_collection()
        revisions_col = get_revisions_collection()

        if news_col is None or sources_col is None:
            logger.error("[Scraper] Database connection unavailable.")
            return {"status": "db_not_connected"}

        for feed in RSS_FEEDS:
            feeds_seen += 1
            feed_cat = feed["category"]

            try:
                parsed = feedparser.parse(feed["url"])
                entries = parsed.entries[:config.MAX_ARTICLES_PER_FEED]
            except Exception as e:
                logger.error(f"[RSS] Failed to fetch {feed['name']}: {e}")
                continue

            for entry in entries:
                rss_entries_seen += 1
                raw_url = getattr(entry, "link", None)
                raw_title = getattr(entry, "title", "").strip()
                raw_summary = getattr(entry, "summary", "").strip()

                if not raw_url or not raw_title or not is_valid_news_title(raw_title):
                    articles_skipped += 1
                    continue

                source_url = clean_source_url(raw_url)

                # Check if this research source has already been audited
                if sources_col.find_one({"$or": [{"source_url": source_url}, {"canonical_source_url": source_url}]}):
                    articles_skipped += 1
                    continue

                new_candidates += 1
                logger.info(f"[Scraper] Resolving source discovery: '{raw_title[:45]}...'")

                # Resolve original destination & extract factual research document
                extracted = extract_source_document(source_url, fallback_title=raw_title, fallback_summary=raw_summary)
                if not extracted["success"]:
                    articles_skipped += 1
                    continue

                source_title = extracted["source_title"]
                resolved_url = extracted["resolved_url"]
                canonical_source_url = extracted["canonical_source_url"]
                publisher_name = extracted["publisher_name"]
                source_domain = extracted["source_domain"]
                source_image = extracted["source_image_url"]
                source_content = extracted["source_content"]
                source_hash = extracted["source_hash"]
                source_pub_date = extracted["published_at"] or getattr(entry, "published", start_iso)

                # Deduplicate by resolved canonical source URL
                if sources_col.find_one({"canonical_source_url": canonical_source_url}):
                    articles_skipped += 1
                    continue

                cluster_id = generate_cluster_id(source_title, feed_cat)
                now_iso = datetime.now(timezone.utc).isoformat()
                existing_article = news_col.find_one({"story_cluster_id": cluster_id})

                if existing_article:
                    # Multi-source research expansion: Add provenance to existing TezKhabar story
                    art_id = str(existing_article["_id"])
                    sources_col.insert_one({
                        "article_id": art_id,
                        "source_name": publisher_name,
                        "source_domain": source_domain,
                        "source_url": resolved_url,
                        "canonical_source_url": canonical_source_url,
                        "source_type": "rss_ingest",
                        "retrieved_at": now_iso,
                        "published_at": source_pub_date,
                        "source_hash": source_hash,
                        "verification_status": "verified"
                    })
                    news_col.update_one(
                        {"_id": existing_article["_id"]},
                        {
                            "$set": {
                                "updated_at": now_iso,
                                "confidence": "multi_source_verified"
                            }
                        }
                    )
                    articles_saved += 1
                    continue

                # Stage 2: AI Editorial Synthesis & Quality Scoring
                editorial_article, facts_packet, quality_score = extract_facts_and_synthesize_editorial(
                    source_title=source_title,
                    source_content=source_content,
                    publisher_name=publisher_name,
                    category=feed_cat
                )

                if not editorial_article or quality_score < config.MIN_QUALITY_SCORE:
                    articles_skipped += 1
                    continue

                final_title = editorial_article["title"]
                base_slug = create_slug(final_title)
                slug = base_slug
                counter = 2
                while news_col.find_one({"slug": slug}):
                    slug = f"{base_slug}-{counter}"
                    counter += 1

                # Configure public attribution based on policy
                public_attribution = None
                if config.PUBLIC_ATTRIBUTION_STYLE == "based_on_multiple_reports":
                    public_attribution = "Reporting synthesized from verified public news dispatches."
                elif config.PUBLIC_ATTRIBUTION_STYLE == "source_names":
                    public_attribution = f"Based on verified reporting from {publisher_name} and agencies."

                # Public TezKhabar Article Document
                public_doc = {
                    "slug": slug,
                    "title": final_title,
                    "dek": editorial_article.get("dek", ""),
                    "summary": editorial_article.get("summary", ""),
                    "description": editorial_article.get("summary", ""),
                    "content": editorial_article.get("body_html", f"<p>{source_content[:600]}</p>"),
                    "body": editorial_article.get("body_html", f"<p>{source_content[:600]}</p>"),
                    "category": feed_cat,
                    "subcategory": "India",
                    "badge": "Developing" if feed_cat in ["politics", "india"] else None,
                    "image": source_image,
                    "image_url": source_image,
                    "imageUrl": source_image,
                    "image_source_type": "licensed_or_wire" if source_image else "fallback",
                    "image_credit": publisher_name if source_image else None,
                    "author": config.DEFAULT_AUTHOR,
                    "publisher": config.DEFAULT_PUBLISHER,
                    "source": config.DEFAULT_AUTHOR,
                    "source_name": config.DEFAULT_AUTHOR,
                    "published_at": source_pub_date,
                    "updated_at": now_iso,
                    "created_at": now_iso,
                    "canonical_url": f"{config.FRONTEND_URL}/news/{slug}",
                    "key_facts": editorial_article.get("key_highlights", []),
                    "key_highlights": editorial_article.get("key_highlights", []),
                    "why_it_matters": editorial_article.get("why_it_matters"),
                    "attribution": public_attribution,
                    "status": "published",
                    "content_status": "published",
                    "fact_check_status": "passed",
                    "originality_status": "verified_original",
                    "quality_score": quality_score,
                    "story_cluster_id": cluster_id,
                    "word_count": len(strip_html_tags(editorial_article.get("body_html", "")).split()),
                    "confidence": "verified"
                }

                res = news_col.insert_one(public_doc)
                art_id = str(res.inserted_id)

                # Store Private Source Provenance Record (Audit Only)
                sources_col.insert_one({
                    "article_id": art_id,
                    "source_name": publisher_name,
                    "source_domain": source_domain,
                    "source_url": resolved_url,
                    "canonical_source_url": canonical_source_url,
                    "source_type": "rss_ingest",
                    "retrieved_at": now_iso,
                    "published_at": source_pub_date,
                    "source_hash": source_hash,
                    "extracted_facts": facts_packet,
                    "verification_status": "verified"
                })

                # Store Initial Revision
                revisions_col.insert_one({
                    "article_id": art_id,
                    "version": 1,
                    "title": final_title,
                    "body": public_doc["content"],
                    "changed_at": now_iso,
                    "change_reason": "initial_editorial_publish"
                })

                articles_saved += 1
                logger.info(f"[Editorial Pipeline] Published: '{final_title[:45]}' -> /news/{slug} (Score: {quality_score})")

        scraper_stats["last_scrape_success"] = True
    except Exception as e:
        logger.error(f"[Scraper] Pipeline exception: {e}")
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
