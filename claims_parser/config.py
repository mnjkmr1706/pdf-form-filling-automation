"""Environment + credential loading."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_ENV_FILE = Path(__file__).resolve().parent.parent / "parser.env"
load_dotenv(_ENV_FILE)


@dataclass(frozen=True)
class AzureConfig:
    endpoint: str
    key: str


def load_azure_config() -> AzureConfig:
    endpoint = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")
    key = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_KEY")
    if not endpoint or not key:
        raise RuntimeError(
            "Azure credentials missing. Set AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT "
            f"and AZURE_DOCUMENT_INTELLIGENCE_KEY in {_ENV_FILE}."
        )
    return AzureConfig(endpoint=endpoint, key=key)
