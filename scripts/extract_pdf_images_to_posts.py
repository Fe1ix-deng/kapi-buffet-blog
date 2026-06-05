#!/usr/bin/env python3
"""Place PDF images in posts using nearby PDF text as anchors.

Requires:
    pip install pymupdf

Default behavior:
    - Reads PDFs from pdf_files/
    - Matches each PDF to a post in src/content/posts/
    - Extracts text blocks and image positions from the PDF
    - Skips the first image in each PDF as the cover image
    - Saves remaining images to public/images/{pdf-stem}/01.jpg, 02.jpg, ...
    - Finds the nearest text block above each image and inserts the image after
      the matching Markdown paragraph
    - Clears old /images/ Markdown refs before inserting the new positions
"""

from __future__ import annotations

import argparse
import re
import shutil
import string
import sys
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import fitz


DATE_RE = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})")
FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n?", re.DOTALL)
IMAGE_PARAGRAPH_RE = re.compile(r"^\s*!\[[^\]]*\]\(/images/[^)]+\)\s*$")
CHINESE_RE = re.compile(r"[\u4e00-\u9fff]+")
ASCII_WORD_RE = re.compile(r"[A-Za-z0-9]+")


@dataclass(frozen=True)
class PdfInfo:
    path: Path
    stem: str
    date: str | None
    keywords: set[str]


@dataclass(frozen=True)
class PostInfo:
    path: Path
    date: str | None
    title: str
    keywords: set[str]


@dataclass(frozen=True)
class TextBlock:
    page_index: int
    y0: float
    y1: float
    text: str


@dataclass(frozen=True)
class PdfImage:
    page_index: int
    x0: float
    y0: float
    y1: float
    xref: int
    order: int
    anchor_text: str
    anchor_candidates: tuple[str, ...]
    image_bytes: bytes


@dataclass
class ImagePlacement:
    image: PdfImage
    url: str
    paragraph_index: int | None = None
    matched_anchor: str = ""
    used_fallback: bool = False


@dataclass
class ProcessResult:
    pdf: Path
    post: Path | None
    status: str
    score: float = 0.0
    reason: str = ""
    images_found: int = 0
    images_saved: int = 0
    images_inserted: int = 0
    fallback_inserted: int = 0
    missing_anchors: list[str] = field(default_factory=list)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract PDF images and place them after matching Markdown anchor text."
    )
    parser.add_argument("--pdf-dir", type=Path, default=Path("pdf_files"))
    parser.add_argument("--posts-dir", type=Path, default=Path("src/content/posts"))
    parser.add_argument("--images-dir", type=Path, default=Path("public/images"))
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing images or Markdown files.",
    )
    return parser.parse_args()


def normalize_stem(stem: str) -> str:
    stem = re.sub(r"^\[\d{4}-\d{2}-\d{2}\]", "", stem)
    stem = re.sub(r"[_\-\s]+", "", stem)
    return stem.strip().lower()


def normalize_for_match(text: str) -> str:
    punctuation = string.punctuation + "，。！？；：、（）【】《》“”‘’·…￥—「」『』"
    translation = str.maketrans("", "", punctuation)
    return re.sub(r"\s+", "", text.lower().translate(translation))


def tokenize(text: str) -> set[str]:
    tokens: set[str] = set()
    compact = normalize_for_match(text)

    for word in ASCII_WORD_RE.findall(compact):
        if len(word) >= 2:
            tokens.add(word)

    for chunk in CHINESE_RE.findall(compact):
        if len(chunk) <= 6:
            tokens.add(chunk)
        else:
            for size in (2, 3, 4):
                for i in range(0, len(chunk) - size + 1):
                    tokens.add(chunk[i : i + size])

    return tokens


def extract_date(text: str) -> str | None:
    match = DATE_RE.search(text)
    return match.group("date") if match else None


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def parse_title(markdown: str) -> str:
    match = re.search(r'^title:\s*["\']?(.*?)["\']?\s*$', markdown, re.MULTILINE)
    return match.group(1).strip() if match else ""


def build_pdf_info(pdf_path: Path) -> PdfInfo:
    stem = pdf_path.stem
    return PdfInfo(
        path=pdf_path,
        stem=stem,
        date=extract_date(stem),
        keywords=tokenize(normalize_stem(stem)),
    )


def build_post_info(post_path: Path) -> PostInfo:
    markdown = read_text(post_path)
    title = parse_title(markdown)
    searchable = f"{post_path.stem} {title} {markdown[:1200]}"
    return PostInfo(
        path=post_path,
        date=extract_date(post_path.name) or extract_date(markdown),
        title=title,
        keywords=tokenize(searchable),
    )


