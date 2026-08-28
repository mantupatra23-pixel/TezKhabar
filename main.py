import os
import sys
import threading
import logging
import urllib.parse
from typing import Optional, Dict, Any
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import xml.sax.saxutils

import uvicorn
from fastapi import FastAPI, HTTPException, Header, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response as PlainResponse

import config
from database import (
    init_db,
    get_db_health,
    get_news_collection,
    get_sources_collection,
    get_revisions_collection
)
from schemas import (
    PublicNewsListResponse,
    PublicSingleArticleResponse,
    PublicArticleItem,
    PaginationMetadata,
    CategoryCountItem,
    ArticleProvenanceResponse,
    SourceProvenanceItem,
    AdminStatsResponse
)
from scraper import run_news_scraper, background_scraper_loop, scraper_stats

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("tezkhabar.main")

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("[Startup] Connecting to MongoDB and verifying editorial collections...")
    db_connected = init_db()
    if db_connected:
        scraper_thread = threading.Thread(target=background_scraper_loop, daemon=True)
        scraper_thread.start()
        logger.info("[Startup] Background news ingestion & editorial worker started.")
    yield
    logger.info("[Shutdown] Cleaning up TezKhabar services...")

app = FastAPI(
    title="TezKhabar Editorial Wire API",
    description="Original editorial news synthesis and verified journalism engine.",
    version="7.0.0",
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

def clean_public_article(doc: dict) -> PublicArticleItem:
    art_id = str(doc.get("_id") or doc.get("id") or "")
    slug = str(doc.get("slug") or art_id)
    title = str(doc.get("title") or "Editorial Report")
    summary = str(doc.get("summary") or doc.get("description") or doc.get("dek") or title)
    dek = str(doc.get("dek") or summary[:140])
    content = doc.get("content") or doc.get("body") or f"<p>{summary}</p>"
    pub_date = str(doc.get("published_at") or doc.get("created_at") or datetime.now(timezone.utc).isoformat())

    img = doc.get("image_url") or doc.get("image") or doc.get("imageUrl")

    return PublicArticleItem(
        id=art_id,
        slug=slug,
        title=title,
        dek=dek,
        summary=summary,
        description=summary,
        content=content,
        body=content,
        category=str(doc.get("category") or "india").lower(),
        subcategory=str(doc.get("subcategory") or "India"),
        badge=doc.get("badge"),
        image=img,
        image_url=img,
        imageUrl=img,
        image_source_type=doc.get("image_source_type", "editorial"),
        image_credit=doc.get("image_credit"),
        author=config.DEFAULT_AUTHOR,
        publisher=config.DEFAULT_PUBLISHER,
        source=config.DEFAULT_AUTHOR,
        source_name=config.DEFAULT_AUTHOR,
        published_at=pub_date,
        publishedAt=pub_date,
        updated_at=doc.get("updated_at"),
        updatedAt=doc.get("updated_at"),
        created_at=str(doc.get("created_at") or pub_date),
        createdAt=str(doc.get("created_at") or pub_date),
        canonical_url=f"{config.FRONTEND_URL}/news/{slug}",
        key_facts=doc.get("key_facts") or doc.get("key_highlights") or [],
        keyFacts=doc.get("key_facts") or doc.get("key_highlights") or [],
        key_highlights=doc.get("key_highlights") or doc.get("key_facts") or [],
        why_it_matters=doc.get("why_it_matters"),
        attribution=doc.get("attribution"),
        word_count=int(doc.get("word_count") or 0),
        confidence=str(doc.get("confidence") or "verified")
    )

# ==========================================
# HEALTH & SERVICE STATUS
# ==========================================
@app.get("/", tags=["Health"])
def root_info():
    return {
        "status": "ok",
        "service": "TezKhabar Editorial Wire Engine",
        "version": "7.0.0",
        "publisher": config.DEFAULT_PUBLISHER,
        "author": config.DEFAULT_AUTHOR,
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
        "ai": "ok" if config.GROQ_API_KEY else "disabled",
        "version": "7.0.0"
    }

# ==========================================
# PUBLIC NEWS APIS (No External URLs Leaked)
# ==========================================
@app.get("/api/news", response_model=PublicNewsListResponse, tags=["News"])
def get_public_news_list(
    category: Optional[str] = Query(None, description="Category filter"),
    page: int = Query(1, ge=1),
    limit: int = Query(15, ge=1, le=50),
    sort: str = Query("latest")
):
    col = get_news_collection()
    if col is None:
        return PublicNewsListResponse(items=[], pagination=PaginationMetadata(page=page, limit=limit, total=0, has_next=False))

    query = {"content_status": "published"}
    if category:
        query["category"] = category.lower()

    try:
        total = col.count_documents(query)
        skip = (page - 1) * limit
        cursor = col.find(query).sort("published_at", -1).skip(skip).limit(limit)
        items = [clean_public_article(d) for d in cursor]
        has_next = (skip + limit) < total
    except Exception as e:
        logger.error(f"[News List Error]: {e}")
        return PublicNewsListResponse(items=[], pagination=PaginationMetadata(page=page, limit=limit, total=0, has_next=False))

    return PublicNewsListResponse(
        items=items,
        pagination=PaginationMetadata(page=page, limit=limit, total=total, has_next=has_next)
    )

def lookup_public_article(raw_slug: str):
    col = get_news_collection()
    if col is None:
        raise HTTPException(status_code=503, detail="Database temporarily unavailable")

    safe_slug = urllib.parse.unquote(raw_slug).strip().lower()

    try:
        doc = col.find_one({"slug": safe_slug, "content_status": "published"})
        if not doc:
            doc = col.find_one({"_id": safe_slug, "content_status": "published"})
    except Exception as e:
        logger.error(f"[Article Lookup Error] Slug '{safe_slug}': {e}")
        raise HTTPException(status_code=500, detail="Internal database error")

    if not doc:
        return JSONResponse(status_code=404, content={"detail": "Article not found", "slug": safe_slug})

    return {"article": clean_public_article(doc).model_dump()}

@app.get("/api/news/{slug}", tags=["News"])
def get_article_by_slug(slug: str):
    return lookup_public_article(slug)

@app.get("/api/articles/{slug}", tags=["News"])
def get_article_by_slug_compat(slug: str):
    return lookup_public_article(slug)

@app.get("/api/latest", response_model=PublicNewsListResponse, tags=["News"])
def get_latest_news_wire(limit: int = Query(15, ge=1, le=50)):
    return get_public_news_list(category=None, page=1, limit=limit, sort="latest")

@app.get("/api/trending", response_model=PublicNewsListResponse, tags=["Trending"])
def get_trending_news(limit: int = Query(10, ge=1, le=30)):
    col = get_news_collection()
    if col is None:
        return PublicNewsListResponse(items=[], pagination=PaginationMetadata(page=1, limit=limit, total=0, has_next=False))

    try:
        cursor = col.find({"content_status": "published"}).sort("published_at", -1).limit(limit)
        items = [clean_public_article(d) for d in cursor]
    except Exception:
        items = []

    return PublicNewsListResponse(
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
            {"$match": {"content_status": "published"}},
            {"$group": {"_id": "$category", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}}
        ]
        results = list(col.aggregate(pipeline))
        return [CategoryCountItem(name=str(r["_id"]).title(), slug=str(r["_id"]), count=r["count"]) for r in results if r.get("_id")]
    except Exception:
        return []

@app.get("/api/search", response_model=PublicNewsListResponse, tags=["Search"])
def search_articles(
    q: str = Query(..., min_length=1, max_length=100),
    category: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(15, ge=1, le=50)
):
    col = get_news_collection()
    if col is None or not q.strip():
        return PublicNewsListResponse(items=[], pagination=PaginationMetadata(page=page, limit=limit, total=0, has_next=False))

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
        items = [clean_public_article(d) for d in cursor]
        has_next = (skip + limit) < total
    except Exception:
        return PublicNewsListResponse(items=[], pagination=PaginationMetadata(page=page, limit=limit, total=0, has_next=False))

    return PublicNewsListResponse(
        items=items,
        pagination=PaginationMetadata(page=page, limit=limit, total=total, has_next=has_next)
    )

# ==========================================
# PROTECTED PROVENANCE AUDIT & ADMIN
# ==========================================
def verify_admin(x_admin_key: Optional[str] = Header(None)):
    if not x_admin_key or x_admin_key != config.ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid Admin Credentials")

@app.get("/api/admin/articles/{identifier}/provenance", response_model=ArticleProvenanceResponse, tags=["Admin"])
def get_article_provenance(identifier: str, x_admin_key: Optional[str] = Header(None)):
    verify_admin(x_admin_key)
    news_col = get_news_collection()
    sources_col = get_sources_collection()
    revisions_col = get_revisions_collection()

    doc = news_col.find_one({"$or": [{"slug": identifier}, {"_id": identifier}]})
    if not doc:
        raise HTTPException(status_code=404, detail="Article not found")

    art_id = str(doc["_id"])
    sources = list(sources_col.find({"article_id": art_id}))
    revisions = list(revisions_col.find({"article_id": art_id}))

    source_items = []
    extracted_facts = {}
    for s in sources:
        source_items.append(SourceProvenanceItem(
            source_name=s.get("source_name", "Unknown"),
            source_domain=s.get("source_domain", ""),
            source_url=s.get("source_url", ""),
            canonical_source_url=s.get("canonical_source_url"),
            retrieved_at=s.get("retrieved_at", ""),
            published_at=s.get("published_at"),
            source_type=s.get("source_type", "rss_ingest"),
            source_hash=s.get("source_hash"),
            verification_status=s.get("verification_status", "verified")
        ))
        if s.get("extracted_facts"):
            extracted_facts = s["extracted_facts"]

    for r in revisions:
        r["_id"] = str(r["_id"])

    return ArticleProvenanceResponse(
        article_id=art_id,
        slug=doc["slug"],
        title=doc["title"],
        author=doc.get("author", config.DEFAULT_AUTHOR),
        published_at=doc.get("published_at", ""),
        quality_score=doc.get("quality_score", 80),
        fact_check_status=doc.get("fact_check_status", "passed"),
        originality_status=doc.get("originality_status", "verified_original"),
        sources=source_items,
        extracted_facts=extracted_facts,
        revisions=revisions
    )

@app.post("/api/admin/scrape-now", tags=["Admin"])
def trigger_manual_scrape(x_admin_key: Optional[str] = Header(None)):
    verify_admin(x_admin_key)
    res = run_news_scraper()
    return {"message": "Editorial ingestion run complete", "result": res}

@app.get("/api/admin/stats", response_model=AdminStatsResponse, tags=["Admin"])
def get_admin_metrics(x_admin_key: Optional[str] = Header(None)):
    verify_admin(x_admin_key)
    col = get_news_collection()
    if col is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        total_articles = col.count_documents({"content_status": "published"})
        pipeline = [
            {"$match": {"content_status": "published"}},
            {"$group": {"_id": "$category", "count": {"$sum": 1}}}
        ]
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

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, log_level="info")
