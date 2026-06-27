"""Core Ollama-based pipeline: validate match details (with a web-search fallback
for stale model knowledge), write a postcard prompt that never asks for on-image
text (so there's nothing to misspell), generate the image, and have the model
review + iteratively fix it.
"""
from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .client import OllamaImageClient, save_image
from .search import format_search_context, search_web


class OllamaConnectionError(RuntimeError):
    """Raised when Ollama can't be reached, or returns something unparsable."""


def call_text_model(
    base_url: str, model: str, prompt: str, images: list[str] | None = None
) -> str:
    payload = {"model": model, "prompt": prompt, "stream": True}
    if images:
        payload["images"] = images

    req = urllib.request.Request(
        base_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    full_response: list[str] = []
    try:
        with urllib.request.urlopen(req) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                token = chunk.get("response", "")
                if token:
                    full_response.append(token)
                if chunk.get("done"):
                    break
    except urllib.error.URLError as exc:
        raise OllamaConnectionError(
            f"Could not connect to Ollama at {base_url}. "
            f"Make sure Ollama is running (`ollama serve`). Details: {exc}"
        ) from exc

    return "".join(full_response)


def build_match_prompt(
    team1: str, team2: str, date: str, game_type: str, web_context: str | None = None
) -> str:
    context_block = ""
    if web_context:
        context_block = (
            "\nNote: your own knowledge may be out of date. Here are live web "
            "search results that may help confirm whether this match really "
            f"happened:\n{web_context}\n\n"
        )
    return (
        "You are a sports news editor.\n"
        f"{context_block}"
        "Before doing anything else, validate this input:\n"
        f"- Date given: \"{date}\"\n"
        f"- Match given: \"{team1} vs. {team2}\"\n"
        f"- Game type given: \"{game_type}\"\n\n"
        "If the date is invalid, OR you have no knowledge of this match happening, "
        "reply with exactly this and nothing else:\n"
        "\"You are wrong: <short reason>\"\n"
        "and stop.\n\n"
        "If valid, your task is to write the Top 5 Headlines for this match, followed by "
        "a single creative image generation prompt based on those headlines.\n\n"
        "CRITICAL RULES FOR YOUR OUTPUT:\n"
        "- You must format your output EXACTLY with the two headers below.\n"
        "- Image prompt should have scorecard, so that scorecard should be printed in picture.\n"
        "- Any art style is allowed for the image (photography, painting, 3D render, etc.).\n\n"
        "HEADLINES:\n"
        "1. [Headline 1]\n"
        "2. [Headline 2]\n"
        "3. [Headline 3]\n"
        "4. [Headline 4]\n"
        "5. [Headline 5]\n\n"
        "IMAGE_PROMPT:\n"
        "[A highly descriptive, creative visual prompt capturing the vibe of the headlines. Do not ask for specific text or words in the image.]"
    )

def build_review_prompt(brief_and_headlines: str) -> str:
    return (
        "You are an exacting art director reviewing an AI-generated image against "
        "the headlines and brief it was generated from.\n\n"
        f"Brief and Headlines:\n\"\"\"\n{brief_and_headlines}\n\"\"\"\n\n"
        "Check the attached image for major discrepancies in this order:\n\n"
        "1) CONTEXT CHECK: Does the image completely fail to represent the sport, "
        "the teams, or the mood of the headlines? (e.g., showing basketball for a soccer match). "
        "If there is a major visual discrepancy, reply with exactly:\n"
        "COMMENTS: <describe what is completely wrong>\n"
        "and stop -- do not check anything else.\n\n"
        "2) GIBBERISH TEXT CHECK: If there is highly prominent, mangled, or nonsensical text "
        "that ruins the image, reply with exactly:\n"
        "SPELLING: <describe where the messy text is>\n"
        "and stop.\n\n"
        "3) If the image adequately captures the match and headlines without major errors, "
        "regardless of the specific art style, reply with exactly:\n"
        "APPROVED\n\n"
        "Reply with nothing else besides one of those three formats: "
        "SPELLING, APPROVED, or COMMENTS."
    )

def build_review_prompt(brief_and_headlines: str) -> str:
    return (
        "You are an exacting art director reviewing an AI-generated image against "
        "the headlines and brief it was generated from.\n\n"
        f"Brief and Headlines:\n\"\"\"\n{brief_and_headlines}\n\"\"\"\n\n"
        "Check the attached image for major discrepancies in this order:\n\n"
        "1) CONTEXT CHECK: Does the image completely fail to represent the sport, "
        "the teams, or the mood of the headlines? (e.g., showing basketball for a soccer match). "
        "If there is a major visual discrepancy, reply with exactly:\n"
        "COMMENTS: <describe what is completely wrong>\n"
        "and stop -- do not check anything else.\n\n"
        "2) GIBBERISH TEXT CHECK: If there is highly prominent, mangled, or nonsensical text "
        "that ruins the image, reply with exactly:\n"
        "SPELLING: <describe where the messy text is>\n"
        "and stop.\n\n"
        "3) If the image adequately captures the match and headlines without major errors, "
        "regardless of the specific art style, reply with exactly:\n"
        "APPROVED\n\n"
        "Reply with nothing else besides one of those three formats: "
        "SPELLING, APPROVED, or COMMENTS."
    )

def slugify(*parts: str) -> str:
    text = "_".join(parts)
    text = re.sub(r"\s+", "_", text.strip())
    text = re.sub(r"[^\w\-]", "", text)
    return text.lower()


def save_prompt_text(text: str, output_dir: str, date: str, team1: str, team2: str) -> Path:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{slugify(date, team1, team2)}_prompt.txt"
    out_path.write_text(text, encoding="utf-8")
    return out_path


@dataclass
class ReviewRound:
    round_num: int
    status: str  # "approved" | "spelling" | "comments"
    message: str = ""
    text_removed: bool = False  # whether THIS round's prompt asked for zero on-image text


@dataclass
class ImageResult:
    path: Path
    approved: bool
    rounds: list[ReviewRound] = field(default_factory=list)
    index: int = 1


@dataclass
class MatchResult:
    date: str
    team1: str
    team2: str
    game_type: str
    prompt_text: str
    prompt_text_path: Path
    valid: bool
    rejection_reason: str = ""
    searched_web: bool = False
    images: list[ImageResult] = field(default_factory=list)


def generate_and_review_image(
    client: OllamaImageClient,
    full_prompt_context: str,
    image_prompt: str,
    size: str,
    output_dir: str,
    base_filename: str,
) -> ImageResult:
    current_prompt = image_prompt
    image_bytes = b""
    rounds: list[ReviewRound] = []
    consecutive_spelling = 0
    text_removed = False

    for round_num in range(1, config.MAX_REVIEW_ROUNDS + 1):
        image_bytes = client.generate(current_prompt, size=size)
        # Pass the FULL context (headlines + prompt) to the reviewer
        status, message = review_image(image_bytes, full_prompt_context)
        rounds.append(
            ReviewRound(round_num=round_num, status=status, message=message, text_removed=text_removed)
        )

        if status == "approved":
            path = save_image(image_bytes, f"{base_filename}_approved", output_dir)
            return ImageResult(path=path, approved=True, rounds=rounds)
        else:
            path = save_image(image_bytes, f"{base_filename}_{round_num}", output_dir)
            print(f"Round {round_num}: {status} issue -- {message}")

        consecutive_spelling = consecutive_spelling + 1 if status == "spelling" else 0

        if not text_removed and consecutive_spelling >= config.MAX_CONSECUTIVE_SPELLING_ISSUES:
            text_removed = True
            current_prompt = (
                f"{image_prompt} -- REVISION: Remove all text, words, banners, and letters "
                "from the image completely. Make it purely visual."
            )
        else:
            current_prompt = f"{image_prompt} -- REVISION FIX: {message}"

    path = save_image(image_bytes, base_filename, output_dir)
    return ImageResult(path=path, approved=False, rounds=rounds)

def review_image(image_bytes: bytes, full_prompt_context: str) -> tuple[str, str]:
    """Ask the text model to check a generated image against its headlines and brief.

    Returns (status, message): status is "approved", "spelling", or "comments".
    message is "" when approved.
    """
    # 1. Build the exact prompt for the reviewer using the new headlines + brief
    review_prompt = build_review_prompt(full_prompt_context)
    
    # 2. Encode the generated image to send back to the vision model
    b64_image = base64.b64encode(image_bytes).decode("utf-8")
    
    # 3. Call the model
    reply = call_text_model(
        config.OLLAMA_URL, config.TEXT_MODEL, review_prompt, images=[b64_image]
    ).strip()

    # 4. Parse the strict output format
    upper = reply.upper()
    if upper.startswith("APPROVED"):
        return "approved", ""
    if upper.startswith("SPELLING"):
        return "spelling", reply.split(":", 1)[1].strip() if ":" in reply else reply
    if upper.startswith("COMMENTS"):
        return "comments", reply.split(":", 1)[1].strip() if ":" in reply else reply
        

    return "comments", reply

def build_fact_check_prompt(generated_text: str) -> str:
    return (
        "You are a meticulous sports fact-checker.\n"
        "Review the following headlines and image prompt for factual accuracy.\n"
        f"Text to review:\n\"\"\"\n{generated_text}\n\"\"\"\n\n"
        "If there are any factual errors regarding the match context, correct them and "
        "rewrite the text preserving the exact format (the HEADLINES: and IMAGE_PROMPT: sections).\n"
        "If it is completely factually accurate, output the original text exactly as is.\n"
        "Do not add any extra commentary, greetings, or explanations."
    )

def run_match(
    client: OllamaImageClient,
    date: str,
    team1: str,
    team2: str,
    game_type: str,
    size: str,
    output_dir: str,
    count: int,
) -> MatchResult:
    # 1. Generate the prompt using the new headline-based instructions
    news_prompt = build_match_prompt(team1, team2, date, game_type)
    response = call_text_model(config.OLLAMA_URL, config.TEXT_MODEL, news_prompt).strip()
    searched_web = False

    # 2. Check if the model rejected the match, and try web search as a fallback
    if response.lower().startswith("you are wrong"):
        results = search_web(f"{team1} vs {team2} {game_type} {date}", config.WEB_SEARCH_RESULTS)
        searched_web = True
        if results:
            web_context = format_search_context(results)
            augmented_prompt = build_match_prompt(team1, team2, date, game_type, web_context=web_context)
            response = call_text_model(config.OLLAMA_URL, config.TEXT_MODEL, augmented_prompt).strip()

    # NEW SECTION: Fact-Checker
    # 2.5. If the match wasn't rejected, run the generated headlines/prompt through a fact-checker
    if not response.lower().startswith("you are wrong"):
        fact_check_instruction = build_fact_check_prompt(response)
        response = call_text_model(config.OLLAMA_URL, config.TEXT_MODEL, fact_check_instruction).strip()

    # 3. Save the full text response (Headlines + Prompt or Rejection) to disk
    prompt_text_path = save_prompt_text(response, output_dir, date, team1, team2)

    # 4. Handle final rejection
    if response.lower().startswith("you are wrong"):
        return MatchResult(
            date=date, team1=team1, team2=team2, game_type=game_type,
            prompt_text=response, prompt_text_path=prompt_text_path,
            valid=False, rejection_reason=response, searched_web=searched_web,
        )


    image_prompt = response 
    if "IMAGE_PROMPT:" in response:
        # Split on the header and grab everything after it
        image_prompt = response.split("IMAGE_PROMPT:")[-1].strip()

    base_name = slugify(date, team1, team2)
    images: list[ImageResult] = []
    
    for i in range(count):
        filename = base_name if count == 1 else f"{base_name}_{i + 1}"
        
        result = generate_and_review_image(
            client=client, 
            full_prompt_context=response, 
            image_prompt=image_prompt, 
            size=size, 
            output_dir=output_dir, 
            base_filename=filename
        )
        result.index = i + 1
        images.append(result)

    return MatchResult(
        date=date, team1=team1, team2=team2, game_type=game_type,
        prompt_text=response, prompt_text_path=prompt_text_path,
        valid=True, images=images, searched_web=searched_web,
    )