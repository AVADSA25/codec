"""
CODEC Google Docs — styled document creator.
Extracted from deep_research.py so it can be used by codec_agents.py
without depending on CrewAI.

Usage:
    from codec_gdocs import create_google_doc
    url = create_google_doc("My Title", markdown_content)
"""
import os
import re
import requests as rq

_gdocs_cfg = {}
try:
    import json as _json
    with open(os.path.expanduser("~/.codec/config.json")) as _f:
        _gdocs_cfg = _json.load(_f)
except Exception:
    pass
PEXELS_API_KEY = _gdocs_cfg.get("pexels_api_key", os.environ.get("PEXELS_API_KEY", ""))

_GENERIC_HEADING_WORDS = {
    "executive", "summary", "introduction", "background", "conclusion",
    "sources", "references", "key", "findings", "analysis", "implications",
    "discussion", "results", "overview", "appendix", "abstract",
}
_CHARS_PER_IMAGE = 9_000
_MAX_IMAGES      = 7


def _pexels_fetch_one(query: str, page_offset: int = 0):
    try:
        resp = rq.get(
            "https://api.pexels.com/v1/search",
            params={"query": query, "per_page": 5, "page": page_offset + 1,
                    "orientation": "landscape"},
            headers={"Authorization": PEXELS_API_KEY},
            timeout=10,
        )
        photos = resp.json().get("photos", [])
        if photos:
            return photos[0]["src"]["large2x"]
    except Exception as e:
        print(f"[GDOCS] Pexels error '{query}': {e}")
    return None


def _heading_keywords(heading: str) -> str:
    cleaned = re.sub(r"^\d+[\.\d]*\s+", "", heading)
    words = [w for w in cleaned.split()
             if w.lower() not in _GENERIC_HEADING_WORDS and len(w) > 3]
    return " ".join(words[:2])


def _section_body_text(positions: list, after_idx: int, max_chars: int = 600) -> str:
    collecting, parts, total = False, [], 0
    for p in positions:
        if p["start"] >= after_idx and not collecting:
            collecting = True
        if collecting and p["type"] == "h2" and p["start"] > after_idx:
            break
        if collecting and p["type"] == "body":
            parts.append(p["text"])
            total += len(p["text"])
            if total >= max_chars:
                break
    return " ".join(parts)


def _smart_query(topic_base: str, positions: list, insert_idx: int) -> str:
    heading = ""
    for p in positions:
        if p["type"] in ("h2", "h3") and p["start"] <= insert_idx:
            heading = p["text"]
    body = _section_body_text(positions, insert_idx)
    ctx  = (heading + " " + body).lower()
    kw   = _heading_keywords(heading)

    if any(w in ctx for w in ["statistic", "percent", "%", "billion", "million",
                               "market", "adoption", "growth", "forecast"]):
        return f"{topic_base} data analytics business"
    if any(w in ctx for w in ["hardware", "chip", "processor", "npu", "gpu", "silicon"]):
        return f"{topic_base} computer hardware chip"
    if any(w in ctx for w in ["privacy", "security", "encryption", "gdpr", "breach"]):
        return f"{topic_base} cybersecurity data protection"
    if any(w in ctx for w in ["global", "enterprise", "fortune", "deployment"]):
        return f"{topic_base} global enterprise technology"
    if any(w in ctx for w in ["energy", "power", "carbon", "sustainable", "green"]):
        return f"{topic_base} sustainable energy environment"
    if any(w in ctx for w in ["medical", "health", "drug", "clinical", "pharma"]):
        return f"{topic_base} medical health technology"
    if any(w in ctx for w in ["legal", "regulatory", "law", "regulation", "executive order"]):
        return f"{topic_base} law regulation compliance"
    if any(w in ctx for w in ["future", "forecast", "trend", "next", "emerging"]):
        return f"{topic_base} future innovation technology"
    if kw:
        return f"{topic_base} {kw}"
    return f"{topic_base} technology"


def _find_image_positions(positions: list) -> tuple:
    h1_list  = [p for p in positions if p["type"] == "h1"]
    hero_idx = h1_list[0]["end"] if h1_list else 2
    additional, next_thresh, running = [], _CHARS_PER_IMAGE, 0
    for p in positions:
        running += p["end"] - p["start"]
        if running >= next_thresh and p["type"] == "h2":
            if not additional or p["start"] != additional[-1]:
                additional.append(p["start"])
                next_thresh += _CHARS_PER_IMAGE
                if len(additional) >= _MAX_IMAGES - 1:
                    break
    return hero_idx, additional


