from hashlib import sha256

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.db import get_session
from backend.app.main import app
from backend.app.publish_web import publish
from backend.app.web_assets import WebAsset


def test_database_frontend_preserves_bytes_routes_and_http_cache(factory, tmp_path):
    index = b'<!doctype html><html><script src="/assets/app-old.js"></script></html>'
    javascript = b'console.log("same frontend");'
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_bytes(index)
    (tmp_path / "assets/app-old.js").write_bytes(javascript)
    assert publish(tmp_path, factory) == {"files": 2, "bytes": len(index) + len(javascript)}
    # Serving succeeds after the source files disappear: PostgreSQL is the source.
    (tmp_path / "index.html").unlink()
    (tmp_path / "assets/app-old.js").unlink()

    def override():
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override
    try:
        with TestClient(app) as client:
            response = client.get("/?page=weather&demo=false")
            assert response.content == index
            assert response.headers["content-type"] == "text/html; charset=utf-8"
            asset = client.get("/assets/app-old.js")
            assert asset.content == javascript
            assert asset.headers["content-type"].startswith("application/javascript")
            assert asset.headers["etag"] == f'"{sha256(javascript).hexdigest()}"'
            cached = client.get("/assets/app-old.js", headers={"If-None-Match": "W/" + asset.headers["etag"]})
            assert cached.status_code == 304
            assert cached.content == b""
            head = client.head("/assets/app-old.js")
            assert head.content == b""
            assert head.headers["content-length"] == str(len(javascript))
            assert client.get("/workspace/weather").content == index
            for path in ("/assets/missing.js", "/api/missing", "/health/missing", "/missing.css"):
                assert client.get(path).status_code == 404
            assert client.get("/health/ready").status_code == 200
            assert client.get("/api/v1/summary").json()["observations"] == 0
            assert client.get("/openapi.json").status_code == 200

            (tmp_path / "index.html").write_bytes(b"updated shell")
            (tmp_path / "assets/app-new.js").write_bytes(b"new chunk")
            publish(tmp_path, factory)
            assert client.get("/").content == b"updated shell"
            assert client.get("/assets/app-old.js").content == javascript
            assert client.get("/assets/app-new.js").content == b"new chunk"
    finally:
        app.dependency_overrides.clear()


def test_publish_rejects_incomplete_build_without_changing_database(factory, tmp_path):
    (tmp_path / "index.html").write_bytes(b"published")
    publish(tmp_path, factory)
    (tmp_path / "index.html").unlink()
    with pytest.raises(ValueError, match="index.html"):
        publish(tmp_path, factory)
    with factory() as session:
        assert session.scalar(select(WebAsset.content).where(WebAsset.path == "index.html")) == b"published"
