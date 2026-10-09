"""Audit exactly two approved artifacts and their POMs/licenses; no build ecosystem."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CAP = 2 * 1024 * 1024
SCOPE = "m1-closure-recovery"
CENTRAL = "https://repo.maven.apache.org/maven2/"
APPROVED = {
    "com.google.code.gson:gson:2.14.0": {
        "filename": "gson-2.14.0.jar", "path": "com/google/code/gson/gson/2.14.0/gson-2.14.0",
        "license_url": "https://raw.githubusercontent.com/google/gson/gson-parent-2.14.0/LICENSE"},
    "com.google.errorprone:error_prone_annotations:2.48.0": {
        "filename": "error_prone_annotations-2.48.0.jar",
        "path": "com/google/errorprone/error_prone_annotations/2.48.0/error_prone_annotations-2.48.0",
        "license_url": "https://raw.githubusercontent.com/google/error-prone/v2.48.0/COPYING"},
}
PARENTS = {
    "com.google.code.gson:gson-parent:2.14.0": CENTRAL + "com/google/code/gson/gson-parent/2.14.0/gson-parent-2.14.0.pom",
    "com.google.errorprone:error_prone_parent:2.48.0": CENTRAL + "com/google/errorprone/error_prone_parent/2.48.0/error_prone_parent-2.48.0.pom",
}
ALLOWED_URLS = set(PARENTS.values())
for specification in APPROVED.values():
    base = CENTRAL + specification["path"]
    ALLOWED_URLS.update({base + ".jar", base + ".jar.sha256", base + ".pom", specification["license_url"]})
NS = {"m": "http://maven.apache.org/POM/4.0.0"}
DOWNLOADS = []
CACHE = {}
LEDGER = ROOT / "work/bootstrap-fetches.jsonl"


def ledger_entry(value):
    LEDGER.parent.mkdir(exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(dict(value, scope=SCOPE, at=datetime.now(timezone.utc).isoformat())) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def used_bytes():
    if not LEDGER.exists():
        return 0
    return sum(item.get("bytes", 0) for item in (json.loads(line) for line in LEDGER.read_text(encoding="utf-8").splitlines()) if item.get("scope") == SCOPE)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, url):
        raise ValueError("Dependency redirects are forbidden")


def fetch(url, maximum):
    if url in CACHE:
        return CACHE[url]
    if url not in ALLOWED_URLS:
        raise ValueError("URL is not in the exact approved artifact/POM/license set")
    remaining = CAP - used_bytes()
    if remaining <= 0:
        raise ValueError("Cumulative new transfer budget exhausted before request")
    key = str(uuid.uuid4())
    ledger_entry({"request_id": key, "url": url, "event": "started", "bytes": 0, "remaining_budget": remaining})
    result = bytearray()
    complete = False
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        request = urllib.request.Request(url, headers={"Accept-Encoding": "identity"})
        response_error = None
        try:
            response = opener.open(request, timeout=20)
        except urllib.error.HTTPError as error:
            response, response_error = error, error
        with response:
            if response.geturl() != url:
                raise ValueError("Unexpected response origin")
            declared = response.headers.get("Content-Length")
            if declared is not None and int(declared) > min(maximum, remaining):
                raise ValueError("Response would exceed per-request/cumulative transfer cap")
            limit = min(maximum, remaining)
            while len(result) < limit:
                chunk = response.read(min(8192, limit - len(result)))
                if not chunk:
                    complete = True
                    break
                result.extend(chunk)
                ledger_entry({"request_id": key, "url": url, "event": "received", "bytes": len(chunk)})
            if declared is not None and len(result) == int(declared):
                complete = True
            if not complete:
                raise ValueError("Transfer reached bounded read limit before confirmed completion")
        data = bytes(result)
        receipt = {"request_id": key, "url": url, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        DOWNLOADS.append(receipt)
        if response_error is not None:
            raise ValueError("HTTP error after measured response body: " + str(response_error.code))
        CACHE[url] = data
        return data
    finally:
        ledger_entry({"request_id": key, "url": url, "event": "finished", "bytes": 0,
                      "received_bytes": len(result), "complete": complete, "sha256": hashlib.sha256(result).hexdigest()})


def field(element, name, default=""):
    if element is None:
        return default
    return element.findtext("m:" + name, default=default, namespaces=NS)


def pom_audit(url):
    documents = []
    current = url
    for _ in range(3):
        data = fetch(current, 262144)
        root = ET.fromstring(data)
        documents.append((current, data, root))
        parent = root.find("m:parent", NS)
        if parent is None:
            break
        coordinate = ":".join(field(parent, name) for name in ("groupId", "artifactId", "version"))
        if coordinate not in PARENTS:
            raise ValueError("Unexpected inherited POM needs review: " + coordinate)
        current = PARENTS[coordinate]
    else:
        raise ValueError("Parent chain exceeds bounded audit")
    properties, declarations, licenses = {}, {}, []
    for origin, data, root in reversed(documents):
        props = root.find("m:properties", NS)
        if props is not None:
            properties.update({item.tag.split("}")[-1]: item.text or "" for item in props})
        version = field(root, "version") or field(root.find("m:parent", NS), "version")
        group = field(root, "groupId") or field(root.find("m:parent", NS), "groupId")
        local = dict(properties, **{"project.version": version, "project.groupId": group})
        def resolve(value):
            for _ in range(4):
                expanded = re.sub(r"\$\{([^}]+)\}", lambda match: local.get(match.group(1), match.group(0)), value)
                if expanded == value:
                    break
                value = expanded
            return value
        for item in root.findall("./m:dependencies/m:dependency", NS):
            entry = {"group": resolve(field(item, "groupId")), "artifact": resolve(field(item, "artifactId")),
                     "version": resolve(field(item, "version")), "scope": field(item, "scope", "compile"),
                     "optional": field(item, "optional", "false"), "type": field(item, "type", "jar"), "declared_in": origin}
            declarations[(entry["group"], entry["artifact"])] = entry
        licenses.extend({"name": field(item, "name"), "url": field(item, "url"), "declared_in": origin}
                        for item in root.findall("./m:licenses/m:license", NS))
    if not any("apache" in item["name"].lower() and "2.0" in item["name"] + item["url"] for item in licenses):
        raise ValueError("Published POM license is not Apache-2.0")
    required = [item for item in declarations.values() if item["scope"] != "test" and item["optional"] != "true"]
    unexpected = [item for item in required if ":".join(item[name] for name in ("group", "artifact", "version")) not in APPROVED or item["type"] != "jar"]
    return {"poms": [{"url": origin, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()} for origin, data, _ in documents],
            "declared_dependencies": list(declarations.values()), "required_non_test_dependencies": required,
            "unexpected_dependencies": unexpected, "published_licenses": licenses}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch-deps", action="store_true", required=True)
    parser.add_argument("--prepare-lock", action="store_true")
    args = parser.parse_args(argv)
    lock_path = ROOT / "dependencies.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    if [item["coordinate"] for item in lock["dependencies"]] != list(APPROVED):
        raise ValueError("Lock must contain exactly the two approved coordinates in order")
    audit = {"schema_version": 1, "transfer_scope": SCOPE, "prior_failed_transfer_bytes": None, "artifacts": [], "status": "auditing"}
    audit_path = ROOT / "docs/dependency-audit.json"
    before = used_bytes()
    for item in lock["dependencies"]:
        specification = APPROVED[item["coordinate"]]
        base = CENTRAL + specification["path"]
        if item["filename"] != specification["filename"] or item["url"] != base + ".jar" or item["pom_url"] != base + ".pom":
            raise ValueError("Lock URL/filename differs from approved artifact")
        target = ROOT / ".deps" / item["filename"]
        expected = item["sha256"]
        if expected is None and not args.prepare_lock:
            raise ValueError("Maintainer audit required before ordinary pinned bootstrap")
        if expected and target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == expected and not args.prepare_lock:
            continue
        closure = pom_audit(item["pom_url"])
        artifact_audit = dict(closure, coordinate=item["coordinate"])
        audit["artifacts"].append(artifact_audit)
        if closure["unexpected_dependencies"]:
            audit.update(status="requires_dependency_review", cumulative_new_transfer_bytes=used_bytes())
            audit_path.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
            raise ValueError("Additional required non-test dependency needs review: " + json.dumps(closure["unexpected_dependencies"]))
        digest = fetch(item["url"] + ".sha256", 1024).decode("ascii").strip().split()[0].lower()
        if not re.fullmatch("[0-9a-f]{64}", digest) or expected and expected != digest:
            raise ValueError("Invalid or changed upstream artifact checksum")
        data = fetch(item["url"], 1048576)
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("Artifact checksum mismatch")
        target.parent.mkdir(exist_ok=True)
        temporary = target.with_suffix(".download")
        temporary.write_bytes(data)
        try:
            with zipfile.ZipFile(temporary) as archive:
                notices = [name for name in archive.namelist() if name.upper().endswith(("LICENSE", "LICENSE.TXT", "NOTICE", "NOTICE.TXT"))]
                if args.prepare_lock:
                    notice_directory = ROOT / "docs/dependency-notices"
                    notice_directory.mkdir(parents=True, exist_ok=True)
                    for name in notices:
                        (notice_directory / (target.stem + "-embedded-" + Path(name).name)).write_bytes(archive.read(name))
            license_data = fetch(specification["license_url"], 65536)
            if b"Apache License" not in license_data or b"Version 2.0" not in license_data:
                raise ValueError("Unexpected upstream license text")
            if args.prepare_lock:
                (ROOT / "docs/dependency-notices" / (target.stem + "-upstream-LICENSE.txt")).write_bytes(license_data)
            artifact_audit.update(artifact_bytes=len(data), sha256=digest, packaged_notices=notices,
                                  notice_finding="No embedded license/notice files" if not notices else "Embedded files retained verbatim",
                                  upstream_license={"url": specification["license_url"], "bytes": len(license_data), "sha256": hashlib.sha256(license_data).hexdigest()})
            temporary.replace(target)
            item.update(sha256=digest, verification_status="Verified JAR/checksum, exact published POM/parent closure, and upstream license")
        finally:
            temporary.unlink(missing_ok=True)
    audit.update(status="verified", this_command_transfer_bytes=used_bytes() - before,
                 cumulative_new_transfer_bytes=used_bytes(), transfer_cap_bytes=CAP, downloads=DOWNLOADS)
    if args.prepare_lock:
        lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
        audit_path.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"dependencies": [{"coordinate": item["coordinate"], "sha256": item["sha256"]} for item in lock["dependencies"]],
                      "this_command_transfer_bytes": used_bytes() - before, "cumulative_new_transfer_bytes": used_bytes(), "status": "verified"}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, ET.ParseError, zipfile.BadZipFile) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
