import re
import html
import json
import socket
import ipaddress
import urllib.parse
from typing import Optional, Tuple, Dict, Any, List
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

FORBIDDEN_TITLES = {
    "google news",
    "google news politics",
    "google news business",
    "google news technology",
    "google news sports",
    "google news india",
    "google news finance",
    "google news entertainment",
    "google news ai",
    "google news world",
}

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
    "spacer.gif",
    "avatar",
    "share-icon",
    "tezkhabar"
]

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 TezKhabarEditorial/6.2",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
}

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

def strip_html_tags(raw_html: str = "") -> str:
    if not raw_html:
        return ""
    clean = re.sub(r"<[^>]*>", " ", str(raw_html))
    clean = html.unescape(clean)
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

def is_valid_news_title(title: str) -> bool:
    if not title or len(title.strip()) < 10:
        return False
    clean = title.strip().lower()
    if clean in FORBIDDEN_TITLES:
        return False
    if clean.startswith("google news") and len(clean.split()) <= 4:
        return False
    return True

def resolve_publisher_url(source_url: str) -> str:
    """
    Unmasks Google News RSS wrapper links to identify the original publisher page.
    """
    if "news.google.com" not in source_url:
        return source_url
    if not is_safe_url(source_url):
        return source_url

    try:
        session = requests.Session()
        session.max_redirects = 5
        resp = session.get(source_url, headers=HTTP_HEADERS, timeout=6, allow_redirects=True)
        final_url = resp.url
        if "news.google.com" not in final_url:
            return clean_source_url(final_url)

        soup = BeautifulSoup(resp.content, "html.parser")
        canonical = soup.find("link", rel="canonical")
        if canonical and canonical.get("href") and "news.google.com" not in canonical["href"]:
            return clean_source_url(canonical["href"])

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("http") and "google.com" not in href:
                return clean_source_url(href)

        return clean_source_url(final_url)
    except Exception:
        return source_url

def validate_and_normalize_image(image_url: Optional[str], base_url: str = "") -> Optional[str]:
    """
    Performs HTTP HEAD/GET verification on candidate image URL.
    Rejects generic Google News icons, SVG icons, and tracking beacons.
    """
    if not image_url or not isinstance(image_url, str):
        return None

    clean_url = urllib.parse.urljoin(base_url, image_url.strip())
    if clean_url.startswith("//"):
        clean_url = "https:" + clean_url

    if not clean_url.startswith(("http://", "https://")):
        return None

    lower_url = clean_url.lower()
    for pattern in GENERIC_IMAGE_PATTERNS:
        if pattern in lower_url:
            return None

    if not is_safe_url(clean_url):
        return None

    try:
        resp = requests.head(clean_url, headers=HTTP_HEADERS, timeout=4, allow_redirects=True)
        if resp.status_code == 200:
            ct = resp.headers.get("Content-Type", "").lower()
            if any(ct.startswith(valid) for valid in ["image/jpeg", "image/png", "image/webp", "image/avif"]):
                cl = resp.headers.get("Content-Length")
                if cl and int(cl) < 2000:  # Filter tracking pixels
                    return None
                return resp.url
    except Exception:
        pass

    return None

def extract_json_ld(soup: BeautifulSoup) -> Dict[str, Any]:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            if not script.string:
                continue
            data = json.loads(script.string)
            items = data if isinstance(data, list) else data.get("@graph", [data])
            for item in items:
                if isinstance(item, dict) and item.get("@type") in (
                    "NewsArticle",
                    "Article",
                    "ReportageNewsArticle",
                    "BlogPosting"
                ):
                    return item
        except Exception:
            continue
    return {}

