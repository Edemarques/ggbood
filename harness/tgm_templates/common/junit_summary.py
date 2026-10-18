"""Summarise a JUnit XML report: "<tests> <failed> <id-set-sha256>" (empty output = no usable report).

Every <testcase> anywhere in the document counts, whatever the nesting (<testsuites>/<testsuite>/...). A testcase
fails when it has a <failure> or <error> child. Its id is "classname::name"; the id set is sorted and hashed.
Usable from the shell (python3 junit_summary.py REPORT) and as a module (summary(path) -> dict or None).
"""
import hashlib
import sys
import xml.etree.ElementTree as ET


def summary(path):
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return None
    ids, failed = [], []
    for tc in root.iter("testcase"):
        tid = "{}::{}".format(tc.get("classname") or "", tc.get("name") or "")
        ids.append(tid)
        if any(child.tag in ("failure", "error") for child in tc):
            failed.append(tid)
    # A suite-level <error>/<failure> outside any testcase (e.g. a crashed module) also counts as a failure.
    for el in root.iter():
        if el.tag in ("testsuite", "testsuites"):
            for child in el:
                if child.tag in ("failure", "error"):
                    failed.append("<suite-level>")
    ids.sort()
    if not ids:
        return None
    return {"tests": len(ids), "failed": len(failed), "ids": ids, "failed_ids": sorted(set(failed)),
            "sha": hashlib.sha256("\n".join(ids).encode()).hexdigest()}


if __name__ == "__main__":
    s = summary(sys.argv[1])
    if s:
        print(s["tests"], s["failed"], s["sha"])
