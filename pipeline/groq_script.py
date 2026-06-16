"""Groq (OpenAI-compatible) — generate Short script + image prompts as JSON.

Supports two modes:
  • Single-language preset (legacy): returns full_narration, youtube_title, etc.
  • Multi-variant preset (bilingual): returns image_prompts once + variants[lang] = {title, desc, narration}.
"""
from __future__ import annotations

import json
import os
from typing import Any

from groq import Groq

from pipeline.channel_presets import ChannelPreset
from pipeline.story_history import history_prompt_block

GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")


# ── Language-specific word-count guidance ──────────────────────────────
LANG_WORD_TARGETS = {
    "en": (
        120,
        155,
        "120-155 English words for variants.en.full_narration (~40-50 sec); "
        "add transitions, examples, and a closing takeaway — NOT a bullet list",
    ),
    "hi": (
        135,
        170,
        "135-170 Devanagari Hindi words — aim ~150 (~55-70 sec); full sentences, not headlines",
    ),
}

# Bilingual presets override per variant; these are fallbacks only.
DEFAULT_MIN_WORDS = {"hi": 50, "en": 50}


def _lang_label(lang: str) -> str:
    return {"en": "English", "hi": "Hindi (Devanagari script)"}.get(lang, lang)


def generate_short_pack(
    preset: ChannelPreset,
    *,
    topic_hint: str | None = None,
    channel_id: str | None = None,
) -> dict[str, Any]:
    topic_hint = (topic_hint or os.environ.get("SHORT_TOPIC", "")).strip()

    user = (
        f"Channel style: {preset['label']}.\n"
        f"Create ONE YouTube Short.\n"
    )
    if topic_hint:
        user += f"Topic idea from creator: {topic_hint}\n"

    if channel_id:
        anti_repeat = history_prompt_block(channel_id)
        if anti_repeat:
            user += anti_repeat

    n = preset["segment_count"]
    variants = preset.get("variants") or []

    if variants:
        return _generate_multivariant(preset, user, n, variants)
    return _generate_single(preset, user, n)


# ─────────────────────────────────────────────────────────────────────────
# Single-language path (backward compat for ghost_stories, school_story, etc.)
# ─────────────────────────────────────────────────────────────────────────
def _generate_single(preset: ChannelPreset, user: str, n: int) -> dict[str, Any]:
    language = (preset.get("language") or "en").lower()
    lo, hi, blurb = LANG_WORD_TARGETS.get(language, LANG_WORD_TARGETS["en"])

    if language == "hi":
        narration_rule = (
            '"full_narration": "COMPLETE narration as ONE continuous paragraph in Devanagari Hindi. '
            f'This is what the voice will read aloud. MUST be {blurb}. '
            'Natural spoken Hindi — no segment markers, no numbering, no English transliteration."'
        )
        strict_extra = (
            "- LANGUAGE: full_narration, youtube_title, and youtube_description MUST be in Devanagari Hindi.\n"
            "- image_prompts MUST be in ENGLISH (the image model does not understand Hindi).\n"
            f"- WORD COUNT: full_narration MUST contain {lo}-{hi} Hindi words.\n"
        )
    else:
        narration_rule = (
            '"full_narration": "COMPLETE story/script as one continuous paragraph. This is what the voice will read. '
            f'Must be {blurb}. Natural narration — no segment breaks, no numbering."'
        )
        strict_extra = f"- full_narration is ONE continuous paragraph, {lo}-{hi} English words.\n"

    user += f"""
Return ONLY valid JSON with this shape:
{{
  "youtube_title": "short catchy title, under 90 chars, no hashtags",
  "youtube_description": "2-3 sentences plus optional #Shorts at end",
  {narration_rule},
  "image_prompts": [
    "visual description for image 1: setting, subject, action. No style words. No text in image.",
    "visual description for image 2...",
    "..."
  ]
}}

STRICT RULES:
{strict_extra}- "image_prompts" array MUST have exactly {n} entries.
- Each image_prompt matches a different moment/beat in order.
- Image prompts are just visuals — no narration text, no style words, no quotes.
- The narration must flow naturally as one spoken piece (no "segment 1", "segment 2" etc).
"""

    max_attempts = 3
    last_err = ""
    for attempt in range(max_attempts):
        extra = ""
        if attempt > 0:
            extra = (
                f"\n\nCRITICAL: Previous attempt failed validation: {last_err}.\n"
                "Please rewrite the narration to be longer and more detailed. "
                f"Aim for {lo}-{hi} words. Add more descriptive sentences to each beat.\n"
            )

        temp = 0.85 if attempt < 2 else 0.45
        data = _call_groq(preset, user + extra, temperature=temp)

        try:
            narration = data.get("full_narration", "").strip()
            if not narration:
                raise ValueError("Missing full_narration")

            prompts = data.get("image_prompts")
            if not isinstance(prompts, list) or len(prompts) != n:
                raise ValueError(f"Expected {n} image_prompts, got {len(prompts or [])}")
            for i, p in enumerate(prompts):
                if not isinstance(p, str) or not p.strip():
                    raise ValueError(f"image_prompt {i} is empty")

            word_count = len(narration.split())
            min_words = preset.get("min_words", DEFAULT_MIN_WORDS.get(language, 80))
            if word_count < min_words:
                raise ValueError(
                    f"Narration too short ({word_count} words, expected ≥ {min_words} for {language})"
                )

            return data
        except ValueError as e:
            last_err = str(e)
            if attempt == max_attempts - 1:
                raise

    # Fallback (should be unreachable due to raise above)
    return data


