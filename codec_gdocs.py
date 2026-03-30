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

def _get_pexels_key() -> str:
    """Lazy-load Pexels API key at call time (not import time)."""
    try:
        import json as _json
        with open(os.path.expanduser("~/.codec/config.json")) as _f:
            cfg = _json.load(_f)
        return cfg.get("pexels_api_key", os.environ.get("PEXELS_API_KEY", ""))
    except Exception:
        return os.environ.get("PEXELS_API_KEY", "")

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
            headers={"Authorization": _get_pexels_key()},
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


def _strip_inline_md(text: str) -> str:
    """Strip all inline markdown formatting to plain text."""
    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"\1", text)  # bold-italic
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)       # bold
    text = re.sub(r"__(.+?)__", r"\1", text)            # bold alt
    text = re.sub(r"\*(.+?)\*", r"\1", text)            # italic
    text = re.sub(r"_(.+?)_", r"\1", text)              # italic alt
    text = re.sub(r"`(.+?)`", r"\1", text)              # code
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)  # links
    return text


def _parse_markdown(content: str) -> list:
    """
    Parse markdown into blocks. Handles:
    - Headings (h1/h2/h3) with stripped inline formatting
    - Bullet points (* or - prefix)
    - Numbered list items (1. 2. etc.)
    - Tables (converted to clean formatted rows)
    - Body text stripped clean (matching deep_research quality)
    - Consecutive empty lines collapsed to one
    """
    blocks = []
    lines = content.split("\n")
    prev_empty = False
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # ── Headings ──
        if line.startswith("# "):
            prev_empty = False
            blocks.append({"text": _strip_inline_md(line[2:].strip()), "type": "h1"})
        elif line.startswith("## "):
            prev_empty = False
            blocks.append({"text": _strip_inline_md(line[3:].strip()), "type": "h2"})
        elif line.startswith("### "):
            prev_empty = False
            blocks.append({"text": _strip_inline_md(line[4:].strip()), "type": "h3"})

        # ── Horizontal rules ──
        elif stripped in ("---", "***", "___"):
            if not prev_empty:
                blocks.append({"text": "", "type": "empty"})
                prev_empty = True

        # ── Empty lines (collapse consecutive) ──
        elif stripped == "":
            if not prev_empty:
                blocks.append({"text": "", "type": "empty"})
                prev_empty = True

        # ── Markdown tables ──
        elif stripped.startswith("|") and "|" in stripped[1:]:
            prev_empty = False
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                tl = lines[i].strip()
                # Skip separator lines like |---|---|
                if re.match(r"^\|[\s\-:|\+]+\|$", tl):
                    i += 1
                    continue
                cells = [c.strip() for c in tl.split("|")]
                cells = [c for c in cells if c]
                table_lines.append(cells)
                i += 1
            if table_lines:
                header = table_lines[0]
                header_text = "   ".join(_strip_inline_md(c) for c in header)
                blocks.append({"text": header_text, "type": "table_header"})
                for row in table_lines[1:]:
                    row_text = "   ".join(_strip_inline_md(c) for c in row)
                    blocks.append({"text": row_text, "type": "table_row"})
            continue

        # ── Bullet points ──
        elif re.match(r"^[\s]*[\*\-\+]\s+", line):
            prev_empty = False
            bullet_text = re.sub(r"^[\s]*[\*\-\+]\s+", "", line)
            blocks.append({"text": _strip_inline_md(bullet_text), "type": "bullet"})

        # ── Numbered list items ──
        elif re.match(r"^[\s]*\d+[\.\)]\s+", line):
            prev_empty = False
            num_match = re.match(r"^[\s]*\d+[\.\)]\s+", line)
            item_text = line[num_match.end():]
            blocks.append({"text": _strip_inline_md(item_text), "type": "numbered"})

        # ── Regular body text ──
        else:
            prev_empty = False
            blocks.append({"text": _strip_inline_md(line), "type": "body"})

        i += 1

    return blocks


