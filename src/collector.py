from __future__ import annotations

import argparse
import hashlib
import html
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

import requests
from PIL import Image

API = "https://commons.wikimedia.org/w/api.php"
ALLOWED_MIME = {"image/jpeg": ".jpg", "image/png": ".png"}


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def meta_value(meta: dict[str, Any], key: str) -> str:
    item = meta.get(key) or {}
    if isinstance(item, dict):
        return strip_html(str(item.get("value", "")))
    return strip_html(str(item))


def safe_name(value: str, fallback: str = "file") -> str:
    value = value.replace("\u0000", "")
    value = re.sub(r"[<>:\"/\\|?*]+", "_", value)
    value = re.sub(r"\s+", " ", value).strip().strip(".")
    return (value or fallback)[:180]


def normalized_key(value: str) -> str:
    value = strip_html(value).lower()
    value = re.sub(r"\b(copy|scan|cropped?|detail|version|large|small|museum|private collection)\b", " ", value)
    value = re.sub(r"[^\w\d]+", "", value, flags=re.UNICODE)
    return value


def license_ok(meta: dict[str, Any]) -> tuple[bool, str, str]:
    short = meta_value(meta, "LicenseShortName")
    usage = meta_value(meta, "UsageTerms")
    url = meta_value(meta, "LicenseUrl")
    copyrighted = meta_value(meta, "Copyrighted").lower()
    text = f"{short} {usage}".lower()

    if copyrighted in {"false", "no", "0"}:
        return True, short or usage or "Public domain", url
    if "public domain" in text or "publicdomain" in text or "cc0" in text:
        return True, short or usage, url
    if "cc by" in text or "creative commons attribution" in text:
        if "noncommercial" in text or "no derivatives" in text or "-nc" in text or "-nd" in text:
            return False, short or usage, url
        return True, short or usage, url
    return False, short or usage, url


def artist_matches(meta: dict[str, Any], aliases: Iterable[str]) -> bool:
    hay = " ".join([
        meta_value(meta, "Artist"),
        meta_value(meta, "Credit"),
        meta_value(meta, "ImageDescription"),
    ]).lower()
    return any(alias.lower() in hay for alias in aliases if alias)


def image_signature_ok(data: bytes, mime: str) -> tuple[bool, int, int, str]:
    try:
        with Image.open(io.BytesIO(data)) as im:
            fmt = (im.format or "").upper()
            width, height = im.size
            im.verify()
        expected = {"image/jpeg": {"JPEG"}, "image/png": {"PNG"}}[mime]
        return fmt in expected and width > 0 and height > 0, width, height, fmt
    except Exception:
        return False, 0, 0, ""


def average_hash(data: bytes, size: int = 16) -> int:
    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("L").resize((size, size))
        pixels = list(im.get_flattened_data()) if hasattr(im, "get_flattened_data") else list(im.getdata())
    avg = sum(pixels) / len(pixels)
    bits = 0
    for px in pixels:
        bits = (bits << 1) | int(px >= avg)
    return bits


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


@dataclass
class DownloadedWork:
    title: str
    source_page: str
    license: str
    license_url: str
    original_filename: str
    downloaded_filename: str
    folder: str
    artist_credit: str
    width: int
    height: int
    sha256: str


