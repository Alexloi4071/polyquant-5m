import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

def load_config(config_path: str = "config/config.yaml") -> dict:
    load_dotenv()
    path = Path(config_path)
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)
