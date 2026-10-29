#!/usr/bin/env python3
"""Build a compact catalog for the unified GitHub Pages frame browser."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


class GalleryPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.heading_parts: list[str] = []
        self.images: list[str] = []
        self.in_title = False
        self.in_h1 = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = dict(attrs)
        if tag.lower() == "title":
            self.in_title = True
        elif tag.lower() == "h1":
            self.in_h1 = True
        elif tag.lower() == "img":
            src = attrs_map.get("src")
            if src:
                self.images.append(src.strip())

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False
        elif tag.lower() == "h1":
            self.in_h1 = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)
        if self.in_h1:
            self.heading_parts.append(data)


def natural_image_sort(filename: str) -> tuple[str, int, str]:
    match = re.search(r"(\d+)(?=\.[^.]+$)", filename)
    number = int(match.group(1)) if match else -1
    base = re.sub(r"_\d+(?=\.[^.]+$)", "", filename).lower()
    return (base, number, filename.lower())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames-root", default="frames", type=Path)
    parser.add_argument("--output", default="_site/gallery-catalog.json", type=Path)
    args = parser.parse_args()

    frames_root = args.frames_root.resolve()
    if not frames_root.is_dir():
        raise SystemExit(f"Frames directory not found: {frames_root}")

    galleries = []
    for page in frames_root.rglob("index.html"):
        folder = page.parent
        try:
            source = page.read_text(encoding="utf-8", errors="replace")
            page_parser = GalleryPageParser()
            page_parser.feed(source)
        except OSError as exc:
            print(f"Skipping unreadable gallery {page}: {exc}")
            continue

        filenames = []
        for src in page_parser.images:
            # Generated pages use local image filenames. Ignore external or
            # nested paths so the catalog cannot point outside this gallery.
            clean_src = src.split("?", 1)[0].split("#", 1)[0]
            image_name = Path(clean_src).name
            if clean_src != image_name or image_name in {"", ".", ".."}:
                continue
            if Path(image_name).suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            if (folder / image_name).is_file():
                filenames.append(image_name)

        filenames = sorted(set(filenames), key=natural_image_sort)
        if not filenames:
            continue

        heading = " ".join(" ".join(page_parser.heading_parts).split())
        title = heading or " ".join(" ".join(page_parser.title_parts).split())
        title = re.sub(r"\s*[—-]\s*video frames\s*$", "", title, flags=re.I).strip()
        if not title:
            title = folder.name

        folder_rel = folder.relative_to(frames_root).as_posix()
        galleries.append({
            "title": title,
            "folder": f"frames/{folder_rel}",
            "id": folder.name,
            "images": filenames,
        })

    galleries.sort(key=lambda item: (item["title"].casefold(), item["folder"].casefold()))
    total_images = sum(len(gallery["images"]) for gallery in galleries)
    catalog = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "galleryCount": len(galleries),
        "imageCount": total_images,
        "galleries": galleries,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp_output = args.output.with_suffix(args.output.suffix + ".tmp")
    with temp_output.open("w", encoding="utf-8") as handle:
        json.dump(catalog, handle, ensure_ascii=False, separators=(",", ":"))
    temp_output.replace(args.output)

    print(
        f"Catalog built: {len(galleries):,} galleries, "
        f"{total_images:,} images -> {args.output}"
    )


if __name__ == "__main__":
    main()
