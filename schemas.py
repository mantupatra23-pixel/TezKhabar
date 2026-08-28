from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field

# ==========================================
# PUBLIC EDITORIAL SCHEMAS (Frontend Consumed)
# ==========================================
class PublicArticleItem(BaseModel):
    id: str
    slug: str
    title: str
    dek: str
    summary: str
    description: str
    content: Optional[str] = None
    body: Optional[str] = None
    category: str
    subcategory: Optional[str] = "India"
    badge: Optional[str] = None
    image: Optional[str] = None
    image_url: Optional[str] = None
    imageUrl: Optional[str] = None
    image_source_type: str = "editorial"
    image_credit: Optional[str] = None
    author: str = "TezKhabar Editorial Desk"
    publisher: str = "TezKhabar"
    source: str = "TezKhabar Editorial Desk"
    source_name: str = "TezKhabar Editorial Desk"
    published_at: str
    publishedAt: str
    updated_at: Optional[str] = None
    updatedAt: Optional[str] = None
    created_at: str
    createdAt: str
    canonical_url: str
    key_facts: List[str] = []
    keyFacts: List[str] = []
    key_highlights: List[str] = []
    why_it_matters: Optional[str] = None
    attribution: Optional[str] = None
    word_count: int = 0
    confidence: str = "verified"

class PaginationMetadata(BaseModel):
    page: int
    limit: int
    total: int
    has_next: bool

class PublicNewsListResponse(BaseModel):
    items: List[PublicArticleItem]
    pagination: PaginationMetadata

class PublicSingleArticleResponse(BaseModel):
    article: PublicArticleItem

class CategoryCountItem(BaseModel):
    name: str
    slug: str
    count: int

# ==========================================
# PRIVATE PROVENANCE / AUDIT SCHEMAS
# ==========================================
class SourceProvenanceItem(BaseModel):
    source_name: str
    source_domain: str
    source_url: str
    canonical_source_url: Optional[str] = None
    retrieved_at: str
    published_at: Optional[str] = None
    source_type: str = "rss_ingest"
    source_hash: Optional[str] = None
    verification_status: str = "verified"

class ArticleProvenanceResponse(BaseModel):
    article_id: str
    slug: str
    title: str
    author: str
    published_at: str
    quality_score: int
    fact_check_status: str
    originality_status: str
    sources: List[SourceProvenanceItem]
    extracted_facts: Dict[str, Any]
    revisions: List[Dict[str, Any]]

class AdminStatsResponse(BaseModel):
    total_articles: int
    articles_today: int
    articles_last_24h: int
    last_scrape_started: Optional[str]
    last_scrape_finished: Optional[str]
    last_scrape_success: bool
    articles_discovered: int
    articles_saved: int
    articles_skipped: int
    category_counts: List[dict]
