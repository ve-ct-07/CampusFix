"""Local AI-powered campus maintenance triage using Ollama."""

import base64
import json
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

OLLAMA_BASE_URL = os.getenv(
    "OLLAMA_BASE_URL",
    "http://localhost:11434",
).rstrip("/")

OLLAMA_CHAT_MODEL = os.getenv(
    "OLLAMA_CHAT_MODEL",
    "qwen3.5:2b",
)

REQUEST_TIMEOUT = 90

CATEGORIES = [
    "Plumbing",
    "Electrical",
    "Infrastructure",
    "Cleaning",
    "Internet/IT",
    "Furniture",
    "Safety",
    "Other",
]

SEVERITIES = ["Low", "Medium", "High", "Critical"]

HAZARDS = [
    "Electrical",
    "Fire",
    "Structural",
    "Slip/Fall",
    "Biological",
    "Chemical",
    "Security",
]

ISSUE_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "enum": CATEGORIES,
        },
        "secondary_hazards": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": HAZARDS,
            },
        },
        "severity": {
            "type": "string",
            "enum": SEVERITIES,
        },
        "summary": {
            "type": "string",
        },
        "safety_risk": {
            "type": "boolean",
        },
        "reason": {
            "type": "string",
        },
        "safety_risk_reason": {
            "type": "string",
        },
    },
    "required": [
        "category",
        "secondary_hazards",
        "severity",
        "summary",
        "safety_risk",
        "reason",
        "safety_risk_reason",
    ],
}


def _keyword_fallback(description: str) -> dict:
    """Conservative fallback when the local model is unavailable."""

    text = description.lower()

    rules = {
        "Plumbing": (
            "water", "leak", "pipe", "tap",
            "drain", "toilet", "faucet",
        ),
        "Electrical": (
            "electric", "wire", "switch",
            "socket", "power", "light",
        ),
        "Infrastructure": (
            "crack", "wall", "ceiling",
            "door", "window", "road",
        ),
        "Cleaning": (
            "garbage", "waste", "dirty",
            "clean", "dust",
        ),
        "Internet/IT": (
            "wifi", "wi-fi", "internet",
            "network", "router", "computer",
        ),
        "Furniture": (
            "chair", "desk", "table",
            "bench", "cupboard",
        ),
        "Safety": (
            "smoke", "fire", "gas",
            "sparking", "exposed wire",
        ),
    }

    category = "Other"

    for candidate, keywords in rules.items():
        if any(word in text for word in keywords):
            category = candidate
            break

    hazards = []

    electrical_terms = (
        "switchboard",
        "electrical panel",
        "exposed wire",
        "electric wire",
        "socket",
        "live wire",
        "sparking",
    )

    fire_terms = ("fire", "smoke", "sparking")
    structural_terms = ("collapsing", "ceiling collapse", "structural damage")
    slip_terms = ("slippery floor", "slippery", "standing water")

    if any(term in text for term in electrical_terms):
        hazards.append("Electrical")

    if any(term in text for term in fire_terms):
        hazards.append("Fire")

    if any(term in text for term in structural_terms):
        hazards.append("Structural")

    if any(term in text for term in slip_terms):
        hazards.append("Slip/Fall")

    water_near_electrical = (
        any(term in text for term in ("water", "leak", "flood"))
        and any(term in text for term in electrical_terms)
    )

    immediate_hazards = (
        "fire",
        "smoke",
        "sparking",
        "exposed wire",
        "live wire",
        "gas leak",
        "electric shock",
    )

    safety = (
        water_near_electrical
        or any(term in text for term in immediate_hazards)
    )

    severity = "Critical" if safety else "Medium"

    return {
        "category": category,
        "secondary_hazards": hazards,
        "severity": severity,
        "summary": description[:180],
        "safety_risk": safety,
        "reason": (
            "Keyword-based primary category assessment. "
            "Human review is required."
        ),
        "safety_risk_reason": (
            "Potential electrical exposure detected."
            if water_near_electrical
            else (
                "Potential immediate hazard detected."
                if safety
                else "No immediate safety hazard identified by fallback rules."
            )
        ),
        "confidence": None,
        "method": "keyword_fallback",
    }



