"""Optional semantic scoring of Backstage story bites with structured GPT output."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from core.backstage_feedback import few_shot_examples


SCORE_SCHEMA = {
    "type": "object",
    "properties": {"scores": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "string"}, "funny": {"type": "number"}, "story": {"type": "number"},
        "hook": {"type": "number"}, "payoff": {"type": "number"}, "complete": {"type": "boolean"},
        "confidence": {"type": "number"}, "reason": {"type": "string"}, "english_text": {"type": "string"},
    }, "required": ["id", "funny", "story", "hook", "payoff", "complete", "confidence", "reason", "english_text"], "additionalProperties": False}}},
    "required": ["scores"], "additionalProperties": False,
}


def _provider_config(provider: str | None = None, model: str | None = None) -> dict[str, str]:
    selected = (provider or os.environ.get("NARRATIVE_LLM_PROVIDER") or "deepseek").strip().lower()
    if selected == "openai":
        return {"provider": "openai", "base_url": os.environ.get("NARRATIVE_LLM_BASE_URL", "https://api.openai.com/v1"), "model": model or os.environ.get("NARRATIVE_LLM_MODEL", "gpt-5-mini"), "key": os.environ.get("NARRATIVE_LLM_API_KEY", os.environ.get("OPENAI_API_KEY", "")).strip()}
    return {"provider": "deepseek", "base_url": os.environ.get("NARRATIVE_LLM_BASE_URL", "https://api.deepseek.com"), "model": model or os.environ.get("NARRATIVE_LLM_MODEL", "deepseek-v4-pro"), "key": os.environ.get("NARRATIVE_LLM_API_KEY", os.environ.get("DEEPSEEK_API_KEY", "")).strip()}


def _validate_scores(data: Any, expected_ids: set[str]) -> list[dict[str, Any]]:
    if not isinstance(data, dict) or not isinstance(data.get("scores"), list):
        raise ValueError("JSON must contain a scores array")
    scores = data["scores"]
    if {str(item.get("id")) for item in scores if isinstance(item, dict)} != expected_ids:
        raise ValueError("JSON scores do not exactly match the requested bite ids")
    required = {"id", "funny", "story", "hook", "payoff", "complete", "confidence", "reason", "english_text"}
    for item in scores:
        if not isinstance(item, dict) or not required.issubset(item):
            raise ValueError("JSON score has missing fields")
        for field in ("funny", "story", "hook", "payoff", "confidence"):
            if not isinstance(item[field], (int, float)) or not 0 <= float(item[field]) <= 10:
                raise ValueError(f"Invalid {field} score")
        if not isinstance(item["complete"], bool) or not isinstance(item["reason"], str):
            raise ValueError("Invalid complete/reason field")
    return scores


def score_story_bites(bites: list[dict[str, Any]], batch_size: int = 25, model: str | None = None, provider: str | None = None) -> dict[str, Any]:
    """Score bites in 20–30 item batches with provider-specific JSON validation/retry."""
    config = _provider_config(provider, model)
    key = config["key"]
    model = config["model"]
    if not bites:
        return {"status": "empty", "provider": config["provider"], "model": model, "batches": 0, "bites": []}
    if not key:
        return {"status": "unavailable", "provider": config["provider"], "model": model, "reason": f"API key for {config['provider']} is not configured", "batches": 0, "bites": bites}
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key, base_url=config["base_url"])
        scored = {str(item.get("id")): dict(item) for item in bites}
        batch_count = 0
        input_tokens = 0
        output_tokens = 0
        cache_hit_tokens = 0
        cache_miss_tokens = 0
        retry_count = 0
        peak = datetime.now(timezone.utc).hour in {1, 2, 3, 6, 7, 8, 9}
        price = ((1.32 if peak else 0.66), (3.96 if peak else 1.98)) if config["provider"] == "deepseek" and model == "deepseek-v4-pro" else ((0.44 if peak else 0.22, 1.32 if peak else 0.66) if config["provider"] == "deepseek" else (0.25, 2.0))
        cache_hit_price = (0.044 if peak else 0.022) if config["provider"] == "deepseek" and model == "deepseek-v4-pro" else (0.014 if peak else 0.007)
        for offset in range(0, len(bites), max(20, min(30, batch_size))):
            batch = bites[offset:offset + max(20, min(30, batch_size))]
            batch_count += 1
            compact = [{"id": item.get("id"), "source": item.get("filename"), "start": item.get("start_sec"), "end": item.get("end_sec"), "text_original": item.get("text_original") or item.get("text"), "complete_hint": item.get("complete")} for item in batch]
            system = "Translate each original story bite into natural English and score it. Return JSON only, with exactly the requested ids and fields. The highest priority is that the bite is a complete, comprehensible sentence or complete conversational intervention. If it is cut off, unintelligible, garbled, or clearly deformed, set complete=false and set funny, story, hook, payoff and confidence to 0. Only after that gate, score whether it contains meaningful content, tells something, has humor, or is a real conversation. Do not reward or search for any particular topic, object, keyword, or named subject. Judge only what the complete text itself communicates. Preserve meaning and do not invent details. Scores are 0 to 10."
            user = json.dumps({"format": {"scores": [{"id": "bite-id", "english_text": "English translation", "funny": 0, "story": 0, "hook": 0, "payoff": 0, "complete": True, "confidence": 0, "reason": "brief criterion-based reason"}]}, "bites": compact}, ensure_ascii=False)
            data = None
            for attempt in range(3):
                response = client.chat.completions.create(model=model, messages=[{"role": "system", "content": system}, {"role": "user", "content": user}], response_format={"type": "json_object"}, max_tokens=6000, temperature=0.1, **({"extra_body": {"thinking": {"type": "disabled"}}} if config["provider"] == "deepseek" else {}))
                usage = getattr(response, "usage", None)
                input_tokens += int(getattr(usage, "prompt_tokens", getattr(usage, "input_tokens", 0)) or 0)
                output_tokens += int(getattr(usage, "completion_tokens", getattr(usage, "output_tokens", 0)) or 0)
                cache_hit_tokens += int(getattr(usage, "prompt_cache_hit_tokens", 0) or 0)
                cache_miss_tokens += int(getattr(usage, "prompt_cache_miss_tokens", 0) or 0)
                raw = response.choices[0].message.content or ""
                try:
                    data = {"scores": _validate_scores(json.loads(raw), {str(item.get("id")) for item in batch})}
                    break
                except (ValueError, json.JSONDecodeError) as exc:
                    if attempt == 2:
                        raise ValueError(f"Invalid JSON after retries: {exc}") from exc
                    retry_count += 1
                    user = json.dumps({"correction": "The previous answer failed validation. Return valid JSON only, exactly matching these ids and fields. No markdown.", "format": {"scores": [{"id": "bite-id", "english_text": "English translation", "funny": 0, "story": 0, "hook": 0, "payoff": 0, "complete": True, "confidence": 0, "reason": "brief criterion-based reason"}]}, "bites": compact}, ensure_ascii=False)
            for score in data.get("scores", []):
                item = scored.get(str(score.get("id")))
                if item is not None:
                    item["text_original"] = item.get("text_original") or item.get("text")
                    item["english_text"] = str(score.get("english_text") or item.get("text") or "")
                    item["text"] = item["english_text"]
                    if score.get("complete") is False:
                        for field in ("funny", "story", "hook", "payoff", "confidence"):
                            score[field] = 0
                    item["narrative_scores"] = {key: score.get(key) for key in ("funny", "story", "hook", "payoff", "complete", "confidence", "reason")}
        cost_usd = (cache_hit_tokens / 1_000_000 * cache_hit_price + cache_miss_tokens / 1_000_000 * price[0] + output_tokens / 1_000_000 * price[1]) if config["provider"] == "deepseek" else input_tokens / 1_000_000 * price[0] + output_tokens / 1_000_000 * price[1]
        return {"status": "ready", "provider": config["provider"], "base_url": config["base_url"], "model": model, "batches": batch_count, "retries": retry_count, "input_tokens": input_tokens, "output_tokens": output_tokens, "cache_hit_tokens": cache_hit_tokens, "cache_miss_tokens": cache_miss_tokens, "cost_usd": round(cost_usd, 6), "bites": list(scored.values())}
    except Exception as exc:
        return {"status": "error", "model": model, "reason": str(exc), "batches": 0, "bites": bites}


def score_story_sequences(sequences: list[dict[str, Any]], batch_size: int = 25, model: str | None = None, provider: str | None = None) -> dict[str, Any]:
    """Score complete conversations as units, translating original text in the same request."""
    config = _provider_config(provider, model)
    if not sequences:
        return {"status": "empty", "provider": config["provider"], "model": config["model"], "batches": 0, "sequences": []}
    if not config["key"]:
        return {"status": "unavailable", "provider": config["provider"], "model": config["model"], "reason": f"API key for {config['provider']} is not configured", "batches": 0, "sequences": sequences}
    try:
        from openai import OpenAI
        client = OpenAI(api_key=config["key"], base_url=config["base_url"])
        scored = {str(item["id"]): dict(item) for item in sequences}
        batch_count = retries = input_tokens = output_tokens = cache_hit = cache_miss = 0
        peak = datetime.now(timezone.utc).hour in {1, 2, 3, 6, 7, 8, 9}
        miss_price, output_price = ((1.32, 3.96) if peak else (0.66, 1.98)) if config["provider"] == "deepseek" and config["model"] == "deepseek-v4-pro" else ((0.44, 1.32) if peak else (0.22, 0.66))
        hit_price = (0.044 if peak else 0.022) if config["provider"] == "deepseek" and config["model"] == "deepseek-v4-pro" else (0.014 if peak else 0.007)
        for offset in range(0, len(sequences), max(20, min(30, batch_size))):
            batch = sequences[offset:offset + max(20, min(30, batch_size))]
            batch_count += 1
            compact = [{"id": item["id"], "clip": item.get("filename"), "start": item.get("start_sec"), "end": item.get("end_sec"), "duration": item.get("duration_sec"), "boundary_complete_hint": item.get("complete"), "text_original": item.get("text_original")} for item in batch]
            system = "Evaluate documentary conversations as complete units. Return JSON only. First priority is a comprehensible sequence whose first and last words form a usable conversational boundary. Do not mark a sequence incomplete merely because it has no punchline, is quiet, or contains an ordinary statement. Mark complete=false and set all numeric scores to 0 only when the text is clearly cut off mid-sentence, unintelligible, or severely garbled. Then score the whole conversation: a coherent account of an event or experience deserves story points even when it is calm, ordinary, or has no joke; also value genuine human exchange, humor, tension, and payoff. Do not require a punchline for story. Do not search for or reward any particular topic, object, keyword, or named subject. Translate the full original text into natural English without inventing details. Scores are 0 to 10."
            examples = [{"criterion_label": "USER EXAMPLE — positive keep/closing" if row.get("mark") in {"keep", "closing"} else "USER EXAMPLE — negative drop", "mark": row.get("mark"), "text_original": row.get("text_original"), "english_text": row.get("english_text"), "reason": row.get("reason")} for row in few_shot_examples(12)]
            user = json.dumps({"format": {"sequences": [{"id": "sequence-id", "english_text": "full English translation", "funny": 0, "story": 0, "hook": 0, "payoff": 0, "complete": True, "confidence": 0, "reason": "criterion-based reason"}]}, "user_feedback_examples": examples, "sequences": compact}, ensure_ascii=False)
            parsed = None
            for attempt in range(3):
                response = client.chat.completions.create(model=config["model"], messages=[{"role": "system", "content": system}, {"role": "user", "content": user}], response_format={"type": "json_object"}, max_tokens=6000, temperature=0.1, **({"extra_body": {"thinking": {"type": "disabled"}}} if config["provider"] == "deepseek" else {}))
                usage = getattr(response, "usage", None)
                input_tokens += int(getattr(usage, "prompt_tokens", 0) or 0); output_tokens += int(getattr(usage, "completion_tokens", 0) or 0)
                cache_hit += int(getattr(usage, "prompt_cache_hit_tokens", 0) or 0); cache_miss += int(getattr(usage, "prompt_cache_miss_tokens", 0) or 0)
                try:
                    raw = json.loads(response.choices[0].message.content or "")
                    items = raw.get("sequences") if isinstance(raw, dict) else None
                    expected = {str(item["id"]) for item in batch}
                    if not isinstance(items, list) or {str(item.get("id")) for item in items} != expected:
                        raise ValueError("sequence ids mismatch")
                    for item in items:
                        if not all(field in item for field in ("id", "english_text", "funny", "story", "hook", "payoff", "complete", "confidence", "reason")):
                            raise ValueError("missing sequence field")
                        if item["complete"] is False:
                            for field in ("funny", "story", "hook", "payoff", "confidence"): item[field] = 0
                        elif any(not isinstance(item[field], (int, float)) or not 0 <= float(item[field]) <= 10 for field in ("funny", "story", "hook", "payoff", "confidence")):
                            raise ValueError("invalid numeric score")
                    parsed = items; break
                except (ValueError, json.JSONDecodeError) as exc:
                    if attempt == 2: raise ValueError(f"Invalid sequence JSON after retries: {exc}") from exc
                    retries += 1
                    user = json.dumps({"correction": "Return valid JSON only, exactly one entry per requested sequence id, with no markdown.", "sequences": compact}, ensure_ascii=False)
            for score in parsed or []:
                item = scored[str(score["id"])]
                item["text_original"] = item.get("text_original") or ""
                item["english_text"] = str(score.get("english_text") or "")
                item["narrative_scores"] = {field: score.get(field) for field in ("funny", "story", "hook", "payoff", "complete", "confidence", "reason")}
        cost = (cache_hit * hit_price + cache_miss * miss_price + output_tokens * output_price) / 1_000_000 if config["provider"] == "deepseek" else (input_tokens * miss_price + output_tokens * output_price) / 1_000_000
        return {"status": "ready", "provider": config["provider"], "base_url": config["base_url"], "model": config["model"], "batches": batch_count, "retries": retries, "input_tokens": input_tokens, "output_tokens": output_tokens, "cache_hit_tokens": cache_hit, "cache_miss_tokens": cache_miss, "cost_usd": round(cost, 6), "sequences": list(scored.values())}
    except Exception as exc:
        return {"status": "error", "provider": config["provider"], "model": config["model"], "reason": str(exc), "batches": 0, "sequences": sequences}


def generate_storyboard(sequences: list[dict[str, Any]], music_path: str = "", model: str | None = None, provider: str | None = None) -> dict[str, Any]:
    """Build a narrative storyboard from complete conversations, without rendering."""
    config = _provider_config(provider, model)
    if not sequences:
        return {"status": "empty", "storyboard": {}}
    if not config["key"]:
        return {"status": "unavailable", "provider": config["provider"], "model": config["model"], "reason": f"API key for {config['provider']} is not configured"}
    try:
        from openai import OpenAI
        client = OpenAI(api_key=config["key"], base_url=config["base_url"])
        compact = [{"id": x.get("id"), "clip": x.get("filename"), "start_sec": x.get("start_sec"), "end_sec": x.get("end_sec"), "text_original": x.get("text_original") or x.get("text"), "english_text": x.get("english_text") or "", "scores": x.get("narrative_scores") or {}} for x in sequences]
        system = """You are the documentary editor for a backstage music film. Create a complete storyboard from the supplied conversation sequences. Do not invent events, shots, dialogue, or timestamps. Do not select by keywords alone: prefer coherent conversations, clear human exchanges, hooks, jokes, and satisfying endings. Treat garbled transcription as unreliable and flag it. Return JSON only with exactly these top-level fields: title, logline, opening, development, closing, scene_notes, music_plan, risks. opening/development/closing are arrays of objects with sequence_id, role, reason, and suggested_duration_sec. scene_notes is an array with sequence_id, what_happens, why_it_works, humor_or_hook, original_text, english_text. music_plan is an array with phase, sequence_ids, policy, transition. Use the requested music rules: background music at the very beginning over black, progressive mix as image enters, fade toward relevant clip audio; never put background music over relevant dialogue or existing source music; use more music-led visual-only scenes when there is no relevant speech; end with background music and a clean fade-out. Include a final line/idea only if the supplied material supports it. risks should list transcription or editorial uncertainties."""
        user = json.dumps({"music_path": music_path, "sequences": compact}, ensure_ascii=False)
        response = client.chat.completions.create(model=config["model"], messages=[{"role": "system", "content": system}, {"role": "user", "content": user}], response_format={"type": "json_object"}, max_tokens=9000, temperature=0.2, **({"extra_body": {"thinking": {"type": "disabled"}}} if config["provider"] == "deepseek" else {}))
        raw = json.loads(response.choices[0].message.content or "{}")
        required = {"title", "logline", "opening", "development", "closing", "scene_notes", "music_plan", "risks"}
        if not isinstance(raw, dict) or not required.issubset(raw):
            raise ValueError("Storyboard JSON is missing required fields")
        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        # DeepSeek chat pricing used elsewhere in this module; the report is
        # deliberately based on the returned usage, never an estimate of calls.
        price_in, price_out = (0.22, 0.66)
        cost = input_tokens / 1_000_000 * price_in + output_tokens / 1_000_000 * price_out if config["provider"] == "deepseek" else input_tokens / 1_000_000 * 0.25 + output_tokens / 1_000_000 * 2.0
        return {"status": "ready", "provider": config["provider"], "model": config["model"], "input_tokens": input_tokens, "output_tokens": output_tokens, "cost_usd": round(cost, 6), "storyboard": raw}
    except Exception as exc:
        return {"status": "error", "provider": config["provider"], "model": config["model"], "reason": str(exc)}