def title_similarity(pdf: PdfInfo, post: PostInfo) -> float:
    pdf_title = normalize_stem(pdf.stem)
    post_title = normalize_stem(post.title or post.path.stem)
    if not pdf_title or not post_title:
        return 0.0

    ratio = SequenceMatcher(None, pdf_title, post_title).ratio()
    overlap = len(pdf.keywords & post.keywords) / max(len(pdf.keywords), 1)
    return max(ratio, overlap)


def find_matching_post(pdf: PdfInfo, posts: list[PostInfo]) -> tuple[PostInfo | None, float, str]:
    same_date = [post for post in posts if pdf.date and post.date == pdf.date]
    if len(same_date) == 1:
        return same_date[0], 1.0, "date"
    if len(same_date) > 1:
        ranked = sorted(
            ((title_similarity(pdf, post), post) for post in same_date),
            key=lambda item: item[0],
            reverse=True,
        )
        if ranked[0][0] >= 0.2 and (len(ranked) == 1 or ranked[0][0] > ranked[1][0]):
            return ranked[0][1], ranked[0][0], "date+keywords"
        return None, ranked[0][0], "ambiguous same-date posts"

    scored: list[tuple[float, PostInfo]] = []
    for post in posts:
        score = title_similarity(pdf, post)
        if pdf.date and post.date:
            try:
                days = abs(
                    (
                        datetime.strptime(pdf.date, "%Y-%m-%d")
                        - datetime.strptime(post.date, "%Y-%m-%d")
                    ).days
                )
            except ValueError:
                days = 999
            if days == 1:
                score += 0.18
            elif days <= 3:
                score += 0.08
        scored.append((min(score, 1.0), post))

    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored:
        return None, 0.0, "no posts found"

    best_score, best_post = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    if best_score >= 0.38 and best_score - second_score >= 0.05:
        return best_post, best_score, "keywords"

    return None, best_score, "no confident match"


def clean_block_text(text: str) -> str:
    return re.sub(r"\s+", "", text).strip()


def anchor_from_text(text: str, length: int = 20) -> str:
    return normalize_for_match(clean_block_text(text))[:length]


def pixmap_to_jpeg_bytes(doc: fitz.Document, xref: int) -> bytes:
    pix = fitz.Pixmap(doc, xref)
    if pix.alpha or pix.colorspace is None or pix.n not in (1, 3):
        pix = fitz.Pixmap(fitz.csRGB, pix)
    return pix.tobytes("jpeg")


def image_rects_for_page(page: fitz.Page, image: tuple) -> list[fitz.Rect]:
    try:
        bbox = page.get_image_bbox(image)
    except Exception:
        bbox = fitz.Rect()

    if bbox and not bbox.is_empty and not bbox.is_infinite:
        return [bbox]

    xref = image[0]
    try:
        return [rect for rect in page.get_image_rects(xref) if not rect.is_empty]
    except Exception:
        return []


def extract_text_blocks_and_images(pdf_path: Path) -> tuple[list[TextBlock], list[PdfImage]]:
    doc = fitz.open(pdf_path)
    text_blocks: list[TextBlock] = []
    image_candidates: list[tuple[int, float, float, float, int, int, bytes]] = []
    image_order = 0

    for page_index, page in enumerate(doc):
        for block in page.get_text("blocks"):
            if len(block) < 7:
                continue
            x0, y0, x1, y1, text, _block_no, block_type = block
            if block_type != 0:
                continue
            cleaned = clean_block_text(str(text))
            if len(normalize_for_match(cleaned)) < 4:
                continue
            text_blocks.append(TextBlock(page_index=page_index, y0=float(y0), y1=float(y1), text=cleaned))

        for image in page.get_images(full=True):
            xref = image[0]
            for rect in image_rects_for_page(page, image):
                image_order += 1
                image_candidates.append(
                    (
                        page_index,
                        float(rect.x0),
                        float(rect.y0),
                        float(rect.y1),
                        xref,
                        image_order,
                        pixmap_to_jpeg_bytes(doc, xref),
                    )
                )

    images: list[PdfImage] = []
    sorted_candidates = sorted(image_candidates, key=lambda item: (item[0], item[2], item[1], item[5]))
    for page_index, x0, y0, y1, xref, order, image_bytes in sorted_candidates:
        anchor_blocks = find_anchor_blocks(text_blocks, page_index, y0)
        anchor_candidates = unique_anchors(anchor_from_text(block.text) for block in anchor_blocks)
        images.append(
            PdfImage(
                page_index=page_index,
                x0=x0,
                y0=y0,
                y1=y1,
                xref=xref,
                order=order,
                anchor_text=anchor_candidates[0] if anchor_candidates else "",
                anchor_candidates=tuple(anchor_candidates),
                image_bytes=image_bytes,
            )
        )

    return text_blocks, images


