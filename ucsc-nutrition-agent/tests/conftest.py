import gzip
import os
import sys
import tempfile
from pathlib import Path

# Isolated data dir + no scheduler / no LLM for tests. Must happen before importing app.
_tmp = tempfile.mkdtemp(prefix="slug-test-")
os.environ["DATA_DIR"] = _tmp
os.environ["ENABLE_SCHEDULER"] = "0"
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("APP_PASSCODE", None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def fixture_html(name: str) -> str:
    return gzip.open(FIX / f"{name}.html.gz", "rt", encoding="utf-8", errors="replace").read()


@pytest.fixture
def html():
    return fixture_html