def _parse_markdown(content: str) -> list:
    blocks = []
    for line in content.split("\n"):
        if line.startswith("# "):
            blocks.append({"text": line[2:].strip(), "type": "h1"})
        elif line.startswith("## "):
            blocks.append({"text": line[3:].strip(), "type": "h2"})
        elif line.startswith("### "):
            blocks.append({"text": line[4:].strip(), "type": "h3"})
        elif line.strip() in ("---", "***", "___"):
            blocks.append({"text": "", "type": "empty"})
        elif line.strip() == "":
            blocks.append({"text": "", "type": "empty"})
        else:
            text = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
            text = re.sub(r"\*(.+?)\*", r"\1", text)
            text = re.sub(r"`(.+?)`", r"\1", text)
            text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
            blocks.append({"text": text, "type": "body"})
    return blocks


def create_google_doc(title: str, content: str) -> str | None:
    """
    Create a styled Google Doc with:
    - H1: 28pt, bold, centered, dark
    - H2: 15pt, bold, CODEC orange, bottom border
    - H3: 13pt, bold, slate
    - Body: 11pt, 150% line spacing
    - Up to 7 Pexels images (hero full-width, then left/right alternating)
    Returns the doc URL or None on error.
    """
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    TOKEN_PATH = os.path.expanduser("~/.codec/google_token.json")
    DARK   = {"red": 0.082, "green": 0.082, "blue": 0.137}
    ORANGE = {"red": 0.910, "green": 0.443, "blue": 0.102}
    SLATE  = {"red": 0.173, "green": 0.243, "blue": 0.314}

    try:
        creds = Credentials.from_authorized_user_file(TOKEN_PATH)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(TOKEN_PATH, "w") as f:
                f.write(creds.to_json())

        svc    = build("docs", "v1", credentials=creds)
        doc    = svc.documents().create(body={"title": title}).execute()
        doc_id  = doc["documentId"]
        doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"

        # Build plain text + position map
        blocks    = _parse_markdown(content)
        full_text = ""
        positions = []
        idx       = 1
        for block in blocks:
            line = block["text"] + "\n"
            positions.append({"start": idx, "end": idx + len(line),
                               "type": block["type"], "text": block["text"]})
            full_text += line
            idx += len(line)

        # Batch 1: insert text + styles
        reqs = [{"insertText": {"location": {"index": 1}, "text": full_text}}]

        for p in positions:
            s, e, t = p["start"], p["end"], p["type"]
            te = e - 1
            if not p["text"]:
                continue

            if t == "h1":
                reqs += [
                    {"updateParagraphStyle": {
                        "range": {"startIndex": s, "endIndex": e},
                        "paragraphStyle": {
                            "namedStyleType": "HEADING_1",
                            "alignment": "CENTER",
                            "spaceAbove": {"magnitude": 0,  "unit": "PT"},
                            "spaceBelow": {"magnitude": 10, "unit": "PT"},
                        },
                        "fields": "namedStyleType,alignment,spaceAbove,spaceBelow",
                    }},
                    {"updateTextStyle": {
                        "range": {"startIndex": s, "endIndex": te},
                        "textStyle": {
                            "fontSize":        {"magnitude": 28, "unit": "PT"},
                            "bold":            True,
                            "foregroundColor": {"color": {"rgbColor": DARK}},
                        },
                        "fields": "fontSize,bold,foregroundColor",
                    }},
                ]
            elif t == "h2":
                reqs += [
                    {"updateParagraphStyle": {
                        "range": {"startIndex": s, "endIndex": e},
                        "paragraphStyle": {
                            "namedStyleType": "HEADING_2",
                            "spaceAbove": {"magnitude": 22, "unit": "PT"},
                            "spaceBelow": {"magnitude":  5, "unit": "PT"},
                            "borderBottom": {
                                "color":     {"color": {"rgbColor": ORANGE}},
                                "width":     {"magnitude": 1.5, "unit": "PT"},
                                "padding":   {"magnitude": 4,   "unit": "PT"},
                                "dashStyle": "SOLID",
                            },
                        },
                        "fields": "namedStyleType,spaceAbove,spaceBelow,borderBottom",
                    }},
                    {"updateTextStyle": {
                        "range": {"startIndex": s, "endIndex": te},
                        "textStyle": {
                            "fontSize":        {"magnitude": 15, "unit": "PT"},
                            "bold":            True,
                            "foregroundColor": {"color": {"rgbColor": ORANGE}},
                        },
                        "fields": "fontSize,bold,foregroundColor",
                    }},
                ]
            elif t == "h3":
                reqs += [
                    {"updateParagraphStyle": {
                        "range": {"startIndex": s, "endIndex": e},
                        "paragraphStyle": {
                            "spaceAbove": {"magnitude": 14, "unit": "PT"},
                            "spaceBelow": {"magnitude":  4, "unit": "PT"},
                        },
                        "fields": "spaceAbove,spaceBelow",
                    }},
                    {"updateTextStyle": {
                        "range": {"startIndex": s, "endIndex": te},
                        "textStyle": {
                            "fontSize":        {"magnitude": 13, "unit": "PT"},
                            "bold":            True,
                            "italic":          False,
                            "foregroundColor": {"color": {"rgbColor": SLATE}},
                        },
                        "fields": "fontSize,bold,italic,foregroundColor",
                    }},
                ]
            elif t == "body":
                reqs += [
                    {"updateParagraphStyle": {
                        "range": {"startIndex": s, "endIndex": e},
                        "paragraphStyle": {
                            "lineSpacing": 150,
                            "spaceBelow": {"magnitude": 8, "unit": "PT"},
                        },
                        "fields": "lineSpacing,spaceBelow",
                    }},
                    {"updateTextStyle": {
                        "range": {"startIndex": s, "endIndex": te},
                        "textStyle": {
                            "fontSize":        {"magnitude": 11, "unit": "PT"},
                            "foregroundColor": {"color": {"rgbColor": DARK}},
                        },
                        "fields": "fontSize,foregroundColor",
                    }},
                ]

        svc.documents().batchUpdate(documentId=doc_id, body={"requests": reqs}).execute()

        # Batch 2: Pexels images
        hero_idx, additional_idxs = _find_image_positions(positions)
        topic_base = " ".join(
            title.replace("CODEC Research:", "").replace(":", "").replace("—", "").split()[:5]
        ).strip()

        img_tasks = [(hero_idx, topic_base)]
        for i_idx in additional_idxs:
            img_tasks.append((i_idx, _smart_query(topic_base, positions, i_idx)))

        SPEC_HERO  = (468, 260, "CENTER")
        SPEC_CYCLE = [
            (310, 174, "START"),
            (348, 196, "END"),
            (310, 174, "START"),
            (348, 196, "END"),
            (420, 236, "CENTER"),
            (310, 174, "START"),
        ]

        insert_points = []
        for i, (i_idx, query) in enumerate(img_tasks):
            url = _pexels_fetch_one(query, page_offset=i % 3)
            if not url:
                continue
            w, h, align = SPEC_HERO if i == 0 else SPEC_CYCLE[(i - 1) % len(SPEC_CYCLE)]
            insert_points.append({"idx": i_idx, "url": url, "w": w, "h": h, "align": align})

        insert_points.sort(key=lambda x: x["idx"], reverse=True)

        img_reqs = []
        for ip in insert_points:
            n = ip["idx"]
            img_reqs.append({"insertText": {"location": {"index": n}, "text": "\n"}})
            img_reqs.append({"insertInlineImage": {
                "location": {"index": n},
                "uri": ip["url"],
                "objectSize": {
                    "height": {"magnitude": ip["h"], "unit": "PT"},
                    "width":  {"magnitude": ip["w"], "unit": "PT"},
                },
            }})
            img_reqs.append({"updateParagraphStyle": {
                "range": {"startIndex": n, "endIndex": n + 2},
                "paragraphStyle": {"alignment": ip["align"]},
                "fields": "alignment",
            }})

        if img_reqs:
            svc.documents().batchUpdate(documentId=doc_id, body={"requests": img_reqs}).execute()

        print(f"[GDocs] Created: {doc_url} ({len(insert_points)} images)")
        return doc_url

    except Exception as e:
        print(f"[GDocs] Error: {e}")
        import traceback; traceback.print_exc()
        return None
