import re
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

def is_safe_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = parsed.hostname
        if not hostname or hostname in ("localhost", "127.0.0.1", "0.0.0.0"):
            return False
        
        # DNS Resolution Check
        ip_str = socket.gethostbyname(hostname)
        ip = ipaddress.ip_address(ip_str)
        for blocked in SSRF_BLOCKED_NETWORKS:
            if ip in blocked:
                return False
        return True
    except Exception:
        return False

def clean_source_url(url: str) -> str:
    try:
        parsed = urllib.parse.urlparse(url)
        clean_qs = []
        for k, v in urllib.parse.parse_qsl(parsed.query):
            if not k.startswith("utm_") and k not in ("fbclid", "gclid", "ref"):
                clean_qs.append((k, v))
        new_query = urllib.parse.urlencode(clean_qs)
        return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, ""))
    except Exception:
        return url

def extract_article(url: str) -> dict:
    if not is_safe_url(url):
        return {"success": False, "error": "SSRF_BLOCKED"}

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 TezKhabarEditorial/6.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=config.REQUEST_TIMEOUT_SECONDS, allow_redirects=True)
        if resp.status_code >= 400:
            return {"success": False, "error": f"HTTP_{resp.status_code}"}
        
        # Check size (Max 2MB)
        if len(resp.content) > 2 * 1024 * 1024:
            return {"success": False, "error": "PAYLOAD_TOO_LARGE"}

        soup = BeautifulSoup(resp.content, "lxml")

        # Extract Meta Tags
        og_title = soup.find("meta", property="og:title")
        og_desc = soup.find("meta", property="og:description")
        og_img = soup.find("meta", property="og:image")
        meta_pub = soup.find("meta", property="article:published_time") or soup.find("meta", {"name": "pubdate"})
        meta_author = soup.find("meta", {"name": "author"})

        title = og_title["content"].strip() if og_title and og_title.get("content") else ""
        if not title and soup.title:
            title = soup.title.string.strip() if soup.title.string else ""

        description = og_desc["content"].strip() if og_desc and og_desc.get("content") else ""
        image_url = og_img["content"].strip() if og_img and og_img.get("content") else None
        published_at = meta_pub["content"].strip() if meta_pub and meta_pub.get("content") else None
        author = meta_author["content"].strip() if meta_author and meta_author.get("content") else None

        # Clean Unwanted Tags
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "iframe", "noscript", "svg"]):
            tag.decompose()

        # Extract Text Paragraphs
        paragraphs = []
        for p in soup.find_all("p"):
            text = p.get_text().strip()
            # Filter low-quality lines
            if len(text) > 35 and not any(junk in text.lower() for junk in ["subscribe", "cookie policy", "terms of use", "all rights reserved", "click here"]):
                paragraphs.append(text)

        body_text = " ".join(paragraphs)
        word_count = len(body_text.split())

        # Quality Gate Check
        if word_count < 30 and len(description.split()) < 15:
            return {"success": False, "error": "CONTENT_BELOW_QUALITY_THRESHOLD"}

        return {
            "success": True,
            "title": title,
            "description": description,
            "body": body_text[:4000],  # trim for safe AI context
            "image_url": image_url if image_url and image_url.startswith("http") else None,
            "published_at": published_at,
            "author": author,
            "word_count": word_count,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