class CommonsClient:
    def __init__(self, user_agent: str, timeout: int = 45, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self.timeout = timeout

    def category_files(self, category: str, limit: int = 250) -> list[str]:
        titles: list[str] = []
        cont: str | None = None
        while len(titles) < limit:
            params = {
                "action": "query",
                "format": "json",
                "list": "categorymembers",
                "cmtitle": f"Category:{category}",
                "cmnamespace": 6,
                "cmtype": "file",
                "cmlimit": min(500, limit - len(titles)),
            }
            if cont:
                params["cmcontinue"] = cont
            r = self.session.get(API, params=params, timeout=self.timeout)
            r.raise_for_status()
            payload = r.json()
            titles.extend(x["title"] for x in payload.get("query", {}).get("categorymembers", []))
            cont = payload.get("continue", {}).get("cmcontinue")
            if not cont:
                break
        return titles[:limit]

    def image_info(self, titles: list[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for start in range(0, len(titles), 20):
            batch = titles[start:start + 20]
            params = {
                "action": "query",
                "format": "json",
                "prop": "imageinfo",
                "titles": "|".join(batch),
                "iiprop": "url|mime|size|extmetadata",
                "iiextmetadatalanguage": "en",
            }
            r = self.session.get(API, params=params, timeout=self.timeout)
            r.raise_for_status()
            pages = payload_pages(r.json())
            for page in pages:
                ii = (page.get("imageinfo") or [None])[0]
                if ii:
                    out.append({"title": page.get("title", ""), **ii})
            time.sleep(0.08)
        return out

    def download(self, url: str, max_bytes: int) -> tuple[bytes, str]:
        with self.session.get(url, timeout=self.timeout, stream=True, allow_redirects=True) as r:
            r.raise_for_status()
            mime = (r.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            declared = int(r.headers.get("Content-Length") or 0)
            if declared and declared > max_bytes:
                raise ValueError(f"source too large: {declared} bytes")
            data = bytearray()
            for chunk in r.iter_content(1024 * 256):
                if not chunk:
                    continue
                data.extend(chunk)
                if len(data) > max_bytes:
                    raise ValueError(f"source exceeded max size: {max_bytes} bytes")
            return bytes(data), mime


def payload_pages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    pages = payload.get("query", {}).get("pages", {})
    if isinstance(pages, dict):
        return list(pages.values())
    return list(pages or [])


def source_page_for(title: str) -> str:
    return "https://commons.wikimedia.org/wiki/" + quote(title.replace(" ", "_"), safe=":/_()-.,'~")


def extract_artwork_title(file_title: str, meta: dict[str, Any]) -> str:
    obj = meta_value(meta, "ObjectName")
    if obj and len(obj) <= 220:
        return obj
    stem = file_title.removeprefix("File:")
    stem = re.sub(r"\.(jpe?g|png)$", "", stem, flags=re.I)
    return stem


def ensure_unique_filename(folder: Path, original: str, index: int, extension: str) -> str:
    stem = Path(original).stem
    name = f"{index:02d}_{safe_name(stem)}{extension}"
    candidate = folder / name
    n = 2
    while candidate.exists():
        name = f"{index:02d}_{safe_name(stem)}_{n}{extension}"
        candidate = folder / name
        n += 1
    return name


def build_sources(painter: dict[str, Any], works: list[DownloadedWork]) -> str:
    lines = [
        f"Painter: {painter['name_ka']} / {painter['name']}",
        f"Active/life period: {painter.get('years', '')}",
        f"Main styles: {', '.join(painter.get('styles', []))}",
        f"Downloaded images: {len(works)}",
        "Source: Wikimedia Commons",
        "",
        "Each entry below records the source page and machine-readable license metadata used at download time.",
        "",
    ]
    for i, work in enumerate(works, 1):
        lines += [
            f"[{i}] {work.title}",
            f"Source page: {work.source_page}",
            f"License/reuse status: {work.license}",
            f"License URL: {work.license_url or 'Not supplied by Commons metadata'}",
            f"Artist/credit metadata: {work.artist_credit or 'Not supplied'}",
            f"Original image filename: {work.original_filename}",
            f"Downloaded filename: {work.folder}/{work.downloaded_filename}",
            f"Dimensions: {work.width}x{work.height}",
            f"SHA-256: {work.sha256}",
            "",
        ]
    return "\n".join(lines)


def validate_zip(zip_path: Path, min_images: int) -> None:
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        images = [n for n in names if n.lower().endswith((".jpg", ".jpeg", ".png"))]
        if len(images) < min_images:
            raise RuntimeError(f"ZIP validation failed: only {len(images)} images")
        if "SOURCES.txt" not in names:
            raise RuntimeError("ZIP validation failed: SOURCES.txt missing")
        for name in images:
            info = zf.getinfo(name)
            if info.file_size <= 0:
                raise RuntimeError(f"ZIP validation failed: empty image {name}")


def collect_painter(client: CommonsClient, painter: dict[str, Any], settings: dict[str, Any], output_dir: Path) -> tuple[Path, list[DownloadedWork]] | None:
    min_images = int(settings["min_images"])
    max_images = int(settings["max_images"])
    max_source_bytes = int(settings["max_source_bytes"])

    with tempfile.TemporaryDirectory(prefix="painter-") as td:
        root = Path(td) / safe_name(painter["name"])
        root.mkdir(parents=True, exist_ok=True)
        candidates: list[tuple[str, str]] = []
        seen_titles: set[str] = set()

        for cat in painter["categories"]:
            folder = cat.get("folder") or "Works"
            for title in client.category_files(cat["name"], limit=300):
                if title in seen_titles:
                    continue
                seen_titles.add(title)
                candidates.append((title, folder))

        if not candidates:
            return None

        folder_by_title = dict(candidates)
        infos = client.image_info([t for t, _ in candidates])
        works: list[DownloadedWork] = []
        sha_seen: set[str] = set()
        artwork_seen: set[str] = set()
        perceptual: list[tuple[int, int, int]] = []

        for info in infos:
            if len(works) >= max_images:
                break
            title = info.get("title", "")
            mime = (info.get("mime") or "").lower()
            if mime not in ALLOWED_MIME:
                continue
            meta = info.get("extmetadata") or {}
            ok, license_name, license_url = license_ok(meta)
            if not ok:
                continue
            if painter.get("require_artist_match"):
                aliases = painter.get("artist_aliases") or [painter["name"], painter.get("name_ka", "")]
                if not artist_matches(meta, aliases):
                    continue

            artwork_title = extract_artwork_title(title, meta)
            art_key = normalized_key(artwork_title)
            if art_key and art_key in artwork_seen:
                continue

            url = info.get("url")
            if not url:
                continue
            try:
                data, response_mime = client.download(url, max_source_bytes)
            except Exception as exc:
                print(f"SKIP download {title}: {exc}", file=sys.stderr)
                continue
            if response_mime not in ALLOWED_MIME:
                print(f"SKIP mime {title}: {response_mime}", file=sys.stderr)
                continue
            valid, width, height, _ = image_signature_ok(data, response_mime)
            if not valid:
                print(f"SKIP invalid image {title}", file=sys.stderr)
                continue

            digest = hashlib.sha256(data).hexdigest()
            if digest in sha_seen:
                continue
            try:
                ph = average_hash(data)
            except Exception:
                ph = None
            if ph is not None:
                duplicate_visual = False
                for old_hash, old_w, old_h in perceptual:
                    ratio_new = width / max(height, 1)
                    ratio_old = old_w / max(old_h, 1)
                    if abs(ratio_new - ratio_old) <= 0.03 and hamming(ph, old_hash) <= 2:
                        duplicate_visual = True
                        break
                if duplicate_visual:
                    continue

            folder_name = safe_name(folder_by_title.get(title, "Works"), "Works")
            folder = root / folder_name
            folder.mkdir(parents=True, exist_ok=True)
            original = title.removeprefix("File:")
            extension = ALLOWED_MIME[response_mime]
            downloaded = ensure_unique_filename(folder, original, len(works) + 1, extension)
            (folder / downloaded).write_bytes(data)

            work = DownloadedWork(
                title=artwork_title,
                source_page=source_page_for(title),
                license=license_name or "Rights-clear according to Commons metadata",
                license_url=license_url,
                original_filename=original,
                downloaded_filename=downloaded,
                folder=folder_name,
                artist_credit=meta_value(meta, "Artist") or meta_value(meta, "Credit"),
                width=width,
                height=height,
                sha256=digest,
            )
            works.append(work)
            sha_seen.add(digest)
            if art_key:
                artwork_seen.add(art_key)
            if ph is not None:
                perceptual.append((ph, width, height))

        if len(works) < min_images:
            print(f"Candidate {painter['name']} rejected: {len(works)} < {min_images} downloadable images", file=sys.stderr)
            return None

        (root / "SOURCES.txt").write_text(build_sources(painter, works), encoding="utf-8")
        manifest = {
            "painter": painter,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "images": [asdict(w) for w in works],
        }
        (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        archive_name = f"{stamp}_{safe_name(painter['name']).replace(' ', '_')}.zip"
        output_dir.mkdir(parents=True, exist_ok=True)
        zip_path = output_dir / archive_name
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(root).as_posix())

        validate_zip(zip_path, min_images)
        return zip_path, works


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_history(path: Path, history: dict[str, Any]) -> None:
    path.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(config_path: Path, history_path: Path, output_dir: Path) -> int:
    config = load_json(config_path)
    settings = config["settings"]
    history = load_json(history_path)
    successful = set(history.get("successful_painters", []))
    client = CommonsClient(settings["user_agent"], int(settings["request_timeout_seconds"]))

    candidates = [p for p in config["painters"] if p["name"] not in successful]
    if not candidates:
        print("No unused painters remain. Add more painters to config/painters.json.", file=sys.stderr)
        return 2

    for painter in candidates:
        print(f"Trying {painter['name']}...", flush=True)
        try:
            result = collect_painter(client, painter, settings, output_dir)
        except Exception as exc:
            print(f"Candidate {painter['name']} failed: {exc}", file=sys.stderr)
            continue
        if result is None:
            continue

        zip_path, works = result
        successful.add(painter["name"])
        history["successful_painters"] = sorted(successful)
        history.setdefault("runs", []).append({
            "painter": painter["name"],
            "painter_ka": painter.get("name_ka"),
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "image_count": len(works),
            "archive": zip_path.name,
        })
        save_history(history_path, history)
        summary = {
            "painter": painter["name"],
            "painter_ka": painter.get("name_ka"),
            "years": painter.get("years"),
            "styles": painter.get("styles", []),
            "image_count": len(works),
            "folders": sorted({w.folder for w in works}),
            "zip": str(zip_path),
        }
        print("RESULT_JSON=" + json.dumps(summary, ensure_ascii=False))
        return 0

    print("No candidate produced enough downloadable rights-clear images in this run.", file=sys.stderr)
    return 3


def main() -> int:
    parser = argparse.ArgumentParser(description="Download rights-clear Georgian painter works from Wikimedia Commons and build a ZIP.")
    parser.add_argument("--config", default="config/painters.json")
    parser.add_argument("--history", default="state/history.json")
    parser.add_argument("--output", default="output")
    args = parser.parse_args()
    return run(Path(args.config), Path(args.history), Path(args.output))


if __name__ == "__main__":
    raise SystemExit(main())