# ─────────────────────────────────────────────────────────────────────────
# Multi-variant path (one Groq call returns every language's narration)
# ─────────────────────────────────────────────────────────────────────────
def _generate_multivariant(
    preset: ChannelPreset, user: str, n: int, variants: list,
) -> dict[str, Any]:
    # Build the per-language requirement lines
    lang_lines = []
    for v in variants:
        lang = v["lang"]
        lo, hi, blurb = LANG_WORD_TARGETS.get(lang, LANG_WORD_TARGETS["en"])
        lang_lines.append(
            f'    "{lang}": {{\n'
            f'      "youtube_title": "catchy title in {_lang_label(lang)} (<90 chars, no hashtags)",\n'
            f'      "youtube_description": "2-3 sentences in {_lang_label(lang)} + optional #Shorts",\n'
            f'      "full_narration": "ONE continuous paragraph in {_lang_label(lang)}. '
            f'{blurb}. Natural spoken narration, no segment markers."\n'
            f'    }}'
        )
    variants_block = ",\n".join(lang_lines)

    word_targets = "\n".join(
        f"  - {_lang_label(v['lang'])}: {LANG_WORD_TARGETS.get(v['lang'], LANG_WORD_TARGETS['en'])[2]}"
        for v in variants
    )
    lang_keys = ", ".join(f'"{v["lang"]}"' for v in variants)

    user += f"""
Return ONLY valid JSON with this shape:
{{
  "image_prompts": [
    "visual description for image 1 — IN ENGLISH ONLY: setting, subject, action. No style words. No text in image.",
    "visual description for image 2 — in English…",
    "..."
  ],
  "variants": {{
{variants_block}
  }}
}}

STRICT RULES:
- "image_prompts" array MUST have exactly {n} entries, ALL in English.
- "variants" object MUST contain keys: {lang_keys}.
- Each variant tells the SAME facts/story but written natively in that language (not literal translation).
- Word-count targets per language:
{word_targets}
- Narrations are continuous spoken paragraphs — no segment numbers, no headings.
- Titles/descriptions: each in its own language.
- BEFORE you output JSON: mentally count words in each full_narration. If English is under 115 words OR Hindi under 100 words, REWRITE that paragraph longer (same facts) until counts are met.
"""

    last_err: str | None = None
    max_attempts = 4
    for attempt in range(max_attempts):
        extra = ""
        if last_err:
            extra = (
                "\n\n=== REGENERATE (previous JSON failed validation) ===\n"
                f"{last_err}\n"
                "Return a NEW complete JSON object that fixes the issue. "
                "Keep the same facts/story and the same image_prompts beats; "
                "expand ONLY the narration(s) that were too short — add 3-5 full sentences each.\n"
            )
        # Later attempts: lower temperature so the model obeys length constraints more reliably.
        temp = 0.85 if attempt < 2 else 0.45
        data = _call_groq(preset, user + extra, temperature=temp)
        try:
            _assert_multivariant_valid(data, variants, n)
            return data
        except ValueError as e:
            last_err = str(e)
            if attempt == max_attempts - 1:
                raise


def _assert_multivariant_valid(data: dict[str, Any], variants: list, n: int) -> None:
    prompts = data.get("image_prompts")
    if not isinstance(prompts, list) or len(prompts) != n:
        raise ValueError(f"Expected {n} image_prompts, got {len(prompts or [])}")
    for i, p in enumerate(prompts):
        if not isinstance(p, str) or not p.strip():
            raise ValueError(f"image_prompt {i} is empty")

    vmap = data.get("variants")
    if not isinstance(vmap, dict):
        raise ValueError("Groq response missing 'variants' object")

    for v in variants:
        lang = v["lang"]
        node = vmap.get(lang)
        if not isinstance(node, dict):
            raise ValueError(f"variants['{lang}'] missing")

        narration = (node.get("full_narration") or "").strip()
        if not narration:
            raise ValueError(f"variants['{lang}'].full_narration empty")

        min_words = v.get("min_words", DEFAULT_MIN_WORDS.get(lang, 80))
        word_count = len(narration.split())
        if word_count < min_words:
            lo, hi, _ = LANG_WORD_TARGETS.get(lang, LANG_WORD_TARGETS["en"])
            raise ValueError(
                f"variants['{lang}'].full_narration too short "
                f"({word_count} words, need ≥{min_words}; ideal range {lo}-{hi})"
            )

        if not (node.get("youtube_title") or "").strip():
            raise ValueError(f"variants['{lang}'].youtube_title empty")


def _call_groq(
    preset: ChannelPreset,
    user: str,
    *,
    temperature: float = 0.85,
) -> dict[str, Any]:
    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    resp = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": preset["groq_system_hint"]},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        max_tokens=3072,
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content
    if not raw:
        raise RuntimeError("Empty Groq response")
    return json.loads(raw)
