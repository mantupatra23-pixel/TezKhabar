import re
import json
import logging
from datetime import datetime, timezone
import unicodedata
from groq import Groq
import config

logger = logging.getLogger("tezkhabar.ai")

groq_client = None
if config.GROQ_API_KEY:
    try:
        groq_client = Groq(api_key=config.GROQ_API_KEY)
    except Exception as e:
        logger.error(f"[AI] Groq client initialization error: {e}")

def create_slug(title: str) -> str:
    # Unicode normalize
    text = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("utf-8")
    text = re.sub(r"[^\w\s-]", "", text).lower().strip()
    slug = re.sub(r"[-\s]+", "-", text)
    return slug[:90].rstrip("-")

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
    # Token overlap cluster generator
    words = re.findall(r"\b[a-z0-9]{3,}\b", title.lower())
    common_stopwords = {"the", "and", "for", "that", "this", "with", "from", "have", "india", "news", "report"}
    key_words = [w for w in words if w not in common_stopwords]
    key_words.sort()
    signature = "-".join(key_words[:4]) if key_words else "general-cluster"
    return f"{category}-{signature}"

def process_article_with_ai(title: str, raw_text: str, source_name: str, feed_category: str) -> dict:
    """
    Summarizes news factually using Groq with structured JSON output.
    """
    normalized_cat = normalize_category(feed_category)
    
    # Fallback structure if AI unavailable
    fallback_result = {
        "title": title,
        "dek": raw_text[:140] + "..." if len(raw_text) > 140 else raw_text,
        "summary": raw_text[:280],
        "category": normalized_cat,
        "subcategory": "India",
        "content": f"<p>{raw_text[:600]}</p>",
        "key_facts": [title],
        "why_it_matters": f"Reported by {source_name} regarding ongoing developments.",
        "confidence": "developing"
    }

    if not groq_client:
        return fallback_result

    prompt = f"""You are a senior editorial news summarization assistant for an Indian news publication.
Summarize this news report strictly and factually.
RULES:
1. Never invent facts, quotes, statistics, dates, or names.
2. Maintain neutral, objective journalistic tone.
3. No sensationalism, clickbait, or emojis.
4. Output valid JSON only matching this schema:

{{
  "title": "Factual headline (concise, max 14 words)",
  "dek": "1 factual sentence explaining the core event",
  "summary": "2-3 clear factual sentences explaining context",
  "category": "{normalized_cat}",
  "subcategory": "National or topic area",
  "content": "<p>2-3 well-written HTML paragraphs summarizing the full facts.</p>",
  "key_facts": ["Fact 1", "Fact 2", "Fact 3"],
  "why_it_matters": "1 sentence on significance",
  "confidence": "developing"
}}

Source Name: {source_name}
Raw Title: {title}
Article Body: {raw_text[:2500]}
"""

    try:
        chat_completion = groq_client.chat.completions.create(
            messages=[
                {"role": "system", "content": "You are a factual editorial news intelligence assistant. Output valid JSON only."},
                {"role": "user", "content": prompt}
            ],
            model=config.GROQ_MODEL,
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=1000,
        )

        content = chat_completion.choices[0].message.content
        parsed = json.loads(content)
        parsed["category"] = normalize_category(parsed.get("category", normalized_cat))
        return parsed
    except Exception as e:
        logger.warning(f"[AI] Groq completion fallback used: {e}")
        return fallback_result
