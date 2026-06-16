"""Image generation from scene prompts (DeAPI → HuggingFace → Pollinations)."""
from __future__ import annotations

import os
import random
import time
import urllib.parse
from pathlib import Path

import httpx

DEAPI_SUBMIT_URL = "https://api.deapi.ai/api/v1/client/txt2img"
DEAPI_POLL_URL = "https://api.deapi.ai/api/v1/client/request-status"

STYLE_SUFFIX = (
    ", cinematic digital illustration, detailed scene art, strong composition, "
    "professional youtube visual quality, no text, no captions, no watermark, no logos"
)

DEFAULT_NEGATIVE = (
    "blurry, low quality, watermark, logo, text, title, signature, ugly, grainy, "
    "gore, blood, nudity, child-unsafe"
)

DEFAULT_HF_MODELS = (
    "stabilityai/stable-diffusion-xl-base-1.0",
    "runwayml/stable-diffusion-v1-5",
)


def full_visual_prompt(scene: str, style_suffix: str | None = None) -> str:
    """Combine the scene description with a channel-specific style suffix."""
    return f"{scene.strip()}{(style_suffix or STYLE_SUFFIX)}"


def model_candidates() -> list[str]:
    raw = os.environ.get("HF_MODELS", "")
    if raw.strip():
        return [m.strip() for m in raw.split(",") if m.strip()]
    return list(DEFAULT_HF_MODELS)


def _api_token(token: str | None = None) -> str:
    return (token or os.environ.get("HF_TOKEN") or os.environ.get("DEAPI_TOKEN") or "").strip()


def _backend_order(*, allow_pollinations: bool = True) -> list[str]:
    explicit = os.environ.get("IMAGE_BACKEND", "auto").strip().lower()
    if explicit and explicit != "auto":
        backends = [b.strip() for b in explicit.split(",") if b.strip()]
    else:
        hf = os.environ.get("HF_TOKEN", "").strip()
        deapi = os.environ.get("DEAPI_TOKEN", "").strip()
        backends = []
        # Workflows often map HF_TOKEN → DEAPI_TOKEN; prefer HF when both names exist.
        if hf:
            backends.append("huggingface")
            if deapi and deapi != hf:
                backends.append("deapi")
        elif deapi:
            backends.append("deapi")
            backends.append("huggingface")
        if allow_pollinations and os.environ.get("HF_NO_POLLINATIONS", "").lower() not in (
            "1",
            "true",
            "yes",
        ):
            backends.append("pollinations")
    if not allow_pollinations:
        backends = [b for b in backends if b != "pollinations"]
    # De-duplicate while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for b in backends:
        if b not in seen:
            seen.add(b)
            out.append(b)
    return out


def _deapi_generate(
    prompt: str,
    *,
    api_key: str,
    width: int,
    height: int,
    model: str,
    max_polls: int = 30,
    poll_interval: float = 3.0,
) -> bytes:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "prompt": prompt,
        "model": model,
        "width": width,
        "height": height,
        "steps": 4,
        "seed": random.randint(1, 999999),
    }

    with httpx.Client(timeout=60.0) as client:
        for submit_try in range(5):
            resp = client.post(DEAPI_SUBMIT_URL, json=payload, headers=headers)
            if resp.status_code == 429:
                wait = 15 * (submit_try + 1)
                print(f"      DeAPI 429 on submit — waiting {wait}s (try {submit_try + 1}/5)…")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            break
        else:
            raise RuntimeError("DeAPI: 429 on submit after 5 retries")

        data = resp.json()
        request_id = data.get("data", {}).get("request_id")
        if not request_id:
            raise RuntimeError(f"No request_id in DeAPI response: {data}")
        print(f"      DeAPI submitted (id: {request_id})")

        poll_headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
        for attempt in range(1, max_polls + 1):
            time.sleep(poll_interval)
            poll_resp = client.get(
                f"{DEAPI_POLL_URL}/{request_id}",
                headers=poll_headers,
                timeout=30.0,
            )
            poll_resp.raise_for_status()
            poll_data = poll_resp.json()
            status = poll_data.get("data", {}).get("status", "")

            if status in ("completed", "success", "done"):
                image_url = poll_data["data"].get("result_url")
                if not image_url:
                    raise RuntimeError(f"Completed but no result_url: {poll_data}")
                img_resp = client.get(image_url, timeout=60.0)
                img_resp.raise_for_status()
                print(f"      DeAPI done (polled {attempt}x)")
                return img_resp.content

            if status in ("failed", "error"):
                raise RuntimeError(f"DeAPI image failed: {poll_data}")

        raise RuntimeError(f"DeAPI timed out after {max_polls} polls for {request_id}")