def unique_anchors(anchors: object) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for anchor in anchors:
        if not isinstance(anchor, str) or not anchor or anchor in seen:
            continue
        seen.add(anchor)
        unique.append(anchor)
    return unique


def find_anchor_blocks(text_blocks: list[TextBlock], page_index: int, image_y0: float) -> list[TextBlock]:
    same_page = [
        block
        for block in text_blocks
        if block.page_index == page_index and block.y1 <= image_y0 and anchor_from_text(block.text)
    ]
    if same_page:
        return sorted(same_page, key=lambda block: block.y1, reverse=True)[:8]

    previous_page = [
        block for block in text_blocks if block.page_index == page_index - 1 and anchor_from_text(block.text)
    ]
    if previous_page:
        return sorted(previous_page, key=lambda block: block.y1, reverse=True)[:8]

    return []


def split_frontmatter(markdown: str) -> tuple[str, str]:
    match = FRONTMATTER_RE.match(markdown)
    if not match:
        return "", markdown
    return match.group(0), markdown[match.end() :]


def split_paragraphs(body: str) -> list[str]:
    stripped = body.strip("\n")
    if not stripped:
        return []
    return re.split(r"\n{2,}", stripped)


def remove_existing_image_refs(markdown: str) -> str:
    frontmatter, body = split_frontmatter(markdown)
    paragraphs = [paragraph for paragraph in split_paragraphs(body) if not IMAGE_PARAGRAPH_RE.match(paragraph)]
    new_body = "\n\n".join(paragraph.strip("\n") for paragraph in paragraphs).rstrip()
    return frontmatter + (new_body + "\n" if new_body else "")


def find_anchor_paragraph(anchor: str, paragraphs: list[str]) -> tuple[int | None, str]:
    normalized_paragraphs = [normalize_for_match(paragraph) for paragraph in paragraphs]

    for length in range(min(len(anchor), 20), 7, -1):
        needle = anchor[:length]
        if not needle:
            continue
        for index, normalized in enumerate(normalized_paragraphs):
            if needle in normalized:
                return index, needle

    best_index: int | None = None
    best_score = 0.0
    for index, normalized in enumerate(normalized_paragraphs):
        if not normalized:
            continue
        window_size = min(max(len(anchor), 8), len(normalized))
        for start in range(0, max(len(normalized) - window_size + 1, 1)):
            window = normalized[start : start + window_size]
            score = SequenceMatcher(None, anchor, window).ratio()
            if score > best_score:
                best_index = index
                best_score = score

    if best_index is not None and best_score >= 0.82:
        return best_index, anchor

    return None, ""


def public_image_url(image_path: Path) -> str:
    try:
        relative = image_path.relative_to(Path("public"))
    except ValueError:
        relative = image_path
    return "/" + relative.as_posix()


def write_extracted_images(pdf: PdfInfo, images: list[PdfImage], images_dir: Path, dry_run: bool) -> list[str]:
    output_dir = images_dir / pdf.stem
    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    urls: list[str] = []
    for index, image in enumerate(images, start=1):
        image_path = output_dir / f"{index:02d}.jpg"
        urls.append(public_image_url(image_path))
        if not dry_run:
            image_path.write_bytes(image.image_bytes)
    return urls


def apply_placements(markdown: str, placements: list[ImagePlacement]) -> tuple[str, int, int, list[str]]:
    cleaned = remove_existing_image_refs(markdown)
    frontmatter, body = split_frontmatter(cleaned)
    paragraphs = split_paragraphs(body)

    placements_by_paragraph: dict[int, list[ImagePlacement]] = {}
    missing: list[str] = []
    fallback_count = 0
    last_paragraph_index: int | None = None
    for placement in placements:
        paragraph_index = None
        matched_anchor = ""
        for anchor in placement.image.anchor_candidates or (placement.image.anchor_text,):
            paragraph_index, matched_anchor = find_anchor_paragraph(anchor, paragraphs)
            if paragraph_index is not None:
                break
        if paragraph_index is None:
            if paragraphs:
                paragraph_index = last_paragraph_index if last_paragraph_index is not None else 0
                placement.used_fallback = True
                fallback_count += 1
            else:
                missing.append(placement.image.anchor_text or "(no anchor text)")
                continue
        placement.paragraph_index = paragraph_index
        placement.matched_anchor = matched_anchor
        last_paragraph_index = paragraph_index
        placements_by_paragraph.setdefault(paragraph_index, []).append(placement)

    rebuilt: list[str] = []
    inserted = 0
    for index, paragraph in enumerate(paragraphs):
        rebuilt.append(paragraph)
        for placement in placements_by_paragraph.get(index, []):
            rebuilt.append(f"![{Path(placement.url).stem}]({placement.url})")
            inserted += 1

    new_body = "\n\n".join(part.strip("\n") for part in rebuilt).rstrip()
    return frontmatter + (new_body + "\n" if new_body else ""), inserted, fallback_count, missing


