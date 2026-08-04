#!/usr/bin/env python3
"""Convert a Markdown file into EditorJS blocks for a lightning.ai blog post.

    python3 md2blocks.py post.md > blocks.json          # file or "-" for stdin
    python3 md2blocks.py post.md --keep-title           # keep the leading "# Title"

Prints the block JSON on stdout and a checklist of things needing attention
(figures, unsupported constructs) on stderr. Images keep their Markdown URL —
re-point them at uploaded lightning.ai URLs before publishing:

    jq '(.blocks[] | select(.type=="image") | .data.file.url) |= "https://…"'

Inline `<figure>`/`<svg>` become empty image blocks: rasterize the SVG, upload
it, and patch the URL in (see SKILL.md).
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys

INLINE_CODE = re.compile(r"`([^`]+)`")
BOLD = re.compile(r"\*\*([^*]+)\*\*")
ITALIC = re.compile(r"(?<![*\w])\*([^*\n]+)\*(?!\*)")
LINK = re.compile(r"!?\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
IMAGE_LINE = re.compile(r"^!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"([^\"]*)\")?\)\s*$")
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
ORDERED = re.compile(r"^(\s*)\d+[.)]\s+(.*)$")
FENCE = re.compile(r"^\s*```\s*([\w+-]*)\s*$")
RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
TABLE_SEP = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
SVG_TITLE = re.compile(r"<title>(.*?)</title>", re.S)


def inline(text: str) -> str:
    """Markdown inline syntax -> the HTML subset the blog editor renders."""
    placeholders: list[str] = []

    def hold(markup: str) -> str:
        placeholders.append(markup)
        return f"\x00{len(placeholders) - 1}\x00"

    # Extract links and code first so their contents survive escaping untouched.
    text = LINK.sub(
        lambda m: hold(f'<a href="{html.escape(m.group(2), quote=True)}">{html.escape(m.group(1))}</a>'),
        text,
    )
    text = INLINE_CODE.sub(lambda m: hold(f'<code class="inline-code">{html.escape(m.group(1))}</code>'), text)
    text = html.escape(text, quote=False)
    text = BOLD.sub(r"<b>\1</b>", text)
    text = ITALIC.sub(r"<i>\1</i>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: placeholders[int(m.group(1))], text)


def unwrap(lines: list[str]) -> str:
    return " ".join(line.strip() for line in lines).strip()


class Converter:
    def __init__(self, keep_title: bool, section_level: int) -> None:
        self.blocks: list[dict] = []
        self.notes: list[str] = []
        self.keep_title = keep_title
        self.section_level = section_level
        self.figures = 0

    def convert(self, text: str) -> dict:
        lines = text.replace("\r\n", "\n").split("\n")
        i, seen_title = 0, False
        while i < len(lines):
            line = lines[i]

            if not line.strip():
                i += 1
            elif m := FENCE.match(line):
                i = self.code(lines, i, m.group(1))
            elif m := HEADING.match(line):
                if m.group(1) == "#" and not seen_title and not self.keep_title:
                    seen_title = True  # the "# Title" belongs in the post metadata
                    i += 1
                    continue
                level = min(max(len(m.group(1)) - 1 + self.section_level, 1), 3)
                self.blocks.append({"type": "header", "data": {"text": inline(m.group(2)), "level": level}})
                i += 1
            elif RULE.match(line):
                self.blocks.append({"type": "delimiter", "data": {}})
                i += 1
            elif m := IMAGE_LINE.match(line):
                self.image(m.group(2), m.group(3) or m.group(1))
                i += 1
            elif line.lstrip().startswith("<"):
                i = self.raw_html(lines, i)
            elif TABLE_ROW.match(line) and i + 1 < len(lines) and TABLE_SEP.match(lines[i + 1]):
                i = self.table(lines, i)
            elif BULLET.match(line) or ORDERED.match(line):
                i = self.list(lines, i)
            elif line.lstrip().startswith(">"):
                i = self.quote(lines, i)
            else:
                i = self.paragraph(lines, i)

        return {"blocks": self.blocks}

    def image(self, url: str, caption: str) -> None:
        self.blocks.append(
            {
                "type": "image",
                "data": {
                    "file": {"url": url},
                    "caption": inline(caption),
                    "withBorder": False,
                    "stretched": False,
                    "withBackground": False,
                },
            }
        )
        if not url.startswith("https://storage.googleapis.com/lightning-avatars/"):
            self.notes.append(f"image block {len(self.blocks) - 1}: upload and re-point {url or '<empty>'}")

    def code(self, lines: list[str], i: int, language: str) -> int:
        body: list[str] = []
        i += 1
        while i < len(lines) and not FENCE.match(lines[i]):
            body.append(lines[i])
            i += 1
        self.blocks.append(
            {"type": "code", "data": {"code": "\n".join(body), "language": language or "python"}}
        )
        return i + 1

    def table(self, lines: list[str], i: int) -> int:
        rows: list[list[str]] = []
        while i < len(lines) and TABLE_ROW.match(lines[i]):
            if not TABLE_SEP.match(lines[i]):
                cells = TABLE_ROW.match(lines[i]).group(1).split("|")
                rows.append([inline(c.strip()) for c in cells])
            i += 1
        self.blocks.append({"type": "table", "data": {"withHeadings": True, "content": rows}})
        return i

    def list(self, lines: list[str], i: int) -> int:
        style = "ordered" if ORDERED.match(lines[i]) else "unordered"
        items: list[dict] = []
        stack: list[tuple[int, dict]] = []  # (indent, item)
        while i < len(lines):
            m = BULLET.match(lines[i]) or ORDERED.match(lines[i])
            if m:
                indent = len(m.group(1).expandtabs(4))
                item = {"content": inline(m.group(2)), "items": []}
                while stack and stack[-1][0] >= indent:
                    stack.pop()
                (stack[-1][1]["items"] if stack else items).append(item)
                stack.append((indent, item))
                i += 1
            elif lines[i].strip() and lines[i][:1].isspace() and stack:
                # continuation line of the current item
                stack[-1][1]["content"] += " " + inline(lines[i].strip())
                i += 1
            else:
                break
        self.blocks.append({"type": "list", "data": {"style": style, "items": items}})
        return i

    def quote(self, lines: list[str], i: int) -> int:
        body: list[str] = []
        while i < len(lines) and lines[i].lstrip().startswith(">"):
            body.append(lines[i].lstrip()[1:].strip())
            i += 1
        # No quote tool is registered in the blog editor — render as emphasis.
        self.blocks.append({"type": "paragraph", "data": {"text": f"<i>{inline(unwrap(body))}</i>"}})
        return i

    def raw_html(self, lines: list[str], i: int) -> int:
        chunk: list[str] = []
        depth = 0
        while i < len(lines):
            chunk.append(lines[i])
            depth += len(re.findall(r"<(figure|svg|div|table)\b", lines[i]))
            depth -= len(re.findall(r"</(figure|svg|div|table)>", lines[i]))
            i += 1
            if depth <= 0 and (not lines[i - 1].strip() or depth == 0 and chunk[0].lstrip().startswith("<")):
                if depth <= 0:
                    break
        blob = "\n".join(chunk)
        if "<svg" in blob or "<img" in blob or "<figure" in blob:
            self.figures += 1
            title = SVG_TITLE.search(blob)
            self.image("", title.group(1).strip() if title else "")
            self.notes.append(
                f"figure {self.figures}: rasterize the SVG, upload it, patch image block "
                f"{len(self.blocks) - 1}"
            )
        else:
            self.notes.append(f"dropped raw HTML near line {i}: {blob.splitlines()[0][:60]}")
        return i

    def paragraph(self, lines: list[str], i: int) -> int:
        body: list[str] = []
        while i < len(lines) and lines[i].strip():
            if (
                HEADING.match(lines[i])
                or FENCE.match(lines[i])
                or RULE.match(lines[i])
                or BULLET.match(lines[i])
                or ORDERED.match(lines[i])
                or lines[i].lstrip().startswith(("<", ">", "|"))
            ):
                break
            body.append(lines[i])
            i += 1
        text = unwrap(body)
        if text:
            self.blocks.append({"type": "paragraph", "data": {"text": inline(text)}})
        return i


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help='Markdown file, or "-" for stdin')
    parser.add_argument("--keep-title", action="store_true", help="keep the leading H1 as a block")
    parser.add_argument(
        "--section-level", type=int, default=1, help="header level for '##' sections (default 1)"
    )
    args = parser.parse_args()

    text = sys.stdin.read() if args.path == "-" else open(args.path).read()
    converter = Converter(args.keep_title, args.section_level)
    print(json.dumps(converter.convert(text), indent=1))

    counts: dict[str, int] = {}
    for block in converter.blocks:
        counts[block["type"]] = counts.get(block["type"], 0) + 1
    print(f"{len(converter.blocks)} blocks: {counts}", file=sys.stderr)
    for note in converter.notes:
        print(f"TODO {note}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
