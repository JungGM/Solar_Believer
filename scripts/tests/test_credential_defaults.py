import hashlib
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

from collector_credentials import DATA_GO_KR_API_KEY, KMA_APIHUB_KEY, get_api_key


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def test_both_collectors_have_builtin_keys_without_environment_or_env_file(tmp_path):
    for name, expected in (("DATA_GO_KR_API_KEY", DATA_GO_KR_API_KEY), ("KMA_APIHUB_KEY", KMA_APIHUB_KEY)):
        assert bool(expected)
        assert digest(get_api_key(name, tmp_path / "missing.env", environment={})) == digest(expected)


def test_explicit_environment_and_file_keys_override_builtin_defaults(tmp_path):
    path = tmp_path / "settings.env"
    path.write_text("DATA_GO_KR_API_KEY=file-key\n", encoding="utf-8")
    assert (
        get_api_key("DATA_GO_KR_API_KEY", path, environment={"DATA_GO_KR_API_KEY": "environment-key"})
        == "environment-key"
    )
    assert get_api_key("DATA_GO_KR_API_KEY", path, environment={}) == "file-key"


def test_fresh_pc_initialization_populates_keys_without_manual_input(tmp_path):
    root = Path(__file__).resolve().parents[2]
    (tmp_path / "scripts").mkdir()
    for source, target in (
        (root / "scripts/init_env.py", tmp_path / "scripts/init_env.py"),
        (root / "collector_credentials.py", tmp_path / "collector_credentials.py"),
        (root / ".env.example", tmp_path / ".env.example"),
    ):
        target.write_bytes(source.read_bytes())
    result = subprocess.run(
        [sys.executable, str(tmp_path / "scripts/init_env.py")], capture_output=True, check=True
    )
    values = dotenv_values(tmp_path / ".env")
    assert bool(values["POSTGRES_PASSWORD"])
    assert digest(values["DATA_GO_KR_API_KEY"]) == digest(DATA_GO_KR_API_KEY)
    assert digest(values["KMA_APIHUB_KEY"]) == digest(KMA_APIHUB_KEY)
    assert DATA_GO_KR_API_KEY.encode() not in result.stdout
    assert KMA_APIHUB_KEY.encode() not in result.stdout
