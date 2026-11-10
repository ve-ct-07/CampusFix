"""Location-aware semantic duplicate detection with persistent embedding cache."""
import os
import re

import numpy as np
import requests
from dotenv import load_dotenv

from database.db import get_issue_embedding, save_issue_embedding

load_dotenv()

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "embeddinggemma-2:270m")


def get_embedding(text: str) -> np.ndarray:
    response = requests.post(
        f"{OLLAMA_BASE_URL}/api/embed",
        json={"model": EMBED_MODEL, "input": text},
        timeout=60,
    )
    response.raise_for_status()
    return np.asarray(response.json()["embeddings"][0], dtype=np.float32)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def normalize_location(location: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (location or "").lower()))


def same_location(location_a: str, location_b: str) -> bool:
    # Strict match for this MVP to avoid cross-building false positives.
    return normalize_location(location_a) == normalize_location(location_b)


def _issue_vector(issue: dict) -> np.ndarray:
    cached = get_issue_embedding(issue["issue_id"], EMBED_MODEL)
    if cached is not None:
        return np.asarray(cached, dtype=np.float32)

    text = (
        f"Location: {issue.get('location', '')}. "
        f"Issue: {issue.get('title', '')}. {issue.get('description', '')}"
    )
    vector = get_embedding(text)
    save_issue_embedding(issue["issue_id"], EMBED_MODEL, vector.tolist())
    return vector


def find_similar_issues(title: str, description: str, existing_issues: list,
                        location: str = "", exclude_issue_ids=None,
                        threshold: float = 0.82, top_k: int = 10) -> list:
    """Return likely duplicates in the exact same normalized location.

    The caller must preserve each report. A match is only a suggestion.
    """
    excluded = set(exclude_issue_ids or [])
    query = get_embedding(f"Location: {location}. Issue: {title}. {description}")
    matches = []

    for issue in existing_issues:
        issue_id = int(issue["issue_id"])
        if issue_id in excluded:
            continue
        if not same_location(location, issue.get("location", "")):
            continue

        score = cosine_similarity(query, _issue_vector(issue))
        if score >= threshold:
            matches.append({
                "issue_id": issue_id,
                "title": issue.get("title", ""),
                "location": issue.get("location", ""),
                "status": issue.get("status", ""),
                "similarity": score,
            })

    return sorted(matches, key=lambda item: item["similarity"], reverse=True)[:top_k]
