import re
import json
import logging
import unicodedata
from groq import Groq
import config

logger = logging.getLogger("tezkhabar.ai")

groq_client = None
active_model = config.GROQ_MODEL

FALLBACK_MODELS = [
    config.GROQ_MODEL,
    "llama-3.1-8b-instant",
    "llama-3.3-70b-versatile",
    "llama-3.1-70b-versatile",
    "llama3-70b-8192",
    "llama3-8b-8192",
    "gemma2-9b-it",
    "mixtral-8x7b-32768"
]

if config.GROQ_API_KEY:
    try:
        groq_client = Groq(api_key=config.GROQ_API_KEY)
        # Verify model availability
        try:
            available_models = [m.id for m in groq_client.models.list().data]
            logger.info(f"[AI] Connected to Groq. Available models: {len(available_models)}")
            if active_model not in available_models:
                for candidate in FALLBACK_MODELS:
                    if candidate in available_models:
                        active_model = candidate
                        logger.info(f"[AI] Switching to verified active model: {active_model}")
                        break
        except Exception as e:
            logger.warning(f"[AI] Model list verification skipped: {e}")
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
    signature = "-".join(key_words[:4]) if key_words else "general-cluster"
    return f"{category}-{signature}"

def process_article_with_ai(title: str, raw_text: str, source_name: str, feed_cat: str) -> dict:
    normalized_cat = normalize_category(feed_cat)
    cleaned_desc = raw_text.strip()[:300] if raw_text else title
    
    # Safe deterministic metadata fallback
    fallback_result = {
        "title": title,
        "dek": cleaned_desc[:140],
        "summary": cleaned_desc,
        "category": normalized_cat,
        "subcategory": "India",
        "content": f"<p>{raw_text[:800] if raw_text else title}</p>",
        "key_facts": [title],
        "why_it_matters": f"Reported by {source_name} regarding ongoing developments.",
        "ai_generated": False,
        "confidence": "developing"
    }

    if not groq_client:
        return fallback_result

    prompt = f"""You are a senior editorial news summarization assistant for an Indian news publication.
Summarize this news report strictly and factually.
RULES:
1. Never invent facts, quotes, statistics, dates, or names.
2. Maintain neutral, objective journalistic tone.
3. Output valid JSON only matching this schema:

{{
  "title": "Factual headline (concise, max 14 words)",
  "dek": "1 factual sentence explaining the core event",
  "summary": "2-3 clear factual sentences explaining context",
  "category": "{normalized_cat}",
  "subcategory": "India",
  "content": "<p>2-3 well-written HTML paragraphs summarizing the full facts.</p>",
  "key_facts": ["Fact 1", "Fact 2", "Fact 3"],
  "why_it_matters": "1 sentence on significance",
  "confidence": "developing"
}}

Source: {source_name}
Title: {title}
Article: {raw_text[:2500]}
"""

    for model_name in [active_model] + [m for m in FALLBACK_MODELS if m != active_model]:
        try:
            chat_completion = groq_client.chat.completions.create(
                messages=[
                    {"role": "system", "content": "You are a factual editorial news intelligence assistant. Output valid JSON only."},
                    {"role": "user", "content": prompt}
                ],
                model=model_name,
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=1000,
            )

            content = chat_completion.choices[0].message.content
            parsed = json.loads(content)
            parsed["category"] = normalize_category(parsed.get("category", normalized_cat))
            parsed["ai_generated"] = True
            return parsed
        except Exception as e:
            err_msg = str(e)
            if "404" in err_msg or "model_not_found" in err_msg:
                logger.warning(f"[AI] Model {model_name} unavailable (404), testing fallback...")
                continue
            else:
                logger.warning(f"[AI] Model {model_name} processing error: {e}")
                break

    logger.info(f"[AI] Using safe metadata fallback for: '{title[:45]}...'")
    return fallback_result