def create_google_doc(title: str, content: str) -> str | None:
    """
    Create a styled Google Doc with:
    - H1: 28pt, bold, centered, dark
    - H2: 15pt, bold, CODEC orange, bottom border
    - H3: 13pt, bold, slate
    - Body: 11pt, 150% line spacing
    - Bullet points with proper Google Docs bullets
    - Tables rendered as styled header + data rows
    - Inline bold preserved within body text
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
    LGRAY  = {"red": 0.933, "green": 0.933, "blue": 0.933}   # #EEEEEE table bg

    try:
        # Ensure all CODEC docs start with "CODEC:" prefix
        if not title.startswith("CODEC:"):
            title = "CODEC: " + title

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
            positions.append({
                "start": idx, "end": idx + len(line),
                "type": block["type"], "text": block["text"],
            })
            full_text += line
            idx += len(line)

        # ── Batch 1: insert text + apply styles ──
        reqs = [{"insertText": {"location": {"index": 1}, "text": full_text}}]

        # Track bullet/numbered ranges for createParagraphBullets
        bullet_ranges = []
        numbered_ranges = []

        for p in positions:
            s, e, t = p["start"], p["end"], p["type"]
            te = e - 1  # text end (exclude trailing \n)
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
                            "spaceBelow": {"magnitude": 8, "unit": "PT"},
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
                            "spaceAbove": {"magnitude": 18, "unit": "PT"},
                            "spaceBelow": {"magnitude":  4, "unit": "PT"},
                            "borderBottom": {
                                "color":     {"color": {"rgbColor": ORANGE}},
                                "width":     {"magnitude": 1.5, "unit": "PT"},
                                "padding":   {"magnitude": 3,   "unit": "PT"},
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
                            "spaceAbove": {"magnitude": 12, "unit": "PT"},
                            "spaceBelow": {"magnitude":  3, "unit": "PT"},
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

            elif t == "table_header":
                reqs += [
                    {"updateParagraphStyle": {
                        "range": {"startIndex": s, "endIndex": e},
                        "paragraphStyle": {
                            "spaceAbove": {"magnitude": 6, "unit": "PT"},
                            "spaceBelow": {"magnitude": 2, "unit": "PT"},
                        },
                        "fields": "spaceAbove,spaceBelow",
                    }},
                    {"updateTextStyle": {
                        "range": {"startIndex": s, "endIndex": te},
                        "textStyle": {
                            "fontSize":        {"magnitude": 10, "unit": "PT"},
                            "bold":            True,
                            "foregroundColor": {"color": {"rgbColor": DARK}},
                        },
                        "fields": "fontSize,bold,foregroundColor",
                    }},
                ]

            elif t == "table_row":
                reqs += [
                    {"updateParagraphStyle": {
                        "range": {"startIndex": s, "endIndex": e},
                        "paragraphStyle": {
                            "spaceAbove": {"magnitude": 0, "unit": "PT"},
                            "spaceBelow": {"magnitude": 1, "unit": "PT"},
                        },
                        "fields": "spaceAbove,spaceBelow",
                    }},
                    {"updateTextStyle": {
                        "range": {"startIndex": s, "endIndex": te},
                        "textStyle": {
                            "fontSize":        {"magnitude": 10, "unit": "PT"},
                            "foregroundColor": {"color": {"rgbColor": DARK}},
                        },
                        "fields": "fontSize,foregroundColor",
                    }},
                ]

            elif t == "bullet":
                bullet_ranges.append({"startIndex": s, "endIndex": e})
                reqs += [
                    {"updateParagraphStyle": {
                        "range": {"startIndex": s, "endIndex": e},
                        "paragraphStyle": {
                            "lineSpacing": 140,
                            "spaceBelow": {"magnitude": 3, "unit": "PT"},
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

            elif t == "numbered":
                numbered_ranges.append({"startIndex": s, "endIndex": e})
                reqs += [
                    {"updateParagraphStyle": {
                        "range": {"startIndex": s, "endIndex": e},
                        "paragraphStyle": {
                            "lineSpacing": 140,
                            "spaceBelow": {"magnitude": 3, "unit": "PT"},
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

            elif t == "body":
                reqs += [
                    {"updateParagraphStyle": {
                        "range": {"startIndex": s, "endIndex": e},
                        "paragraphStyle": {
                            "lineSpacing": 150,
                            "spaceBelow": {"magnitude": 4, "unit": "PT"},
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

        # Apply bullet list formatting
        for br in bullet_ranges:
            reqs.append({"createParagraphBullets": {
                "range": br,
                "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
            }})

        # Apply numbered list formatting
        for nr in numbered_ranges:
            reqs.append({"createParagraphBullets": {
                "range": nr,
                "bulletPreset": "NUMBERED_DECIMAL_NESTED",
            }})

        svc.documents().batchUpdate(documentId=doc_id, body={"requests": reqs}).execute()

        # ── Batch 2: Pexels images ──
        hero_idx, additional_idxs = _find_image_positions(positions)
        topic_base = " ".join(
            title.replace("CODEC Research:", "").replace("CODEC Report", "")
                 .replace(":", "").replace("\u2014", "").split()[:5]
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
