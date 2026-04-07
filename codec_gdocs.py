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
_CHARS_PER_IMAGE = 6_000   # ~2 pages between images
_MAX_IMAGES      = 10

# Words that produce garbage Pexels results — strip from queries
_NOISE_WORDS = {
    "daily", "briefing", "report", "update", "digest", "summary",
    "codec", "weekly", "monthly", "morning", "evening", "newsletter",
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
    "monday", "tuesday", "wednesday", "thursday", "friday",
    "saturday", "sunday", "today", "edition",
}

# Curated fallback queries that always produce professional imagery
_PROFESSIONAL_FALLBACKS = [
    "modern office business skyline",
    "global finance stock market data",
    "technology innovation workspace",
    "world map global economy",
    "corporate strategy meeting",
    "digital analytics dashboard",
    "city skyline sunrise business district",
    "trading floor financial markets",
]


def _pexels_fetch_one(query: str, page_offset: int = 0):
    """Fetch one landscape photo from Pexels, with relevance filtering."""
    try:
        resp = rq.get(
            "https://api.pexels.com/v1/search",
            params={"query": query, "per_page": 15, "page": page_offset + 1,
                    "orientation": "landscape"},
            headers={"Authorization": _get_pexels_key()},
            timeout=10,
        )
        photos = resp.json().get("photos", [])
        if not photos:
            return None
        # Filter out photos whose alt text suggests irrelevant content
        _reject = {"scrabble", "letter", "tile", "wood block", "note", "sticky",
                    "sign", "paper", "handwriting", "written", "text on",
                    "copy space", "flat lay", "mockup", "mock up", "placeholder"}
        for photo in photos:
            alt = (photo.get("alt") or "").lower()
            if any(rw in alt for rw in _reject):
                continue
            return photo["src"]["large2x"]
        # All photos looked irrelevant — skip rather than insert garbage
        print(f"[GDOCS] Pexels: all results irrelevant for '{query}', skipping")
        return None
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


def _clean_topic_base(raw: str) -> str:
    """Strip noise words (dates, generic labels) so Pexels gets useful queries."""
    words = raw.split()
    cleaned = [w for w in words
               if w.lower() not in _NOISE_WORDS and not w.isdigit() and len(w) > 2]
    return " ".join(cleaned).strip()


