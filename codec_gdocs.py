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
    """Lazy-load Pexels API key at call time (not import time).
    PR-2B-2 (D-15): sourced via Keychain-aware getter (cfg→Keychain migration
    on first call) with PEXELS_API_KEY env fallback inside the getter."""
    try:
        from codec_config import get_pexels_api_key
        return get_pexels_api_key()
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
    """Build a Pexels query anchored to the REPORT topic and the SECTION heading.

    Relevance fix (2026-07): the old version matched category buzzwords against
    the section BODY text, which misfired constantly — a stray word like
    "clinical" or "energy" in a sentence pulled a medical/energy stock photo into
    an unrelated report. Now every query leads with the report topic, adds the
    section's heading keywords, and only appends a category descriptor when the
    signal is in the HEADING (a far more reliable cue)."""
    heading = ""
    for p in positions:
        if p["type"] in ("h2", "h3") and p["start"] <= insert_idx:
            heading = p["text"]
    tb = _clean_topic_base(topic_base)          # report topic (from title)
    hk = _heading_keywords(heading)             # up to 2 salient heading words
    hl = heading.lower()

    # Category descriptor keyed on the HEADING only (reliable), never replacing topic.
    _CATS = [
        (["privacy", "security", "encryption", "breach", "cyber"], "cybersecurity data protection"),
        (["hardware", "chip", "processor", "npu", "gpu", "silicon", "semiconductor"], "computer hardware technology"),
        (["market", "adoption", "growth", "forecast", "revenue", "statistic", "trend"], "business data analytics chart"),
        (["energy", "power", "carbon", "sustainable", "climate", "green"], "sustainable energy"),
        (["medical", "health", "clinical", "pharma", "drug"], "medical health technology"),
        (["legal", "regulation", "regulatory", "compliance", "policy", "law"], "law regulation compliance"),
        (["forex", "currency", "trading", "exchange"], "forex currency trading"),
        (["ai", "artificial", "intelligence", "machine", "learning", "llm", "model", "neural"], "artificial intelligence technology"),
    ]
    descriptor = ""
    for kws, desc in _CATS:
        if any(k in hl for k in kws):
            descriptor = desc
            break

    parts = []
    if tb:
        parts.append(tb)
    if hk and hk.lower() not in (tb or "").lower():
        parts.append(hk)
    if descriptor:
        parts.append(descriptor)
    q = " ".join(parts).strip()
    if q:
        return q
    return f"{tb} professional business" if tb else "global business technology professional"


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
                rows = [[_strip_inline_md(c) for c in row] for row in table_lines]
                blocks.append({"type": "table", "rows": rows, "text": ""})
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


# ── Real Google Docs tables ─────────────────────────────────────────────────
# Markdown tables are inserted into the text as a one-line sentinel paragraph
# (⟦TABLE_n⟧). After the main text + images are laid down, we upgrade each
# sentinel into a native Docs table. Sentinels are located by text search (so
# they survive every prior index shift) and processed bottom-to-top. Each
# upgrade is independently guarded: on any failure the sentinel is replaced with
# readable flattened text, so a report never ships with a raw ⟦TABLE_n⟧ marker.
def _table_marker(n: int) -> str:
    return f"⟦TABLE_{n}⟧"


def _find_marker(svc, doc_id: str, marker: str):
    """Return (start, end) index range of `marker`'s text, or None."""
    doc = svc.documents().get(documentId=doc_id).execute()
    for el in doc.get("body", {}).get("content", []):
        para = el.get("paragraph")
        if not para:
            continue
        for pe in para.get("elements", []):
            tr = pe.get("textRun")
            if not tr:
                continue
            content = tr.get("content", "")
            off = content.find(marker)
            if off != -1:
                s = pe["startIndex"] + off
                return (s, s + len(marker))
    return None


def _replace_marker_with_text(svc, doc_id: str, marker: str, text: str):
    """Fallback: swap the sentinel for plain text (never leave a raw marker)."""
    loc = _find_marker(svc, doc_id, marker)
    if not loc:
        return
    s, e = loc
    reqs = [{"deleteContentRange": {"range": {"startIndex": s, "endIndex": e}}}]
    if text:
        reqs.append({"insertText": {"location": {"index": s}, "text": text}})
    svc.documents().batchUpdate(documentId=doc_id, body={"requests": reqs}).execute()


