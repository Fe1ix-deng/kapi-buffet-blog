#!/usr/bin/env python3
"""Extract PDF images and insert them into matching blog posts.

Requires:
    pip install pymupdf

Default behavior:
    - Reads PDFs from pdf_files/
    - Writes images to public/images/{pdf-stem}/01.jpg, 02.jpg, ...
    - Matches posts in src/content/posts/ by date first, then title keywords
    - Inserts image references every few paragraphs
"""

from __future__ import annotations

import argparse
import hashlib
import io
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import fitz


DATE_RE = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})")
FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n?", re.DOTALL)
IMAGE_REF_RE = re.compile(r"!\[[^\]]*\]\((?P<src>/images/[^)]+)\)")
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
    searchable_text: str


@dataclass
class ProcessResult:
    pdf: Path
    post: Path | None
    status: str
    images_extracted: int = 0
    images_inserted: int = 0
    score: float = 0.0
    reason: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract images from PDFs and insert Markdown image refs into matching posts."
    )
    parser.add_argument("--pdf-dir", type=Path, default=Path("pdf_files"))
    parser.add_argument("--posts-dir", type=Path, default=Path("src/content/posts"))
    parser.add_argument("--images-dir", type=Path, default=Path("public/images"))
    parser.add_argument(
        "--interval",
        type=int,
        default=4,
        help="Insert one image after every N Markdown paragraphs. Default: 4.",
    )
    parser.add_argument(
        "--overwrite-images",
        action="store_true",
        help="Rewrite existing extracted image files.",
    )
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


def tokenize(text: str) -> set[str]:
    tokens: set[str] = set()
    compact = re.sub(r"\s+", "", text.lower())

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
    title_part = normalize_stem(stem)
    return PdfInfo(
        path=pdf_path,
        stem=stem,
        date=extract_date(stem),
        keywords=tokenize(title_part),
    )