def process_pdf(pdf: PdfInfo, posts: list[PostInfo], images_dir: Path, dry_run: bool) -> ProcessResult:
    post, score, reason = find_matching_post(pdf, posts)
    result = ProcessResult(pdf=pdf.path, post=post.path if post else None, status="", score=score, reason=reason)

    if post is None:
        result.status = "unmatched"
        return result

    _text_blocks, images = extract_text_blocks_and_images(pdf.path)
    result.images_found = len(images)

    images_to_place = images[1:]
    if not images_to_place:
        original = read_text(post.path)
        updated = remove_existing_image_refs(original)
        if not dry_run and updated != original:
            post.path.write_text(updated, encoding="utf-8")
        result.status = "matched_no_insertable_images"
        return result

    urls = write_extracted_images(pdf, images_to_place, images_dir, dry_run)
    placements = [ImagePlacement(image=image, url=url) for image, url in zip(images_to_place, urls)]
    original = read_text(post.path)
    updated, inserted, fallback_count, missing = apply_placements(original, placements)

    result.images_saved = len(urls)
    result.images_inserted = inserted
    result.fallback_inserted = fallback_count
    result.missing_anchors = missing
    result.status = "matched" if not missing else "matched_with_missing_anchors"

    if not dry_run:
        post.path.write_text(updated, encoding="utf-8")

    return result


def clear_images_dir(images_dir: Path, dry_run: bool) -> None:
    if dry_run:
        return
    if images_dir.exists():
        shutil.rmtree(images_dir)
    images_dir.mkdir(parents=True, exist_ok=True)


def clean_all_post_image_refs(posts: list[PostInfo], dry_run: bool) -> None:
    for post in posts:
        original = read_text(post.path)
        updated = remove_existing_image_refs(original)
        if not dry_run and updated != original:
            post.path.write_text(updated, encoding="utf-8")


def print_report(results: list[ProcessResult], dry_run: bool) -> None:
    heading = "DRY RUN report" if dry_run else "Report"
    print(f"\n{heading}")
    print("=" * len(heading))

    matched = [result for result in results if result.post and result.status != "unmatched"]
    unmatched = [result for result in results if result.status == "unmatched"]
    with_missing = [result for result in results if result.missing_anchors]

    print("\nMatched PDFs:")
    if not matched:
        print("  - None")
    for result in matched:
        print(
            "  - "
            f"{result.pdf.name} -> {result.post.name} "
            f"({result.reason}, score={result.score:.2f}, "
            f"pdf_images={result.images_found}, saved_after_cover={result.images_saved}, "
            f"inserted={result.images_inserted}, fallback={result.fallback_inserted}, "
            f"status={result.status})"
        )

    print("\nNeed manual PDF-to-post handling:")
    if not unmatched:
        print("  - None")
    for result in unmatched:
        print(f"  - {result.pdf.name} ({result.reason}, best_score={result.score:.2f})")

    print("\nNeed manual anchor handling:")
    if not with_missing:
        print("  - None")
    for result in with_missing:
        preview = "; ".join(anchor[:24] for anchor in result.missing_anchors[:5])
        extra = "" if len(result.missing_anchors) <= 5 else f"; +{len(result.missing_anchors) - 5} more"
        print(f"  - {result.pdf.name}: {len(result.missing_anchors)} missing anchors ({preview}{extra})")


def main() -> int:
    args = parse_args()
    pdf_dir = args.pdf_dir
    posts_dir = args.posts_dir
    images_dir = args.images_dir

    if not pdf_dir.exists():
        print(f"PDF directory not found: {pdf_dir}", file=sys.stderr)
        return 1
    if not posts_dir.exists():
        print(f"Posts directory not found: {posts_dir}", file=sys.stderr)
        return 1

    pdfs = [build_pdf_info(path) for path in sorted(pdf_dir.glob("*.pdf"))]
    posts = [build_post_info(path) for path in sorted(posts_dir.glob("*.md"))]

    clear_images_dir(images_dir, args.dry_run)
    clean_all_post_image_refs(posts, args.dry_run)

    results = [process_pdf(pdf=pdf, posts=posts, images_dir=images_dir, dry_run=args.dry_run) for pdf in pdfs]
    print_report(results, args.dry_run)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
