"""Create local settings once, without replacing an existing .env or printing secrets."""

from pathlib import Path
import secrets
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collector_credentials import DATA_GO_KR_API_KEY, KMA_APIHUB_KEY

root = Path(__file__).resolve().parents[1]
content = (root / ".env.example").read_text(encoding="utf-8")
content = content.replace("POSTGRES_PASSWORD=", f"POSTGRES_PASSWORD={secrets.token_urlsafe(32)}", 1)
content = content.replace("DATA_GO_KR_API_KEY=", "DATA_GO_KR_API_KEY=" + DATA_GO_KR_API_KEY, 1)
content = content.replace("KMA_APIHUB_KEY=", "KMA_APIHUB_KEY=" + KMA_APIHUB_KEY, 1)
try:
    with (root / ".env").open("x", encoding="utf-8") as output:
        output.write(content)
    print("Created local .env with a random database password; built-in API credentials configured.")
except FileExistsError:
    print("Existing .env preserved. Compare it with .env.example for new settings.")
