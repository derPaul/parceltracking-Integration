"""Install the requirements of the core integrations ai_task depends on.

They ship with every Home Assistant install but not with the homeassistant
package; the versions are pinned in the manifests of the installed release.
"""

import json
import subprocess
import sys
from pathlib import Path

import homeassistant.components

COMPONENTS = ("ai_task", "conversation", "camera", "image", "media_source")
root = Path(homeassistant.components.__path__[0])
requirements = sorted(
    {
        req
        for name in COMPONENTS
        for req in json.loads((root / name / "manifest.json").read_text()).get(
            "requirements", []
        )
    }
)
print("Installing", *requirements)
if requirements:
    subprocess.check_call([sys.executable, "-m", "pip", "install", *requirements])