def _populate_table(svc, doc_id: str, near_idx: int, rows: list, orange, dark):
    """Fill the freshly-inserted table's cells (bottom-to-top) + style header."""
    doc = svc.documents().get(documentId=doc_id).execute()
    table_el = None
    for el in doc.get("body", {}).get("content", []):
        if el.get("table") and el.get("startIndex", 0) >= near_idx - 1:
            table_el = el
            break
    if not table_el:
        return
    trows = table_el["table"].get("tableRows", [])
    inserts = []  # (cell_start_index, text)
    for r, trow in enumerate(trows):
        for c, cell in enumerate(trow.get("tableCells", [])):
            cont = cell.get("content", [])
            if not cont:
                continue
            cell_start = cont[0].get("startIndex")
            if cell_start is None:
                continue
            text = rows[r][c] if (r < len(rows) and c < len(rows[r])) else ""
            if text:
                inserts.append((cell_start, text))
    # Bottom-to-top so earlier inserts don't shift later cell indices.
    inserts.sort(key=lambda x: x[0], reverse=True)
    reqs = [{"insertText": {"location": {"index": idx}, "text": txt}}
            for idx, txt in inserts]
    if reqs:
        svc.documents().batchUpdate(documentId=doc_id, body={"requests": reqs}).execute()

    # Header styling pass (best-effort; re-fetch for post-insert indices).
    try:
        doc2 = svc.documents().get(documentId=doc_id).execute()
        tbl2 = None
        for el in doc2.get("body", {}).get("content", []):
            if el.get("table") and el.get("startIndex", 0) >= near_idx - 1:
                tbl2 = el
                break
        if not tbl2:
            return
        style_reqs = []
        header_cells = tbl2["table"]["tableRows"][0].get("tableCells", [])
        for cell in header_cells:
            # Shade header cell background.
            style_reqs.append({"updateTableCellStyle": {
                "tableCellStyle": {"backgroundColor": {"color": {"rgbColor": orange}}},
                "fields": "backgroundColor",
                "tableCellLocation": {
                    "tableStartLocation": {"index": tbl2["startIndex"]},
                    "rowIndex": 0,
                    "columnIndex": header_cells.index(cell),
                },
            }})
            # Bold + white header text.
            for ce in cell.get("content", []):
                pel = ce.get("paragraph", {}).get("elements", [])
                for x in pel:
                    tr = x.get("textRun")
                    if tr and tr.get("content", "").strip():
                        style_reqs.append({"updateTextStyle": {
                            "range": {"startIndex": x["startIndex"], "endIndex": x["endIndex"] - 1},
                            "textStyle": {"bold": True,
                                          "foregroundColor": {"color": {"rgbColor": {"red": 1, "green": 1, "blue": 1}}}},
                            "fields": "bold,foregroundColor",
                        }})
        if style_reqs:
            svc.documents().batchUpdate(documentId=doc_id, body={"requests": style_reqs}).execute()
    except Exception as e:
        print(f"[GDocs] table header styling skipped: {e}")


