import re
import json
import logging
import unicodedata
from groq import Groq
import config
from extractor import strip_html_tags, is_valid_news_title

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
    return slug[:90].rstrip("-") or "news-story"

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

def process_article_with_ai(title: str, raw_text: str, source_name: str, feed_cat: str) -> dict:
    normalized_cat = normalize_category(feed_cat)
    clean_summary = strip_html_tags(raw_text)[:300] if raw_text else title
    
    fallback_result = {
        "dek": clean_summary[:140],
        "summary": clean_summary,
        "category": normalized_cat,
        "subcategory": "India",
        "content": f"<p>{raw_text[:800]}</p>",
        "key_facts": [title],
        "why_it_matters": f"Reported by {source_name} regarding ongoing developments.",
        "ai_generated": False,
        "confidence": "developing"
    }

    if not groq_client:
        return fallback_result

    prompt = f"""You are a senior editorial news assistant for TezKhabar.
Summarize this news report strictly and factually.
RULES:
1. Never invent facts, quotes, statistics, dates, or names.
2. Maintain neutral, objective journalistic tone.
3. Output valid JSON only matching this schema:

{{
  "dek": "1 factual sentence explaining the core event",
  "summary": "2-3 clear factual sentences explaining context",
  "category": "{normalized_cat}",
  "subcategory": "India",
  "content": "<p>2-3 well-written HTML paragraphs summarizing the full facts.</p>",
  "key_facts": ["Fact 1", "Fact 2", "Fact 3"],
  "why_it_matters": "1 sentence on significance",
  "confidence": "developing"
}}

Publisher: {source_name}
Title: {title}
Article Body: {raw_text[:2500]}
"""

    for model_name in [config.GROQ_MODEL, "llama-3.1-8b-instant", "llama3-70b-8192", "llama3-8b-8192"]:
        try:
            chat_completion = groq_client.chat.completions.create(
                messages=[
                    {"role": "system", "content": "You are a factual editorial news intelligence assistant. Output valid JSON only."},
                    {"role": "user", "content": prompt}
                ],
                model=model_name,
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=900,
            )

            content = chat_completion.choices[0].message.content
            parsed = json.loads(content)
            parsed["category"] = normalize_category(parsed.get("category", normalized_cat))
            parsed["ai_generated"] = True
            return parsed
        except Exception:
            continue

    return fallback_result
