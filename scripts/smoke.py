"""Read-only HTTP checks of the running Compose stack; no demo data is inserted."""

import json
import urllib.request


def get(url):
    with urllib.request.urlopen(url, timeout=15) as response:
        assert response.status == 200
        return response.read()


assert json.loads(get("http://localhost:8000/health/ready"))["status"] == "ready"
assert "observations" in json.loads(get("http://localhost:3000/api/v1/summary"))
for route in ("observations", "predictions", "settlements", "model-versions", "training-runs", "job-runs"):
    assert isinstance(json.loads(get(f"http://localhost:3000/api/v1/{route}")), list)
assert b'<div id="root">' in get("http://localhost:3000/")
print("API readiness, six DB read endpoints, web proxy and React HTML: passed")
