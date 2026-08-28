from typing import List, Optional, Any
from pydantic import BaseModel, Field

class NewsSourceItem(BaseModel):
    name: str
    url: Optional[str] = None
    published_at: Optional[str] = None
    domain: Optional[str] = None
    stance: Optional[str] = None

class ArticleDocument(BaseModel):
    slug: str
    title: str
    dek: str = ""
    summary: str = ""
    content: str = ""
    category: str = "india"
    subcategory: Optional[str] = None
    badge: Optional[str] = None
    image_url: Optional[str] = None
    source_url: str
    source_name: str
    source_domain: str
    author: Optional[str] = None
    published_at: str
    updated_at: Optional[str] = None
    created_at: str
    language: str = "en"
    region: str = "IN"
    story_cluster_id: str
    source_count: int = 1
    sources: List[NewsSourceItem] = []
    key_facts: List[str] = []
    why_it_matters: Optional[str] = None
    timeline: List[dict] = []
    ai_summary: Optional[str] = None
    ai_generated: bool = True
    content_status: str = "published"
    confidence: str = "developing"
    canonical_source_url: Optional[str] = None
    canonical_url: str
    word_count: int = 0

class PaginationMetadata(BaseModel):
    page: int
    limit: int
    total: int
    has_next: bool

class NewsListResponse(BaseModel):
    items: List[ArticleDocument]
    pagination: PaginationMetadata

class SingleArticleResponse(BaseModel):
    article: ArticleDocument

class CategoryCountItem(BaseModel):
    name: str
    slug: str
    count: int

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
