"""Read-only HTTP checks of the running Compose stack; no demo data is inserted."""

import argparse
import json
import urllib.request


def get(url):
    with urllib.request.urlopen(url, timeout=15) as response:
        assert response.status == 200
        return response.read()


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--api-url", default="http://localhost:8000")
parser.add_argument("--web-url", default="http://localhost:3000")
args = parser.parse_args()
api_url, web_url = args.api_url.rstrip("/"), args.web_url.rstrip("/")

assert json.loads(get(f"{api_url}/health/ready"))["status"] == "ready"
assert "observations" in json.loads(get(f"{web_url}/api/v1/summary"))
for route in ("observations", "predictions", "settlements", "model-versions", "training-runs", "job-runs"):
    assert isinstance(json.loads(get(f"{web_url}/api/v1/{route}")), list)
assert b'<div id="root">' in get(f"{web_url}/")
assert "portfolios" in json.loads(get(f"{web_url}/api/v1/dashboard/options"))
assert len(json.loads(get(f"{web_url}/api/v1/dashboard?date=2026-01-01"))["series"]) == 24
demo = json.loads(get(f"{web_url}/api/v1/dashboard?demo=true&date=2026-01-01&portfolio_id=1"))
assert demo["is_demo"] and len(demo["plants"]) == 4
assert demo["summary"]["capacity_kw"] == 20000 and demo["summary"]["settlement_krw"] is None
print("API readiness, six DB read endpoints, web proxy and React HTML: passed")
print("Dashboard options, period bins, explicit demo portfolio and unset settlement: passed")