def build_post_info(post_path: Path) -> PostInfo:
    markdown = read_text(post_path)
    title = parse_title(markdown)
    date = extract_date(post_path.name) or extract_date(markdown)
    searchable = f"{post_path.stem} {title} {markdown[:1200]}"
    return PostInfo(
        path=post_path,
        date=date,
        title=title,
        keywords=tokenize(searchable),
        searchable_text=searchable,
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


def image_hash(image_bytes: bytes) -> str:
    return hashlib.sha1(image_bytes).hexdigest()


def pixmap_to_jpeg_bytes(doc: fitz.Document, xref: int) -> bytes:
    pix = fitz.Pixmap(doc, xref)
    if pix.alpha or pix.colorspace is None or pix.n not in (1, 3):
        pix = fitz.Pixmap(fitz.csRGB, pix)
    return pix.tobytes("jpeg")


def extract_images(pdf_path: Path, output_dir: Path, overwrite: bool, dry_run: bool) -> list[Path]:
    doc = fitz.open(pdf_path)
    seen: set[str] = set()
    images: list[bytes] = []

    for page in doc:
        for image in page.get_images(full=True):
            xref = image[0]
            jpeg_bytes = pixmap_to_jpeg_bytes(doc, xref)
            digest = image_hash(jpeg_bytes)
            if digest in seen:
                continue
            seen.add(digest)
            images.append(jpeg_bytes)

    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    paths: list[Path] = []
    for index, jpeg_bytes in enumerate(images, start=1):
        image_path = output_dir / f"{index:02d}.jpg"
        paths.append(image_path)
        if dry_run:
            continue
        if image_path.exists() and not overwrite:
            continue
        image_path.write_bytes(jpeg_bytes)

    return paths


def split_frontmatter(markdown: str) -> tuple[str, str]:
    match = FRONTMATTER_RE.match(markdown)
    if not match:
        return "", markdown
    return match.group(0), markdown[match.end() :]


def is_insertable_paragraph(paragraph: str) -> bool:
    stripped = paragraph.strip()
    if not stripped:
        return False
    if stripped.startswith("!") or stripped.startswith("|"):
        return False
    if stripped.startswith("```") or stripped.startswith("~~~"):
        return False
    if re.fullmatch(r"https?://\S+", stripped):
        return False
    return True


def split_paragraphs(body: str) -> list[str]:
    return re.split(r"\n{2,}", body.strip("\n"))


def insert_image_refs(markdown: str, image_urls: list[str], interval: int) -> tuple[str, int]:
    if not image_urls:
        return markdown, 0

    existing = set(IMAGE_REF_RE.findall(markdown))
    pending_urls = [url for url in image_urls if url not in existing]
    if not pending_urls:
        return markdown, 0

    frontmatter, body = split_frontmatter(markdown)
    paragraphs = split_paragraphs(body)
    if not paragraphs:
        return markdown, 0

    rebuilt: list[str] = []
    inserted = 0
    insertable_count = 0
    in_code_block = False

    for paragraph in paragraphs:
        stripped = paragraph.strip()
        rebuilt.append(paragraph)

        if stripped.startswith(("```", "~~~")):
            in_code_block = not in_code_block

        if in_code_block or not is_insertable_paragraph(paragraph):
            continue

        insertable_count += 1
        if insertable_count % interval == 0 and inserted < len(pending_urls):
            url = pending_urls[inserted]
            rebuilt.append(f"![{Path(url).stem}]({url})")
            inserted += 1

    while inserted < len(pending_urls):
        url = pending_urls[inserted]
        rebuilt.append(f"![{Path(url).stem}]({url})")
        inserted += 1

    new_body = "\n\n".join(part.strip("\n") for part in rebuilt).rstrip() + "\n"
    return frontmatter + new_body, inserted


def public_image_url(images_dir: Path, image_path: Path) -> str:
    public_root = Path("public")
    relative = image_path
    try:
        relative = image_path.relative_to(public_root)
    except ValueError:
        try:
            relative = image_path.relative_to(images_dir.parent)
        except ValueError:
            relative = image_path
    return "/" + relative.as_posix()


def process_pdf(
    pdf: PdfInfo,
    posts: list[PostInfo],
    images_dir: Path,
    interval: int,
    overwrite_images: bool,
    dry_run: bool,
) -> ProcessResult:
    post, score, reason = find_matching_post(pdf, posts)
    result = ProcessResult(pdf=pdf.path, post=post.path if post else None, status="", score=score, reason=reason)

    if post is None:
        result.status = "unmatched"
        return result

    output_dir = images_dir / pdf.stem
    image_paths = extract_images(pdf.path, output_dir, overwrite_images, dry_run)
    result.images_extracted = len(image_paths)

    if not image_paths:
        result.status = "matched_no_images"
        return result

    image_urls = [public_image_url(images_dir, path) for path in image_paths]
    original = read_text(post.path)
    updated, inserted = insert_image_refs(original, image_urls, interval)
    result.images_inserted = inserted

    if inserted == 0:
        result.status = "matched_already_inserted"
        return result

    result.status = "matched"
    if not dry_run:
        post.path.write_text(updated, encoding="utf-8")

    return result


def print_report(results: list[ProcessResult], dry_run: bool) -> None:
    heading = "DRY RUN report" if dry_run else "Report"
    print(f"\n{heading}")
    print("=" * len(heading))

    matched = [result for result in results if result.post and result.status != "unmatched"]
    unmatched = [result for result in results if result.status == "unmatched"]

    print("\nMatched PDFs:")
    if not matched:
        print("  - None")
    for result in matched:
        print(
            "  - "
            f"{result.pdf.name} -> {result.post.name} "
            f"({result.reason}, score={result.score:.2f}, "
            f"images={result.images_extracted}, inserted={result.images_inserted}, "
            f"status={result.status})"
        )

    print("\nNeed manual handling:")
    if not unmatched:
        print("  - None")
    for result in unmatched:
        print(
            "  - "
            f"{result.pdf.name} "
            f"({result.reason}, best_score={result.score:.2f})"
        )


def main() -> int:
    args = parse_args()
    if args.interval < 1:
        print("--interval must be at least 1", file=sys.stderr)
        return 2

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

    results = [
        process_pdf(
            pdf=pdf,
            posts=posts,
            images_dir=images_dir,
            interval=args.interval,
            overwrite_images=args.overwrite_images,
            dry_run=args.dry_run,
        )
        for pdf in pdfs
    ]

    print_report(results, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
