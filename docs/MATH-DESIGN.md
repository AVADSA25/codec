# MATH — Math and diagrams in replies

Item 12 of docs/UI-PHASE2-3-PLAN.md.

## Why

Replies that contain formulas or Mermaid diagrams show their raw source: `$\frac{a}{b}$` and a ```` ```mermaid ````
code block. Chat, Home and Tasks share one renderer (`static/codec-md.js`), so one change covers all three.

## What

- **Vendored, never from a CDN at run time.** KaTeX 0.18.9 (`dist/katex.min.js`, `dist/katex.min.css`, the 20
  `dist/fonts/*.woff2`) into `static/vendor/katex/`, and Mermaid 12.0.0 (`dist/mermaid.min.js`, 5.3 MB) into
  `static/vendor/`, from cdn.jsdelivr.net (download approved 2026-09-29). Each file matched jsDelivr's published
  SHA-256; `static/vendor/SOURCES.md` gets a row per file. The two MIT licence texts sit next to them (not served:
  the `/static` mount serves only `.css .svg .woff2 .js .png`). The KaTeX stylesheet asks for `.woff2` first, so
  the `.woff`/`.ttf` fallbacks are not vendored.
- **Loaded only when a reply needs them.** The renderer adds `<script>`/`<link>` tags for KaTeX the first time a
  rendered reply holds math, and for Mermaid the first time a finished reply holds a diagram. The URLs carry the
  version (`?v=0.18.9`, `?v=12.0.0`), so an upgrade is not served from an old cache. No page loads either up front.
- **Math.** A marked extension finds `$...$` and `\(...\)` (inline) and `$$...$$` and `\[...\]` (display; a
  ```` ```math ```` block too). Models write both notations, so both are read. Money stays text: an inline `$...$`
  must not start or end with a space, and its closing `$` must not run into a digit, so "$5 and $10" is not math.
  The extension only marks the formula (`<code class="language-math…">`, which the existing DOMPurify config
  already keeps); after sanitizing, KaTeX turns each mark into math (`trust: false`, `throwOnError: false`,
  `maxExpand` and `maxSize` limits, formulas over 4,000 characters stay as code). KaTeX's output goes through its
  own DOMPurify instance (HTML, MathML and SVG profiles; its classes and inline sizes kept; no ids, no links, no
  `url(` in a style) before it reaches the page. Without KaTeX (load failure) the formula stays readable as code.
  Math renders while a reply streams (cached per formula).
- **Diagrams.** A ```` ```mermaid ```` block in a finished reply (not while it streams) is drawn with Mermaid
  (`securityLevel: 'strict'`, `startOnLoad: false`, labels as SVG text, not HTML; errors are not drawn into the
  page), then the SVG goes through a DOMPurify instance with the SVG profile: no scripts, handlers, foreignObject
  or images, links and `url(...)` only to the diagram's own `#ids`, ids only with the diagram's own prefix. The
  result is shown as an image (`<img src="data:image/svg+xml,...">`), so nothing in it can run, load an address
  or touch the page's ids and classes. The box has a "Diagram" header with Copy (the source) and Download (the
  SVG). The Mermaid theme follows the page theme when the diagram is drawn; the box keeps a matching background if
  the theme changes later. A diagram Mermaid cannot read stays as its code block, labelled so.
- The renderer's API gains `texHtml(tex, display)` and `safeSvg(svg, idPrefix)` (the two sanitizing steps, also
  used by the tests).

## API or schema change

None on the server. `window.codecMarkdown` gains `texHtml` and `safeSvg`. New static files only.

## Test plan

tests/test_markdown_math.py: every vendored file is present and matches its SOURCES.md SHA-256; `/static` serves
them with the right types; no page loads KaTeX or Mermaid up front and the renderer loads them by version; Mermaid
is set up with `securityLevel: 'strict'` and KaTeX with `trust: false`, and both outputs pass through DOMPurify.
Under jsdom (skipped when node cannot find jsdom): `$x^2$`, `\(\alpha\)`, `$$...$$`, `\[...\]` and a ```` ```math ````
block become math marks and "$5 and $10" does not; with KaTeX loaded, formulas render and `\href{javascript:...}`
is inert; `safeSvg` strips script, handlers, foreignObject, outside links, outside `url(...)` and foreign ids.

Browser (throwaway dashboard): a saved chat and a streamed reply with inline and display math, money, a flowchart
and a broken diagram; KaTeX and Mermaid requested only when needed; Home and Tasks render the same; 1470x956,
375x812, light theme.

## Rollback

Revert the PR; replies show the source again. The vendored files go with it.
