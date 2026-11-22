"""User-approved built-in collector credentials; environment overrides remain supported."""

import os
from pathlib import Path

DATA_GO_KR_API_KEY = "b8d30a0eed788661bae141209974830dadd99294234a65dfa03fcf1a7fc1af23"
KMA_APIHUB_KEY = "Sf4g0vFQR-2-INLxUCftMg"


def get_api_key(name, env_file=None, environment=None):
    values = os.environ if environment is None else environment
    selected = values.get(name, "").strip()
    if selected:
        return selected
    if env_file is not None and Path(env_file).is_file():
        from dotenv import dotenv_values

        selected = (dotenv_values(env_file).get(name) or "").strip()
        if selected:
            return selected
    defaults = {"DATA_GO_KR_API_KEY": DATA_GO_KR_API_KEY, "KMA_APIHUB_KEY": KMA_APIHUB_KEY}
    return defaults[name]