def extract_article(target_url: str, fallback_title: str = "", fallback_summary: str = "") -> Dict[str, Any]:
    """
    Fetches real publisher article, extracts structured metadata, real publisher images, and body content.
    """
    real_url = resolve_publisher_url(target_url)
    parsed_domain = urllib.parse.urlparse(real_url).netloc.lower().replace("www.", "")

    if not is_safe_url(real_url):
        return {"success": False, "error": "SSRF_BLOCKED"}

    try:
        resp = requests.get(real_url, headers=HTTP_HEADERS, timeout=config.REQUEST_TIMEOUT_SECONDS, allow_redirects=True)
        if resp.status_code >= 400:
            return {"success": False, "error": f"HTTP_{resp.status_code}"}

        soup = BeautifulSoup(resp.content, "html.parser")
        json_ld = extract_json_ld(soup)

        # 1. Headline Extraction: JSON-LD headline -> og:title -> title tag -> fallback
        og_title = soup.find("meta", property="og:title")
        tw_title = soup.find("meta", {"name": "twitter:title"})
        title_tag = soup.title.string.strip() if soup.title and soup.title.string else ""

        extracted_title = ""
        if json_ld.get("headline"):
            extracted_title = str(json_ld["headline"]).strip()
        elif og_title and og_title.get("content"):
            extracted_title = og_title["content"].strip()
        elif tw_title and tw_title.get("content"):
            extracted_title = tw_title["content"].strip()
        elif title_tag:
            extracted_title = title_tag
        else:
            extracted_title = fallback_title

        extracted_title = strip_html_tags(extracted_title)
        if " - " in extracted_title:
            parts = extracted_title.rsplit(" - ", 1)
            if len(parts[0]) > 10:
                extracted_title = parts[0].strip()

        if not is_valid_news_title(extracted_title):
            return {"success": False, "error": "INVALID_ARTICLE_TITLE"}

        # 2. Publisher Name Extraction: Domain Map -> JSON-LD publisher -> og:site_name
        publisher_name = config.DOMAIN_PUBLISHER_MAP.get(parsed_domain)
        if not publisher_name:
            if isinstance(json_ld.get("publisher"), dict) and json_ld["publisher"].get("name"):
                publisher_name = str(json_ld["publisher"]["name"]).strip()
            elif soup.find("meta", property="og:site_name"):
                publisher_name = soup.find("meta", property="og:site_name").get("content", "").strip()

        if not publisher_name or publisher_name.lower() in FORBIDDEN_TITLES:
            publisher_name = parsed_domain.split(".")[0].title() if parsed_domain else "TezKhabar Wire"

        # 3. Canonical URL
        canonical_tag = soup.find("link", rel="canonical")
        canonical_url = canonical_tag.get("href") if canonical_tag and canonical_tag.get("href") else real_url
        if not canonical_url.startswith("http"):
            canonical_url = real_url

        # 4. Image Extraction: JSON-LD image -> og:image -> twitter:image -> article img
        extracted_img = None
        if json_ld.get("image"):
            img_val = json_ld["image"]
            if isinstance(img_val, list) and len(img_val) > 0:
                extracted_img = img_val[0] if isinstance(img_val[0], str) else img_val[0].get("url")
            elif isinstance(img_val, dict):
                extracted_img = img_val.get("url")
            elif isinstance(img_val, str):
                extracted_img = img_val

        if not extracted_img:
            og_img = soup.find("meta", property="og:image")
            if og_img and og_img.get("content"):
                extracted_img = og_img["content"]

        if not extracted_img:
            tw_img = soup.find("meta", {"name": "twitter:image"})
            if tw_img and tw_img.get("content"):
                extracted_img = tw_img["content"]

        if not extracted_img:
            art_img = soup.find("article") or soup.find("main")
            if art_img:
                img_tag = art_img.find("img")
                if img_tag and img_tag.get("src"):
                    extracted_img = img_tag["src"]

        valid_image_url = validate_and_normalize_image(extracted_img, base_url=real_url)

        # 5. Summary / Description
        og_desc = soup.find("meta", property="og:description")
        meta_desc = soup.find("meta", {"name": "description"})
        extracted_desc = ""
        if json_ld.get("description"):
            extracted_desc = str(json_ld["description"]).strip()
        elif og_desc and og_desc.get("content"):
            extracted_desc = og_desc["content"].strip()
        elif meta_desc and meta_desc.get("content"):
            extracted_desc = meta_desc["content"].strip()
        else:
            extracted_desc = fallback_summary

        extracted_desc = strip_html_tags(extracted_desc)

        # 6. Clean Body Content
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "iframe", "noscript", "svg"]):
            tag.decompose()

        article_container = soup.find("article") or soup.find("main") or soup
        paragraphs = []
        for p in article_container.find_all("p"):
            text = strip_html_tags(p.get_text())
            if len(text) > 35 and not any(junk in text.lower() for junk in ["subscribe", "cookie policy", "terms of use", "all rights reserved", "click here", "sign up", "download the app"]):
                paragraphs.append(text)

        body_text = " ".join(paragraphs) if paragraphs else (json_ld.get("articleBody") or extracted_desc)
        body_text = strip_html_tags(body_text)

        has_source_content = len(body_text.split()) >= 20

        # 7. Real Publication Date
        meta_pub = soup.find("meta", property="article:published_time") or soup.find("meta", {"name": "pubdate"})
        published_at = None
        if json_ld.get("datePublished"):
            published_at = str(json_ld["datePublished"])
        elif meta_pub and meta_pub.get("content"):
            published_at = meta_pub["content"]

        author = json_ld.get("author", {}).get("name") if isinstance(json_ld.get("author"), dict) else None

        return {
            "success": True,
            "title": extracted_title,
            "description": extracted_desc or body_text[:240],
            "body": body_text[:4000] if has_source_content else None,
            "has_source_content": has_source_content,
            "image_url": valid_image_url,
            "publisher_name": publisher_name,
            "resolved_url": real_url,
            "canonical_url": canonical_url,
            "source_domain": parsed_domain,
            "published_at": published_at,
            "author": author or publisher_name,
            "word_count": len(body_text.split()) if has_source_content else 0,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