def _hf_generate(
    prompt: str,
    *,
    api_key: str,
    width: int,
    height: int,
    negative: str,
    models: list[str],
) -> bytes:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "inputs": prompt,
        "parameters": {"negative_prompt": negative, "width": width, "height": height},
        "options": {"wait_for_model": True},
    }

    last_err: Exception | None = None
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        for model in models:
            url = f"https://api-inference.huggingface.co/models/{model}"
            for attempt in range(1, 4):
                try:
                    resp = client.post(url, json=payload, headers=headers)
                    if resp.status_code == 503:
                        print(f"  🤖 HF model loading ({model})… waiting 20s ({attempt}/3)")
                        time.sleep(20)
                        continue
                    if resp.status_code == 401:
                        raise RuntimeError("HuggingFace token rejected (401)")
                    resp.raise_for_status()
                    content_type = resp.headers.get("content-type", "")
                    if "image" not in content_type and resp.content[:1] != b"\x89":
                        raise RuntimeError(
                            f"HF returned non-image payload from {model}: {resp.text[:200]}"
                        )
                    return resp.content
                except Exception as e:
                    last_err = e
                    if attempt == 3:
                        break
                    time.sleep(5)
    raise RuntimeError(f"HuggingFace failed for all models: {last_err}")


def _pollinations_generate(prompt: str, *, width: int, height: int) -> bytes:
    encoded = urllib.parse.quote(prompt, safe="")
    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width={width}&height={height}&nologo=true"
    )
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.content


def save_scene_image(
    index: int,
    prompt: str,
    out_path: Path,
    *,
    token: str | None = None,
    width: int = 768,
    height: int = 768,
    negative: str = DEFAULT_NEGATIVE,
    models: list[str] | None = None,
    allow_pollinations: bool = True,
) -> tuple[str, str]:
    """Generate and save one image from the scene prompt. Returns (status, detail)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    api_key = _api_token(token)
    model_list = models or model_candidates()
    backends = _backend_order(allow_pollinations=allow_pollinations)
    if not backends:
        return "fail", "No image backend configured (set HF_TOKEN, DEAPI_TOKEN, or enable Pollinations)"

    errors: list[str] = []
    for backend in backends:
        try:
            if backend == "deapi":
                if not api_key:
                    raise RuntimeError("DEAPI_TOKEN not set")
                model = os.environ.get("DEAPI_MODEL", "Flux_2_Klein_4B_BF16")
                img_bytes = _deapi_generate(
                    prompt, api_key=api_key, width=width, height=height, model=model
                )
                out_path.write_bytes(img_bytes)
                return "ok", "deapi"

            if backend == "huggingface":
                if not api_key:
                    raise RuntimeError("HF_TOKEN not set")
                img_bytes = _hf_generate(
                    prompt,
                    api_key=api_key,
                    width=width,
                    height=height,
                    negative=negative,
                    models=model_list,
                )
                out_path.write_bytes(img_bytes)
                return "ok", "huggingface"

            if backend == "pollinations":
                img_bytes = _pollinations_generate(prompt, width=width, height=height)
                out_path.write_bytes(img_bytes)
                return "ok", "pollinations"

        except Exception as e:
            errors.append(f"{backend}: {e}")
            print(f"      {backend} failed — trying next backend… ({e})")

    return "fail", "; ".join(errors) if errors else "All image backends failed"