def _smart_query(topic_base: str, positions: list, insert_idx: int) -> str:
    heading = ""
    for p in positions:
        if p["type"] in ("h2", "h3") and p["start"] <= insert_idx:
            heading = p["text"]
    body = _section_body_text(positions, insert_idx)
    ctx  = (heading + " " + body).lower()
    kw   = _heading_keywords(heading)

    # Clean noise from topic_base so queries don't contain dates/generic words
    tb = _clean_topic_base(topic_base)

    if any(w in ctx for w in ["statistic", "percent", "%", "billion", "million",
                               "market", "adoption", "growth", "forecast"]):
        return f"{tb} data analytics business" if tb else "financial data analytics business"
    if any(w in ctx for w in ["hardware", "chip", "processor", "npu", "gpu", "silicon"]):
        return f"{tb} computer hardware chip" if tb else "computer hardware technology chip"
    if any(w in ctx for w in ["privacy", "security", "encryption", "gdpr", "breach"]):
        return f"{tb} cybersecurity data protection" if tb else "cybersecurity data protection"
    if any(w in ctx for w in ["global", "enterprise", "fortune", "deployment"]):
        return f"{tb} global enterprise technology" if tb else "global enterprise technology"
    if any(w in ctx for w in ["energy", "power", "carbon", "sustainable", "green"]):
        return f"{tb} sustainable energy environment" if tb else "sustainable energy environment"
    if any(w in ctx for w in ["medical", "health", "drug", "clinical", "pharma"]):
        return f"{tb} medical health technology" if tb else "medical health technology"
    if any(w in ctx for w in ["legal", "regulatory", "law", "regulation", "executive order"]):
        return f"{tb} law regulation compliance" if tb else "law regulation compliance"
    if any(w in ctx for w in ["future", "forecast", "trend", "next", "emerging"]):
        return f"{tb} future innovation technology" if tb else "future innovation technology"
    if any(w in ctx for w in ["forex", "currency", "trading", "exchange rate"]):
        return f"{tb} forex currency trading" if tb else "forex currency trading markets"
    if any(w in ctx for w in ["ai", "artificial intelligence", "machine learning", "llm", "gpt"]):
        return f"{tb} artificial intelligence technology" if tb else "artificial intelligence technology"
    if kw:
        return f"{tb} {kw}" if tb else f"professional business {kw}"
    # Fallback: use cleaned base or a professional default
    return f"{tb} technology business" if tb else "global business technology professional"


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
    - Up to 7 Pexels images (hero full-width, rest centered and consistent size)
    - Images skipped for invoices, meeting summaries, code reviews, etc.
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
        # Ensure all CODEC docs start with "CODEC:" prefix (avoid "CODEC: CODEC ...")
        if not title.upper().startswith("CODEC"):
            title = "CODEC: " + title
        elif title.startswith("CODEC ") and not title.startswith("CODEC:"):
            title = "CODEC: " + title[6:]

        creds = Credentials.from_authorized_user_file(TOKEN_PATH)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(TOKEN_PATH, "w") as f:
                f.write(creds.to_json())

        svc    = build("docs", "v1", credentials=creds)
        doc    = svc.documents().create(body={"title": title}).execute()
        doc_id  = doc["documentId"]
        doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"

        # Share doc as "anyone with link can view" so returned URLs are accessible
        try:
            drive_svc = build("drive", "v3", credentials=creds)
            drive_svc.permissions().create(
                fileId=doc_id,
                body={"type": "anyone", "role": "reader"},
                fields="id",
            ).execute()
        except Exception as share_err:
            print(f"[GDocs] Warning: could not set sharing permissions: {share_err}")

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
        # Skip images for document types that don't need them
        _title_lower = title.lower()
        _skip_images = any(kw in _title_lower for kw in [
            "invoice", "meeting summary", "meeting notes", "minutes",
            "social media posts", "code review", "email",
        ])
        if _skip_images:
            print(f"[GDocs] Skipping images for: {title}")
            print(f"[GDocs] Created: {doc_url} (0 images)")
            return doc_url

        hero_idx, additional_idxs = _find_image_positions(positions)
        raw_topic = " ".join(
            title.replace("CODEC Research:", "").replace("CODEC Report", "")
                 .replace("CODEC:", "").replace(":", "").replace("\u2014", "").split()[:5]
        ).strip()
        topic_base = _clean_topic_base(raw_topic)

        # For the hero image, use a broad professional query
        if topic_base:
            hero_query = f"{topic_base} professional business"
        else:
            hero_query = _PROFESSIONAL_FALLBACKS[0]

        img_tasks = [(hero_idx, hero_query)]
        for i_idx in additional_idxs:
            img_tasks.append((i_idx, _smart_query(topic_base, positions, i_idx)))

        SPEC_HERO  = (468, 260, "CENTER")
        # All images centered and consistent size for clean look
        SPEC_CYCLE = [
            (420, 236, "CENTER"),
            (420, 236, "CENTER"),
            (420, 236, "CENTER"),
            (420, 236, "CENTER"),
            (420, 236, "CENTER"),
            (420, 236, "CENTER"),
        ]

        insert_points = []
        for i, (i_idx, query) in enumerate(img_tasks):
            url = _pexels_fetch_one(query, page_offset=i % 3)
            # If primary query failed, try a curated professional fallback
            if not url:
                fallback_q = _PROFESSIONAL_FALLBACKS[i % len(_PROFESSIONAL_FALLBACKS)]
                print(f"[GDOCS] Trying fallback query: '{fallback_q}'")
                url = _pexels_fetch_one(fallback_q, page_offset=0)
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
