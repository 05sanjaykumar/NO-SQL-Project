"""
Print GitHub Actions error annotations for failed or errored tests in a JUnit XML
report, so failures are visible in the run summary without opening the logs.

    python scripts/junit_annotations.py unit-tests.xml
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET


def main(path: str, limit: int = 15) -> None:
    root = ET.parse(path).getroot()
    shown = 0
    for case in root.iter("testcase"):
        for kind in ("failure", "error"):
            node = case.find(kind)
            if node is None:
                continue
            name = f"{case.get('classname', '')}::{case.get('name', '')}"
            text = (node.get("message") or "") + "\n" + (node.text or "")
            text = text.strip().replace("%", "%25").replace("\r", "").replace("\n", "%0A")[:3000]
            print(f"::error title={kind}: {name}::{text}")
            shown += 1
            if shown >= limit:
                return
    if shown == 0:
        print("::notice::no failed or errored test cases in the report")


if __name__ == "__main__":
    main(sys.argv[1])