def _apply_rule_overrides(description: str, result: dict) -> dict:
    """Correct clear category and hazard cases using transparent rules."""

    text = description.lower()
    result = dict(result)

    hazards = list(result.get("secondary_hazards") or [])

    def contains(*terms):
        return any(term in text for term in terms)

    # Clear primary-category corrections.
    if contains("wifi", "wi-fi", "wireless network", "internet connection",
                 "network router", "router is not working"):
        result["category"] = "Internet/IT"

    elif contains("garbage", "rubbish", "trash", "waste accumulation",
                  "uncollected waste", "dirty washroom", "dirty corridor"):
        result["category"] = "Cleaning"

    elif contains("gas leak", "gas leakage", "leaking gas"):
        result["category"] = "Safety"

    elif contains("water", "leak", "pipe", "drain", "tap", "toilet"):
        result["category"] = "Plumbing"

    elif contains("socket", "switchboard", "electrical panel",
                  "electric wire", "wiring", "power outlet"):
        result["category"] = "Electrical"

    elif contains("chair", "desk", "table", "bench", "cupboard"):
        result["category"] = "Furniture"

    elif contains("ceiling", "wall crack", "damaged wall",
                  "broken window", "damaged door"):
        result["category"] = "Infrastructure"

    # Add hazards only when the report contains supporting evidence.
    water_present = contains("water", "leak", "flood", "overflow")
    electrical_present = contains(
        "switchboard", "electrical panel", "live wire",
        "exposed wire", "electrical socket", "electric socket",
        "power outlet", "sparking",
    )

    if water_present and electrical_present:
        if "Electrical" not in hazards:
            hazards.append("Electrical")
        result["safety_risk"] = True
        result["safety_risk_reason"] = (
            "Water is reported near electrical equipment, creating "
            "a potential shock, short-circuit, or fire hazard."
        )

    slippery_water = (
        water_present
        and contains("slippery", "slippery floor", "wet floor",
                     "across the corridor", "across the walkway",
                     "spreading across the floor")
    )

    if slippery_water:
        if "Slip/Fall" not in hazards:
            hazards.append("Slip/Fall")
        result["safety_risk"] = True
        result["safety_risk_reason"] = (
            "Water on a walking surface can create a slip-and-fall hazard."
        )

    loose_overhead = contains(
        "hanging loose", "loose ceiling", "hanging ceiling",
        "ceiling panel", "falling ceiling", "loose panel",
    )

    if loose_overhead and contains("above students", "above people",
                                   "above occupants", "above a desk",
                                   "over students"):
        if "Structural" not in hazards:
            hazards.append("Structural")
        result["safety_risk"] = True
        result["safety_risk_reason"] = (
            "A loose overhead panel above people may fall and cause injury."
        )

    if contains("gas leak", "gas leakage", "leaking gas"):
        if "Fire" not in hazards:
            hazards.append("Fire")
        result["safety_risk"] = True
        result["safety_risk_reason"] = (
            "A suspected gas leak can create a fire or explosion hazard."
        )

    if contains("smoke", "burning smell", "sparking", "fire"):
        if "Fire" not in hazards:
            hazards.append("Fire")
        result["safety_risk"] = True

    result["secondary_hazards"] = [
        hazard for hazard in HAZARDS if hazard in hazards
    ]

    # Do not downgrade a model assessment when a rule identifies a hazard.
    if result["safety_risk"] and result["severity"] == "Low":
        result["severity"] = "High"

    return result


def classify_issue(description: str, image_path: str | None = None) -> dict:
    """Classify the primary issue and identify secondary hazards."""

    image_b64 = None

    if image_path:
        path = Path(image_path)

        if path.is_file():
            image_b64 = base64.b64encode(
                path.read_bytes()
            ).decode("ascii")

    prompt = f"""
You are an AI assistant supporting a university campus maintenance team.

Analyze the following report and identify the primary maintenance problem
separately from any secondary safety hazards.

IMPORTANT CLASSIFICATION RULES:

1. The primary category describes what needs to be repaired.
   Examples:
   - A leaking water pipe is Plumbing.
   - A broken electrical socket is Electrical.
   - A damaged wall is Infrastructure.

2. Secondary hazards describe additional risks caused by the problem.
   Examples:
   - Water leaking near a switchboard:
     Primary category = Plumbing.
     Secondary hazard = Electrical.
   - A broken ceiling with risk of falling debris:
     Primary category = Infrastructure.
     Secondary hazard = Structural.

3. Do not classify every electrical-related hazard as an Electrical
   maintenance problem. Identify the actual source of the problem.

4. Set safety_risk to true only when the description or image provides
   credible evidence of an immediate or significant safety concern.

5. Severity:
   - Low: Minor inconvenience.
   - Medium: Normal maintenance issue.
   - High: Significant disruption or substantial hazard.
   - Critical: Immediate danger requiring urgent attention.

6. Do not invent details. If uncertain, be conservative and explain
   the uncertainty in the reason.

7. Secondary hazards must contain only relevant hazards.
   Return an empty array if none are identified.

Return the required structured JSON object.

REPORT:
{description}
"""

    message = {
        "role": "user",
        "content": prompt,
    }

    if image_b64:
        message["images"] = [image_b64]

    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": OLLAMA_CHAT_MODEL,
                "messages": [message],
                "format": ISSUE_SCHEMA,
                "stream": False,
                "think": False,
                "options": {
                    "temperature": 0.1,
                },
            },
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        raw = response.json()["message"]["content"]
        result = json.loads(raw)

        if result.get("category") not in CATEGORIES:
            raise ValueError("Invalid category")

        if result.get("severity") not in SEVERITIES:
            raise ValueError("Invalid severity")

        hazards = result.get("secondary_hazards")

        if not isinstance(hazards, list):
            raise ValueError("Invalid secondary hazards")

        if any(hazard not in HAZARDS for hazard in hazards):
            raise ValueError("Unknown secondary hazard")

        if not isinstance(result.get("safety_risk"), bool):
            raise ValueError("Invalid safety risk value")

        result = _apply_rule_overrides(description, result)
        result["method"] = "ollama_local"

        # Model confidence is not calibrated.
        result["confidence"] = None

        return result

    except (
        requests.RequestException,
        KeyError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
    ):
        fallback = _keyword_fallback(description)
        return _apply_rule_overrides(description, fallback)
