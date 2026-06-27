from __future__ import annotations


def search_web(query: str, max_results: int = 5) -> list[dict]:
    """Search the web for `query`. Returns [] on any failure."""
    try:
        from ddgs import DDGS
    except ImportError:
        return []

    try:
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))
    except Exception:
        return []


def format_search_context(results: list[dict]) -> str:
    """Turn search hits into a short, model-readable block of text."""
    lines = []
    for r in results:
        title = (r.get("title") or "").strip()
        body = (r.get("body") or "").strip()
        if title or body:
            lines.append(f"- {title}: {body}")
    return "\n".join(lines)