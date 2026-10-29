"""Record installed runtime/test dependency versions without local paths."""
from importlib.metadata import distributions
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
excluded = {"flight-agent-mcp", "pip", "setuptools"}
packages = {dist.metadata["Name"]: dist.version for dist in distributions()
            if dist.metadata["Name"].lower().replace("_", "-") not in excluded}
(ROOT / "requirements.lock").write_text(
    "# Verified on Python 3.12; includes offline test dependencies.\n"
    + "\n".join(f"{name}=={packages[name]}" for name in sorted(packages, key=str.lower)) + "\n",
    encoding="utf8")
