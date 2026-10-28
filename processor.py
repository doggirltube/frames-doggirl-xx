import hashlib
import hmac
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse
from html import escape

import psycopg
import requests

REPO = "doggirltube/frames-doggirl-xx"
SITE_BASE = f"https://{REPO.split('/')[0]}.github.io/{REPO.split('/')[1]}"
FRAMES_ROOT = Path("frames")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "30"))
FRAME_COUNT = int(os.getenv("FRAME_COUNT", "30"))
MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "5"))
SECRET = os.getenv("VIDEO_SIGNING_SECRET", "").encode()


def is_full_url(value):
    return isinstance(value, str) and value.startswith(("http://", "https://"))



import time
import hmac
import hashlib


def is_full_url(url):
    """Check whether a string is a complete HTTP(S) URL."""
    return isinstance(url, str) and url.startswith(("http://", "https://"))


def sign_video_url(filename, expires_in=1800):
    if not filename:
        return None

    # Keep existing complete URLs unchanged.
    if is_full_url(filename):
        return filename

    if filename.startswith("b2/"):
        # Sign B2 video URLs.
        expires = int(time.time()) + expires_in
        data = f"{filename}:{expires}".encode()
        sig = hmac.new(SECRET, data, hashlib.sha256).hexdigest()

        return (
            f"https://media.dirtytube.site/video/"
            f"{filename}?e={expires}&sig={sig}"
        )

    # Non-B2 files use media2 without signing.
    clean_name = filename.removeprefix("b2/")

    if not clean_name.endswith(".mp4"):
        clean_name += ".mp4"

    return f"https://media2.dirtytube.site/{clean_name}"

def safe_stem(source, row_id):
    name = urlparse(source).path.rsplit("/", 1)[-1] if is_full_url(source) else source
    name = name.removeprefix("b2/").rsplit("/", 1)[-1]
    name = re.sub(r"\.mp4$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-_")
    return name[:100] or f"video-{row_id}"


def render_page(folder, title, stem, count):
    images = "\n".join(
        f'<figure><a href="{stem}_{i}.jpg" target="_blank"><img loading="lazy" src="{stem}_{i}.jpg" alt="Frame {i}"></a><figcaption>Frame {i}</figcaption></figure>'
        for i in range(1, count + 1)
    )
    safe_title = escape(str(title or stem))
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{safe_title} — video frames</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#111;color:#eee;font:16px/1.5 system-ui,sans-serif}}
header{{padding:1.25rem 2vw;background:#1b1b1b;position:sticky;top:0}}h1{{font-size:1.2rem;margin:0}}p{{color:#aaa;margin:.25rem 0 0}}
main{{padding:2vw;display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:14px}}
figure{{margin:0;background:#202020;border-radius:8px;overflow:hidden}}img{{display:block;width:100%;aspect-ratio:16/9;object-fit:contain;background:#000}}figcaption{{padding:7px 10px;color:#bbb;font-size:.85rem}}
</style></head><body><header><h1>{safe_title}</h1><p>{count} sampled frames</p></header><main>{images}</main></body></html>"""
    (folder / "index.html").write_text(html, encoding="utf-8")


def main():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("Set DATABASE_URL in GitHub Actions secrets")
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise RuntimeError("ffmpeg and ffprobe must be installed")

    FRAMES_ROOT.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(database_url, autocommit=True) as conn:
        rows = conn.execute(
            """SELECT id, title, video_src, frames_attempts
               FROM blog_posts
               WHERE video_src IS NOT NULL
                 AND frames_status IN ('pending', 'error', 'processing')
                 AND frames_attempts < %s
               ORDER BY frames_attempts ASC, id ASC
               LIMIT %s""",
            (MAX_ATTEMPTS, BATCH_SIZE),
        ).fetchall()

        for row_id, title, video_src, attempts in rows:
            folder = None
            conn.execute(
                """UPDATE blog_posts
                   SET frames_status = 'processing',
                       frames_path = NULL,
                       frames_attempts = frames_attempts + 1,
                       frames_error = NULL
                   WHERE id = %s""",
                (row_id,),
            )
            try:
                video_url = sign_video_url(str(video_src))
                stem = safe_stem(str(video_src), row_id)
                folder = FRAMES_ROOT / f"{row_id}-{stem}"
                if folder.exists():
                    shutil.rmtree(folder)
                folder.mkdir(parents=True, exist_ok=True)

                with tempfile.TemporaryDirectory() as temp_dir:
                    video_path = Path(temp_dir) / "source.mp4"
                    with requests.get(video_url, stream=True, timeout=(30, 180)) as response:
                        response.raise_for_status()
                        with video_path.open("wb") as output:
                            for chunk in response.iter_content(chunk_size=1024 * 1024):
                                if chunk:
                                    output.write(chunk)

                    probe = subprocess.run(
                        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                         "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
                        check=True, capture_output=True, text=True,
                    )
                    duration = float(probe.stdout.strip())
                    if duration <= 0:
                        raise ValueError("Video duration is not positive")

                    count = max(1, FRAME_COUNT)
                    for i in range(1, count + 1):
                        timestamp = duration * i / (count + 1)
                        output_path = folder / f"{stem}_{i}.jpg"
                        subprocess.run(
                            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(timestamp),
                             "-i", str(video_path), "-frames:v", "1", "-q:v", "3", "-y", str(output_path)],
                            check=True, capture_output=True,
                        )
                        if not output_path.exists() or output_path.stat().st_size == 0:
                            raise RuntimeError(f"Frame {i} was not generated")

                render_page(folder, title, stem, count)
                conn.execute(
                    """UPDATE blog_posts
                       SET frames_status = 'generated',
                           frames_path = %s,
                           frames_error = NULL,
                           frames_generated_at = NOW()
                       WHERE id = %s""",
                    (folder.as_posix().rstrip("/") + "/", row_id),
                )
                print(f"Generated frames for id={row_id}; path={folder.as_posix()}/")
            except Exception as exc:
                if folder and folder.exists():
                    shutil.rmtree(folder, ignore_errors=True)
                error = str(exc)[:1500]
                conn.execute(
                    """UPDATE blog_posts
                       SET frames_status = 'error', frames_error = %s
                       WHERE id = %s""",
                    (error, row_id),
                )
                print(f"Failed database id={row_id}: {error}")

        remaining = conn.execute(
            """SELECT EXISTS (
                 SELECT 1 FROM blog_posts
                 WHERE video_src IS NOT NULL
                   AND (
                     frames_status IN ('pending', 'processing')
                     OR (frames_status = 'error' AND frames_attempts < %s)
                   )
               )""",
            (MAX_ATTEMPTS,),
        ).fetchone()[0]
        output_path = os.getenv("GITHUB_OUTPUT")
        if output_path:
            with open(output_path, "a", encoding="utf-8") as output:
                output.write(f"has_more={'true' if remaining else 'false'}\n")


if __name__ == "__main__":
    main()
