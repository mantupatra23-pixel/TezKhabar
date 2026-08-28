import re
import json
import logging
import unicodedata
from typing import Dict, Any, Optional, Tuple
from groq import Groq
import config
from extractor import strip_html_tags

logger = logging.getLogger("tezkhabar.ai")
groq_client = None

if config.GROQ_API_KEY:
    try:
        groq_client = Groq(api_key=config.GROQ_API_KEY)
    except Exception as e:
        logger.error(f"[AI] Groq client initialization error: {e}")

def create_slug(title: str) -> str:
    text = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("utf-8")
    text = re.sub(r"[^\w\s-]", "", text).lower().strip()
    slug = re.sub(r"[-\s]+", "-", text)
    return slug[:90].rstrip("-") or "editorial-story"

def normalize_category(category_candidate: str) -> str:
    if not category_candidate:
        return "india"
    cat = category_candidate.lower().strip()
    if cat in config.CONTROLLED_CATEGORIES:
        return cat
    if cat in config.CATEGORY_ALIAS_MAP:
        return config.CATEGORY_ALIAS_MAP[cat]
    return "india"

def generate_cluster_id(title: str, category: str) -> str:
    words = re.findall(r"\b[a-z0-9]{3,}\b", title.lower())
    common_stopwords = {"the", "and", "for", "that", "this", "with", "from", "have", "india", "news", "report"}
    key_words = [w for w in words if w not in common_stopwords]
    key_words.sort()
    signature = "-".join(key_words[:4]) if key_words else "cluster"
    return f"{category}-{signature}"

def extract_facts_and_synthesize_editorial(
    source_title: str,
    source_content: str,
    publisher_name: str,
    category: str
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any], int]:
    """
    Two-stage Editorial Pipeline:
    1. Extract verified structured facts from research material.
    2. Write an original TezKhabar journalistic article.
    """
    clean_cat = normalize_category(category)

    if not groq_client or not source_content or len(source_content.split()) < 25:
        # Factual fallback without hallucinations
        fallback_facts = {"event": source_title, "summary": source_content[:300]}
        fallback_article = {
            "title": source_title,
            "dek": source_content[:140],
            "summary": source_content[:280],
            "content": f"<p>{source_content[:800]}</p>",
            "key_highlights": [source_title],
            "why_it_matters": f"Reporting synthesized from {publisher_name}.",
            "category": clean_cat,
        }
        return fallback_article, fallback_facts, 50

    prompt = f"""You are the senior editor at TezKhabar Editorial Desk.
Your task is to review the research material below, extract verified factual notes, and write a completely fresh, original TezKhabar editorial news article.

HARD EDITORIAL RULES:
1. NEVER copy source sentences or paragraph structure.
2. Write original headlines and prose with neutral, authoritative journalism.
3. NEVER invent facts, names, dates, quotes, or statistics not found in the research notes.
4. DO NOT write "TezKhabar reporters witnessed" unless explicitly provided.
5. DO NOT include external URLs, "Read more at...", or clickbait phrasing.
6. Return a valid JSON object matching the schema below.

RESEARCH MATERIAL:
Publisher: {publisher_name}
Topic Category: {clean_cat}
Raw Headline: {source_title}
Source Text: {source_content[:3200]}

OUTPUT JSON SCHEMA:
{{
  "facts": {{
    "core_event": "1-2 sentences summarizing the verified event",
    "key_figures": ["Person/Org 1", "Person/Org 2"],
    "confirmed_data": ["Verified fact/number 1", "Verified fact/number 2"],
    "uncertainties": "Any developing or unconfirmed points"
  }},
  "article": {{
    "title": "Fresh, original journalistic headline (max 14 words)",
    "dek": "Clear, factual 1-2 sentence subtitle",
    "summary": "2-3 sentence executive editorial summary",
    "body_html": "<p>Intro paragraph explaining what, where, who, and why it matters.</p><h2>Key Developments</h2><p>Analytical explanation of the facts.</p><h2>Context and Implications</h2><p>Background and forward-looking significance.</p>",
    "key_highlights": [
      "Highlight 1",
      "Highlight 2",
      "Highlight 3"
    ],
    "why_it_matters": "1 concise sentence explaining the broader significance."
  }},
  "quality_score": 85
}}
"""

    for model in [config.GROQ_MODEL, "llama-3.1-8b-instant", "llama3-70b-8192"]:
        try:
            resp = groq_client.chat.completions.create(
                messages=[
                    {"role": "system", "content": "You are a professional editorial news intelligence architect. Output valid JSON only."},
                    {"role": "user", "content": prompt}
                ],
                model=model,
                response_format={"type": "json_object"},
                temperature=0.15,
                max_tokens=1400,
            )
            parsed = json.loads(resp.choices[0].message.content)
            article_data = parsed.get("article", {})
            facts_data = parsed.get("facts", {})
            score = int(parsed.get("quality_score", 80))

            if not article_data.get("title") or not article_data.get("body_html"):
                continue

            article_data["category"] = clean_cat
            return article_data, facts_data, score
        except Exception as e:
            logger.warning(f"[AI] Generation retry for '{source_title[:30]}': {e}")
            continue

    return None, {}, 0
