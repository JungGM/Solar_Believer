"""Atomically publish a Vite build to the service database."""

import argparse
import hashlib
import mimetypes
from pathlib import Path

from sqlalchemy import select

from .db import session_factory
from .web_assets import WebAsset


CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json",
}


def publish(directory: Path, factory=None):
    directory = directory.resolve()
    if not (directory / "index.html").is_file():
        raise ValueError("Build directory must contain index.html")
    assets = []
    for file in sorted(directory.rglob("*")):
        if not file.is_file():
            continue
        if file.is_symlink() or not file.resolve().is_relative_to(directory):
            raise ValueError("Build must not contain external links")
        path = file.relative_to(directory).as_posix()
        if len(path) > 512 or path.split("/", 1)[0] in {"api", "health", "docs", "redoc"}:
            raise ValueError(f"Invalid web asset path: {path}")
        content = file.read_bytes()
        assets.append({
            "path": path,
            "content": content,
            "content_type": CONTENT_TYPES.get(file.suffix.lower())
            or mimetypes.guess_type(path)[0] or "application/octet-stream",
            "sha256": hashlib.sha256(content).hexdigest(),
        })
    # Keep old hashed chunks for browsers that loaded the previous index.html.
    # All updates, including the entry point, become visible in one commit.
    with (factory or session_factory())() as session, session.begin():
        if session.bind.dialect.name == "postgresql":
            from sqlalchemy import text
            session.execute(text("SELECT pg_advisory_xact_lock(736052917)"))
        for values in assets:
            existing = session.scalar(select(WebAsset).where(WebAsset.path == values["path"]))
            if existing is None:
                session.add(WebAsset(**values))
            else:
                for key, value in values.items():
                    setattr(existing, key, value)
    return {"files": len(assets), "bytes": sum(len(asset["content"]) for asset in assets)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", type=Path, default=Path("/app/web-dist"))
    print(publish(parser.parse_args().directory))