def _insert_real_tables(svc, doc_id: str, tables: list, orange, dark):
    """Upgrade every ⟦TABLE_n⟧ sentinel into a native Docs table."""
    for t in range(len(tables) - 1, -1, -1):
        rows = tables[t]
        marker = _table_marker(t)
        if not rows:
            try:
                _replace_marker_with_text(svc, doc_id, marker, "")
            except Exception:
                pass
            continue
        n_rows = len(rows)
        n_cols = max(len(r) for r in rows)
        try:
            loc = _find_marker(svc, doc_id, marker)
            if not loc:
                continue
            s, e = loc
            # Delete the sentinel text, then insert an empty table in its place.
            svc.documents().batchUpdate(documentId=doc_id, body={"requests": [
                {"deleteContentRange": {"range": {"startIndex": s, "endIndex": e}}},
                {"insertTable": {"location": {"index": s}, "rows": n_rows, "columns": n_cols}},
            ]}).execute()
            _populate_table(svc, doc_id, s, rows, orange, dark)
            print(f"[GDocs] table {t}: rendered {n_rows}x{n_cols} native table")
        except Exception as ex:
            print(f"[GDocs] table {t} upgrade failed ({ex}); using text fallback")
            try:
                flat = "\n".join("   ".join(r) for r in rows)
                _replace_marker_with_text(svc, doc_id, marker, flat)
            except Exception:
                pass


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
    from googleapiclient.discovery import build
    from codec_google_auth import get_credentials

    DARK   = {"red": 0.082, "green": 0.082, "blue": 0.137}
    ORANGE = {"red": 0.910, "green": 0.443, "blue": 0.102}
    SLATE  = {"red": 0.173, "green": 0.243, "blue": 0.314}

    try:
        # Ensure all CODEC docs start with "CODEC:" prefix (avoid "CODEC: CODEC ...")
        if not title.upper().startswith("CODEC"):
            title = "CODEC: " + title
        elif title.startswith("CODEC ") and not title.startswith("CODEC:"):
            title = "CODEC: " + title[6:]

        creds = get_credentials()

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
        tables    = []   # rows per markdown table, upgraded to native tables last
        for block in blocks:
            if block.get("type") == "table":
                # Reserve a one-line sentinel paragraph; real table swapped in later.
                tables.append(block.get("rows") or [])
                block = {"type": "body", "text": _table_marker(len(tables) - 1)}
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
            if tables:
                _insert_real_tables(svc, doc_id, tables, ORANGE, DARK)
            print(f"[GDocs] Skipping images for: {title}")
            print(f"[GDocs] Created: {doc_url} (0 images, {len(tables)} tables)")
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

        # ── Batch 3: upgrade markdown-table sentinels into native tables ──
        if tables:
            _insert_real_tables(svc, doc_id, tables, ORANGE, DARK)

        print(f"[GDocs] Created: {doc_url} ({len(insert_points)} images, {len(tables)} tables)")
        return doc_url

    except Exception as e:
        print(f"[GDocs] Error: {e}")
        import traceback; traceback.print_exc()
        return None


def _doc_id_from(url_or_id: str) -> str | None:
    """Extract a Google Doc ID from a full docs URL or accept a bare ID."""
    if not url_or_id:
        return None
    s = url_or_id.strip()
    m = re.search(r"/document/d/([a-zA-Z0-9_-]+)", s)
    if m:
        return m.group(1)
    # Bare ID (Google Doc IDs are long alphanumeric strings with - and _)
    if re.fullmatch(r"[a-zA-Z0-9_-]{20,}", s):
        return s
    return None


def read_google_doc_text(url_or_id: str, max_chars: int = 60000) -> dict | None:
    """
    Read the plain-text body of a Google Doc the CODEC user owns/can access.
    Accepts a full docs URL or a bare doc ID. Uses the same Google OAuth as
    create_google_doc. Returns {"title", "text", "truncated"} or None on failure.
    Used by the chat "Discuss this report" handoff so chat-mode CODEC can talk
    about a report it generated in agent mode (the full report lives in the Doc,
    not in the short chat summary).
    """
    from googleapiclient.discovery import build
    from codec_google_auth import get_credentials

    doc_id = _doc_id_from(url_or_id)
    if not doc_id:
        print(f"[GDocs] read: could not parse doc id from {url_or_id!r}")
        return None
    try:
        creds = get_credentials()
        svc = build("docs", "v1", credentials=creds)
        doc = svc.documents().get(documentId=doc_id).execute()
        title = doc.get("title", "Untitled")
        text = ""
        for element in doc.get("body", {}).get("content", []):
            # Paragraph text
            for para in element.get("paragraph", {}).get("elements", []):
                text += para.get("textRun", {}).get("content", "")
            # Table text (reports render comparison tables)
            for row in element.get("table", {}).get("tableRows", []):
                cells = []
                for cell in row.get("tableCells", []):
                    cell_txt = ""
                    for ce in cell.get("content", []):
                        for pe in ce.get("paragraph", {}).get("elements", []):
                            cell_txt += pe.get("textRun", {}).get("content", "")
                    cells.append(cell_txt.strip())
                if any(cells):
                    text += " | ".join(cells) + "\n"
        truncated = len(text) > max_chars
        return {"title": title, "text": text[:max_chars], "truncated": truncated}
    except Exception as e:
        print(f"[GDocs] read error: {e}")
        return None
