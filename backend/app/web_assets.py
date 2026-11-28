"""Serve published frontend bytes from PostgreSQL, never from a local directory."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import LargeBinary, String
from sqlalchemy.orm import Mapped, Session, mapped_column

from .db import Base, get_session


class WebAsset(Base):
    __tablename__ = "web_assets"

    path: Mapped[str] = mapped_column(String(512), primary_key=True)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    content_type: Mapped[str] = mapped_column(String(100))
    sha256: Mapped[str] = mapped_column(String(64))


router = APIRouter()


@router.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
def frontend(path: str, request: Request, session: Annotated[Session, Depends(get_session)]):
    # Unknown API/health routes must not silently return the SPA shell.
    if path.split("/", 1)[0] in {"api", "health", "docs", "redoc", "openapi.json"}:
        raise HTTPException(404, "Not found")
    if "\\" in path or any(part in {".", ".."} for part in path.split("/")):
        raise HTTPException(404, "Not found")
    asset = session.get(WebAsset, path or "index.html")
    if asset is None and not path.startswith("assets/") and "." not in path.rsplit("/", 1)[-1]:
        asset = session.get(WebAsset, "index.html")
    if asset is None:
        raise HTTPException(404, "Web asset not published")
    etag = f'"{asset.sha256}"'
    headers = {"ETag": etag, "Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"}
    candidates = request.headers.get("if-none-match", "").split(",")
    if any(value.strip().removeprefix("W/") in {etag, "*"} for value in candidates):
        return Response(status_code=304, headers=headers)
    headers["Content-Type"] = asset.content_type
    headers["Content-Length"] = str(len(asset.content))
    return Response(content=asset.content if request.method != "HEAD" else b"", headers=headers)
