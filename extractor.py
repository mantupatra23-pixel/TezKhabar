import re
import html
import socket
import ipaddress
import urllib.parse
import requests
from bs4 import BeautifulSoup
import config

SSRF_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

GENERIC_IMAGE_PATTERNS = [
    "lh3.googleusercontent.com",
    "gstatic.com",
    "google.com/logos",
    "news.google.com/api/attachments",
    "default_news",
    "placeholder",
    "fallback",
    "1x1",
    "pixel",
    "favicon",
    "logo_google",
    "feed-icon",
    "icon.png",
    "spacer.gif"
]

def is_safe_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = parsed.hostname
        if not hostname or hostname in ("localhost", "127.0.0.1", "0.0.0.0"):
            return False
        
        ip_str = socket.gethostbyname(hostname)
        ip = ipaddress.ip_address(ip_str)
        for blocked in SSRF_BLOCKED_NETWORKS:
            if ip in blocked:
                return False
        return True
    except Exception:
        return False

def is_valid_article_image(url: Optional[str] = None) -> bool:
    if not url or not isinstance(url, str):
        return False
    if not url.startswith("http"):
        return False
    
    lower_url = url.lower()
    for pattern in GENERIC_IMAGE_PATTERNS:
        if pattern in lower_url:
            return False
            
    # Check for image file extension or standard CDN image delivery
    valid_extensions = (".jpg", ".jpeg", ".png", ".webp", ".avif")
    path = urllib.parse.urlparse(url).path.lower()
    if any(path.endswith(ext) for ext in valid_extensions) or "image" in lower_url or "img" in lower_url or "photo" in lower_url:
        return True

    return True

def strip_html_tags(raw_html: str = "") -> str:
    if not raw_html:
        return ""
    # Strip HTML tags
    clean = re.sub(r"<[^>]*>", " ", raw_html)
    # Unescape HTML entities (&nbsp;, &amp;, etc.)
    clean = html.unescape(clean)
    # Collapse multiple whitespaces
    return re.sub(r"\s+", " ", clean).strip()

def clean_source_url(url: str) -> str:
    try:
        parsed = urllib.parse.urlparse(url)
        clean_qs = []
        for k, v in urllib.parse.parse_qsl(parsed.query):
            if not k.startswith("utm_") and k not in ("fbclid", "gclid", "ref", "oc"):
                clean_qs.append((k, v))
        new_query = urllib.parse.urlencode(clean_qs)
        return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, ""))
    except Exception:
        return url

def parse_rss_title_and_source(raw_title: str, default_source: str = "TezKhabar Wire") -> tuple[str, str]:
    """
    Google News format: 'Headline of the Story - Publisher Name'
    Extracts clean headline and real publisher.
    """
    clean_title = strip_html_tags(raw_title)
    if " - " in clean_title:
        parts = clean_title.rsplit(" - ", 1)
        headline = parts[0].strip()
        publisher = parts[1].strip()
        if len(publisher) >= 2 and publisher.lower() != "google news":
            return headline, publisher
    return clean_title, default_source

def extract_image_from_entry(entry: Any) -> Optional[str]:
    # 1. Media content
    if hasattr(entry, "media_content") and entry.media_content:
        for media in entry.media_content:
            url = media.get("url")
            if is_valid_article_image(url):
                return url

    # 2. Media thumbnail
    if hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
        for media in entry.media_thumbnail:
            url = media.get("url")
            if is_valid_article_image(url):
                return url

    # 3. Enclosures
    if hasattr(entry, "enclosures") and entry.enclosures:
        for enc in entry.enclosures:
            url = enc.get("href")
            if is_valid_article_image(url):
                return url

    # 4. Images inside content / summary HTML
    html_content = ""
    if hasattr(entry, "content") and entry.content:
        html_content = entry.content[0].get("value", "")
    elif hasattr(entry, "summary"):
        html_content = entry.summary or ""

    if html_content:
        soup = BeautifulSoup(html_content, "html.parser")
        for img in soup.find_all("img"):
            src = img.get("src")
            if is_valid_article_image(src):
                return src

    return None

def extract_article(url: str, fallback_title: str = "", fallback_summary: str = "") -> dict:
    fallback_summary_clean = strip_html_tags(fallback_summary)
    fallback_title_clean = strip_html_tags(fallback_title)

    if not is_safe_url(url):
        return {
            "success": True if len(fallback_summary_clean.split()) >= 10 else False,
            "title": fallback_title_clean,
            "description": fallback_summary_clean,
            "body": fallback_summary_clean,
            "image_url": None,
            "published_at": None,
            "author": None,
            "word_count": len(fallback_summary_clean.split()),
        }

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 TezKhabarEditorial/6.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=5, allow_redirects=True)
        if resp.status_code >= 400:
            return {
                "success": True if len(fallback_summary_clean.split()) >= 10 else False,
                "title": fallback_title_clean,
                "description": fallback_summary_clean,
                "body": fallback_summary_clean,
                "image_url": None,
                "published_at": None,
                "author": None,
                "word_count": len(fallback_summary_clean.split()),
            }

        soup = BeautifulSoup(resp.content, "html.parser")

        og_title = soup.find("meta", property="og:title")
        og_desc = soup.find("meta", property="og:description")
        og_img = soup.find("meta", property="og:image") or soup.find("meta", {"name": "twitter:image"})
        meta_pub = soup.find("meta", property="article:published_time") or soup.find("meta", {"name": "pubdate"})
        meta_author = soup.find("meta", {"name": "author"})

        title = og_title["content"].strip() if og_title and og_title.get("content") else (soup.title.string.strip() if soup.title and soup.title.string else fallback_title_clean)
        description = og_desc["content"].strip() if og_desc and og_desc.get("content") else fallback_summary_clean
        
        extracted_img = og_img["content"].strip() if og_img and og_img.get("content") else None
        valid_img = extracted_img if is_valid_article_image(extracted_img) else None

        published_at = meta_pub["content"].strip() if meta_pub and meta_pub.get("content") else None
        author = meta_author["content"].strip() if meta_author and meta_author.get("content") else None

        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "iframe", "noscript"]):
            tag.decompose()

        paragraphs = [p.get_text().strip() for p in soup.find_all("p") if len(p.get_text().strip()) > 35]
        body_text = " ".join(paragraphs) if paragraphs else description

        if len(body_text.split()) < 15 and len(fallback_summary_clean.split()) >= 15:
            body_text = fallback_summary_clean

        return {
            "success": True,
            "title": strip_html_tags(title or fallback_title_clean),
            "description": strip_html_tags(description or body_text[:200]),
            "body": strip_html_tags(body_text[:3500]),
            "image_url": valid_img,
            "published_at": published_at,
            "author": author,
            "word_count": len(body_text.split()),
        }
    except Exception:
        return {
            "success": True if len(fallback_summary_clean.split()) >= 10 else False,
            "title": fallback_title_clean,
            "description": fallback_summary_clean,
            "body": fallback_summary_clean,
            "image_url": None,
            "published_at": None,
            "author": None,
            "word_count": len(fallback_summary_clean.split()),
        }
