import os
from pathlib import Path

import psycopg


REPO = "doggirltube/frames-doggirl-xx"
SITE_BASE = f"https://{REPO.split('/')[0]}.github.io/{REPO.split('/')[1]}"


def main():
    database_url = os.environ["DATABASE_URL"]
    with psycopg.connect(database_url, autocommit=True) as conn:
        rows = conn.execute(
            """SELECT id, frames_path FROM blog_posts
               WHERE frames_status = 'generated'
                 AND frames_path IS NOT NULL
                 AND frames_path <> ''"""
        ).fetchall()

        for row_id, frames_path in rows:
            folder = Path(str(frames_path).rstrip("/"))
            if not (folder / "index.html").is_file():
                print(
                    f"Skip id={row_id}: gallery folder not present at "
                    f"{frames_path!r}"
                )
                continue

            conn.execute(
                """UPDATE blog_posts
                   SET frames_status = 'done', frames_error = NULL
                   WHERE id = %s""",
                (row_id,),
            )
            public_url = f"{SITE_BASE}/{str(frames_path).strip('/')}/"
            print(
                f"Gallery published for id={row_id}; "
                f"path={frames_path}; url={public_url}"
            )


if __name__ == "__main__":
    main()
