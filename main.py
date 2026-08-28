import os
import sys
import threading
import logging
import urllib.parse
from typing import Optional
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import xml.sax.saxutils

import uvicorn
from fastapi import FastAPI, HTTPException, Header, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response as PlainResponse

import config
from database import init_db, get_db_health, get_news_collection
from schemas import (
    NewsListResponse,
    SingleArticleResponse,
    CategoryCountItem,
    AdminStatsResponse,
    ArticleDocument,
    PaginationMetadata,
)
from scraper import run_news_scraper, background_scraper_loop, scraper_stats

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("tezkhabar.main")

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("[Startup] Connecting to MongoDB and verifying collections...")
    db_connected = init_db()
    
    if db_connected:
        scraper_thread = threading.Thread(target=background_scraper_loop, daemon=True)
        scraper_thread.start()
        logger.info("[Startup] Background news ingestion worker started.")
    else:
        logger.warning("[Startup] Degraded mode: MongoDB connection pending.")
    yield
    logger.info("[Shutdown] Cleaning up services...")

app = FastAPI(
    title="TezKhabar News Intelligence API",
    description="Production editorial news ingestion, clustering and article routing engine.",
    version="6.1.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

def clean_doc(doc: dict) -> dict:
    if not doc:
        return {}
    if "_id" in doc:
        doc["id"] = str(doc["_id"])
        doc["_id"] = str(doc["_id"])
    
    slug = str(doc.get("slug") or doc.get("id") or "news-story")
    title = str(doc.get("title") or "News Story")
    summary = str(doc.get("summary") or doc.get("description") or doc.get("dek") or title)
    dek = str(doc.get("dek") or summary[:140])
    source_name = str(doc.get("source_name") or doc.get("source") or "TezKhabar Wire")
    pub_date = str(doc.get("published_at") or doc.get("created_at") or datetime.now(timezone.utc).isoformat())

    doc["slug"] = slug
    doc["title"] = title
    doc["dek"] = dek
    doc["summary"] = summary
    doc["description"] = summary
    doc["content"] = str(doc.get("content") or f"<p>{summary}</p>")
    doc["category"] = str(doc.get("category") or "india").lower()
    doc["subcategory"] = str(doc.get("subcategory") or "India")
    doc["source"] = source_name
    doc["source_name"] = source_name
    doc["source_domain"] = doc.get("source_domain")
    doc["source_url"] = str(doc.get("source_url") or "#")
    doc["published_at"] = pub_date
    doc["created_at"] = str(doc.get("created_at") or pub_date)
    doc["canonical_url"] = str(doc.get("canonical_url") or f"{config.FRONTEND_URL}/news/{slug}")
    doc["content_status"] = "published"
    doc["sources"] = doc.get("sources") if isinstance(doc.get("sources"), list) else []
    doc["key_facts"] = doc.get("key_facts") if isinstance(doc.get("key_facts"), list) else []
    doc["timeline"] = doc.get("timeline") if isinstance(doc.get("timeline"), list) else []
    doc["source_count"] = int(doc.get("source_count") or max(len(doc["sources"]), 1))
    doc["ai_generated"] = bool(doc.get("ai_generated", False))
    doc["confidence"] = str(doc.get("confidence") or "developing")
    return doc

# ==========================================
# HEALTH & SERVICE STATUS
# ==========================================
@app.get("/", tags=["Health"])
def root_info():
    return {
        "status": "ok",
        "service": "TezKhabar Backend Engine",
        "version": "6.1.0",
        "frontend": config.FRONTEND_URL,
        "database": get_db_health()
    }

@app.get("/health", tags=["Health"])
def health_check():
    db_status = get_db_health()
    if db_status != "connected":
        return JSONResponse(status_code=503, content={"status": "degraded", "database": "disconnected"})
    return {
        "status": "ok",
        "service": "tezkhabar-backend",
        "database": "connected",
        "version": "6.1.0"
    }

# ==========================================
# PUBLIC NEWS APIS
# ==========================================
@app.get("/api/news", response_model=NewsListResponse, tags=["News"])
def get_news_list(
    category: Optional[str] = Query(None, description="Category filter"),
    page: int = Query(1, ge=1),
    limit: int = Query(15, ge=1, le=50),
    sort: str = Query("latest")
):
    col = get_news_collection()
    if col is None:
        return NewsListResponse(items=[], pagination=PaginationMetadata(page=page, limit=limit, total=0, has_next=False))

    query = {"content_status": "published"}
    if category:
        query["category"] = category.lower()

    try:
        total = col.count_documents(query)
        skip = (page - 1) * limit
        cursor = col.find(query).sort("published_at", -1).skip(skip).limit(limit)
        items = [ArticleDocument(**clean_doc(d)) for d in cursor]
        has_next = (skip + limit) < total
    except Exception as e:
        logger.error(f"[News List Error]: {e}")
        return NewsListResponse(items=[], pagination=PaginationMetadata(page=page, limit=limit, total=0, has_next=False))

    return NewsListResponse(
        items=items,
        pagination=PaginationMetadata(page=page, limit=limit, total=total, has_next=has_next)
    )

def lookup_article_by_slug(raw_slug: str):
    col = get_news_collection()
    if col is None:
        raise HTTPException(status_code=503, detail="Database temporarily unavailable")

    safe_slug = urllib.parse.unquote(raw_slug).strip().lower()

    try:
        doc = col.find_one({"slug": safe_slug})
        if not doc:
            # Case-insensitive / ID fallback
            doc = col.find_one({"slug": {"$regex": f"^{re.escape(safe_slug)}$", "$options": "i"}})
        if not doc:
            doc = col.find_one({"_id": safe_slug})
    except Exception as e:
        logger.error(f"[Article Lookup Error] Slug '{safe_slug}': {e}")
        raise HTTPException(status_code=500, detail="Internal database error")

    if not doc:
        logger.info(f"[ArticleAPI] 404 Not Found for slug: '{safe_slug}'")
        return JSONResponse(status_code=404, content={"detail": "Article not found", "slug": safe_slug})

    logger.info(f"[ArticleAPI] Found article for slug: '{safe_slug}'")
    return {"article": ArticleDocument(**clean_doc(doc)).model_dump()}

@app.get("/api/news/{slug}", tags=["News"])
def get_article_by_slug(slug: str):
    return lookup_article_by_slug(slug)

@app.get("/api/articles/{slug}", tags=["News"])
def get_article_by_slug_compat(slug: str):
    return lookup_article_by_slug(slug)

@app.get("/api/latest", response_model=NewsListResponse, tags=["News"])
def get_latest_news_wire(limit: int = Query(15, ge=1, le=50)):
    return get_news_list(category=None, page=1, limit=limit, sort="latest")

@app.get("/api/trending", response_model=NewsListResponse, tags=["Trending"])
def get_trending_news(limit: int = Query(10, ge=1, le=30)):
    col = get_news_collection()
    if col is None:
        return NewsListResponse(items=[], pagination=PaginationMetadata(page=1, limit=limit, total=0, has_next=False))

    try:
        cursor = col.find({"content_status": "published"}).sort([("source_count", -1), ("published_at", -1)]).limit(limit)
        items = [ArticleDocument(**clean_doc(d)) for d in cursor]
    except Exception:
        items = []

    return NewsListResponse(
        items=items,
        pagination=PaginationMetadata(page=1, limit=limit, total=len(items), has_next=False)
    )

@app.get("/api/categories", response_model=list[CategoryCountItem], tags=["Categories"])
def get_categories():
    col = get_news_collection()
    if col is None:
        return []

    try:
        pipeline = [
            {"$group": {"_id": "$category", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}}
        ]
        results = list(col.aggregate(pipeline))
        return [CategoryCountItem(name=str(r["_id"]).title(), slug=str(r["_id"]), count=r["count"]) for r in results if r.get("_id")]
    except Exception:
        return []

@app.get("/api/search", response_model=NewsListResponse, tags=["Search"])
def search_articles(
    q: str = Query(..., min_length=1, max_length=100),
    category: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(15, ge=1, le=50)
):
    col = get_news_collection()
    if col is None or not q.strip():
        return NewsListResponse(items=[], pagination=PaginationMetadata(page=page, limit=limit, total=0, has_next=False))

    try:
        regex_query = {"$regex": q.strip(), "$options": "i"}
        match_condition = {
            "content_status": "published",
            "$or": [{"title": regex_query}, {"summary": regex_query}, {"dek": regex_query}]
        }
        if category:
            match_condition["category"] = category.lower()

        total = col.count_documents(match_condition)
        skip = (page - 1) * limit
        cursor = col.find(match_condition).sort("published_at", -1).skip(skip).limit(limit)
        items = [ArticleDocument(**clean_doc(d)) for d in cursor]
        has_next = (skip + limit) < total
    except Exception:
        return NewsListResponse(items=[], pagination=PaginationMetadata(page=page, limit=limit, total=0, has_next=False))

    return NewsListResponse(
        items=items,
        pagination=PaginationMetadata(page=page, limit=limit, total=total, has_next=has_next)
    )

def verify_admin(x_admin_key: Optional[str] = Header(None)):
    if not x_admin_key or x_admin_key != config.ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid Admin Credentials")

@app.post("/api/admin/scrape-now", tags=["Admin"])
def trigger_manual_scrape(x_admin_key: Optional[str] = Header(None)):
    verify_admin(x_admin_key)
    res = run_news_scraper()
    return {"message": "Scrape operation completed", "result": res}

@app.get("/api/admin/stats", response_model=AdminStatsResponse, tags=["Admin"])
def get_admin_metrics(x_admin_key: Optional[str] = Header(None)):
    verify_admin(x_admin_key)
    col = get_news_collection()
    if col is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        total_articles = col.count_documents({})
        pipeline = [{"$group": {"_id": "$category", "count": {"$sum": 1}}}]
        category_counts = [{"category": r["_id"], "count": r["count"]} for r in col.aggregate(pipeline)]
    except Exception:
        total_articles = 0
        category_counts = []

    return AdminStatsResponse(
        total_articles=total_articles,
        articles_today=scraper_stats.get("articles_saved", 0),
        articles_last_24h=scraper_stats.get("articles_saved", 0),
        last_scrape_started=scraper_stats.get("last_scrape_started"),
        last_scrape_finished=scraper_stats.get("last_scrape_finished"),
        last_scrape_success=scraper_stats.get("last_scrape_success", True),
        articles_discovered=scraper_stats.get("rss_entries_seen", 0),
        articles_saved=scraper_stats.get("articles_saved", 0),
        articles_skipped=scraper_stats.get("articles_skipped", 0),
        category_counts=category_counts
    )

@app.get("/sitemap.xml", response_class=PlainResponse, tags=["SEO"])
def get_main_sitemap():
    col = get_news_collection()
    if col is None:
        return PlainResponse("<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'></urlset>", media_type="application/xml")

    articles = list(col.find({"content_status": "published"}).sort("published_at", -1).limit(100))
    xml_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        f'  <url><loc>{config.FRONTEND_URL}</loc><changefreq>always</changefreq><priority>1.0</priority></url>'
    ]

    for cat in config.CONTROLLED_CATEGORIES:
        xml_lines.append(f'  <url><loc>{config.FRONTEND_URL}/category/{cat}</loc><changefreq>hourly</changefreq><priority>0.8</priority></url>')

    for a in articles:
        slug = xml.sax.saxutils.escape(a.get("slug", ""))
        xml_lines.append(f'  <url><loc>{config.FRONTEND_URL}/news/{slug}</loc><changefreq>never</changefreq><priority>0.9</priority></url>')

    xml_lines.append('</urlset>')
    return PlainResponse("\n".join(xml_lines), media_type="application/xml")

@app.get("/news-sitemap.xml", response_class=PlainResponse, tags=["SEO"])
def get_news_sitemap():
    col = get_news_collection()
    if col is None:
        return PlainResponse("<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'></urlset>", media_type="application/xml")

    articles = list(col.find({"content_status": "published"}).sort("published_at", -1).limit(50))
    xml_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">'
    ]

    for a in articles:
        slug = xml.sax.saxutils.escape(a.get("slug", ""))
        title = xml.sax.saxutils.escape(a.get("title", ""))
        pub_date = a.get("published_at", datetime.now(timezone.utc).isoformat())
        xml_lines.append('  <url>')
        xml_lines.append(f'    <loc>{config.FRONTEND_URL}/news/{slug}</loc>')
        xml_lines.append('    <news:news>')
        xml_lines.append('      <news:publication><news:name>TezKhabar</news:name><news:language>en</news:language></news:publication>')
        xml_lines.append(f'      <news:publication_date>{pub_date}</news:publication_date>')
        xml_lines.append(f'      <news:title>{title}</news:title>')
        xml_lines.append('    </news:news>')
        xml_lines.append('  </url>')

    xml_lines.append('</urlset>')
    return PlainResponse("\n".join(xml_lines), media_type="application/xml")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, log_level="info")
