"""Read-only Salesforce snapshots. Trusted configuration, fixed queries, no actor tools."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import math
import re
import time
from urllib.parse import quote

from crm_agent.java_client import DomainError
from crm_agent.protocol import ProtocolError, encode, strict_json


class SalesforceError(DomainError):
    def __init__(self, code):
        super().__init__(code, "Read-only Salesforce snapshot stopped: " + code)


@dataclass(frozen=True)
class HttpReply:
    status: int
    headers: dict
    body: bytes


FIELDS = {
    "Opportunity": {"Id": ("id", 18), "Name": ("string", 120), "CloseDate": ("date", 0),
                    "NextStep": ("string", 255), "LastModifiedDate": ("datetime", 0)},
    "Task": {"Id": ("id", 18), "WhatId": ("reference", 18), "Subject": ("string", 255),
             "Description": ("textarea", 32000), "ActivityDate": ("date", 0),
             "LastModifiedDate": ("datetime", 0)},
}
LIMITS = {"max_requests": 32, "max_pages": 4, "max_response_bytes": 262144,
          "max_total_bytes": 2097152, "deadline_seconds": 60, "request_seconds": 20,
          "min_quota_headroom": 100}
PROFILE_KEYS = {"schema_version", "instance_url", "identity_host", "org_id", "user_id",
                "api_version", "opportunity_ids", "task_ids", "external_enabled", *LIMITS}


def sf_id(value, prefix):
    return isinstance(value, str) and re.fullmatch(prefix + r"[A-Za-z0-9]{15}", value) is not None


def profile(value):
    if not isinstance(value, dict) or set(value) != PROFILE_KEYS or type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise SalesforceError("INVALID_CONFIG")
    value = dict(value)
    if (not isinstance(value["instance_url"], str) or not re.fullmatch(
            r"https://[a-z0-9]+(?:-[a-z0-9]+)*(?:\.(?:develop|sandbox))?\.my\.salesforce\.com",
            value["instance_url"]) or not isinstance(value["identity_host"], str) or value["identity_host"] not in {"login.salesforce.com", "test.salesforce.com"}):
        raise SalesforceError("INVALID_CONFIG")
    if not sf_id(value["org_id"], "00D") or not sf_id(value["user_id"], "005"):
        raise SalesforceError("INVALID_CONFIG")
    if not isinstance(value["api_version"], str) or not re.fullmatch(r"v[3-9][0-9]\.0", value["api_version"]):
        raise SalesforceError("INVALID_CONFIG")
    for key, prefix, cap in [("opportunity_ids", "006", 10), ("task_ids", "00T", 8)]:
        ids = value[key]
        if (not isinstance(ids, list) or len(ids) > cap or (key == "opportunity_ids" and not ids)
                or any(not sf_id(item, prefix) for item in ids) or len(ids) != len(set(ids))):
            raise SalesforceError("INVALID_CONFIG")
        value[key] = sorted(ids)
    if type(value["external_enabled"]) is not bool:
        raise SalesforceError("INVALID_CONFIG")
    for key, ceiling in LIMITS.items():
        n = value[key]
        if type(n) is not int or not 1 <= n <= ceiling:
            raise SalesforceError("INVALID_CONFIG")
    if value["max_response_bytes"] > value["max_total_bytes"]:
        raise SalesforceError("INVALID_CONFIG")
    return value


def query_url(config, kind):
    config = profile(config)
    if not isinstance(kind, str) or kind not in FIELDS:
        raise SalesforceError("INVALID_QUERY")
    ids = config["opportunity_ids"] if kind == "Opportunity" else config["task_ids"]
    if not ids:
        raise SalesforceError("INVALID_QUERY")
    clause = ",".join("'" + item + "'" for item in ids)
    statement = "SELECT " + ",".join(FIELDS[kind]) + " FROM " + kind + " WHERE Id IN (" + clause + ")"
    if kind == "Task":
        linked = ",".join("'" + item + "'" for item in config["opportunity_ids"])
        statement += " AND WhatId IN (" + linked + ")"
    statement += " ORDER BY Id LIMIT " + str(len(ids))
    return config["instance_url"] + "/services/data/" + config["api_version"] + "/query?q=" + quote(statement, safe="")


def allowed_url(config, url):
    """Closed GET surface, also checked by the owned HTTP child."""
    config = profile(config)
    if not isinstance(url, str) or len(url) > 2048:
        return False
    identity = "https://" + config["identity_host"] + "/id/" + config["org_id"] + "/" + config["user_id"]
    base = config["instance_url"] + "/services/data/" + config["api_version"]
    fixed = {identity, base + "/limits/", base + "/sobjects/Opportunity/describe/",
             base + "/sobjects/Task/describe/", query_url(config, "Opportunity")}
    if config["task_ids"]:
        fixed.add(query_url(config, "Task"))
    return url in fixed or re.fullmatch(re.escape(base) + r"/query/[A-Za-z0-9]{15,18}-[0-9]{1,9}", url) is not None


class SalesforceSnapshot:
    def __init__(self, config, transport, *, ledger=None, clock=time.monotonic, cancelled=lambda: False):
        self.config = profile(config)
        self.transport, self.clock, self.cancelled = transport, clock, cancelled
        if getattr(transport, "mode", None) not in {"in-memory-double", "local-http-double", "external-salesforce"}:
            raise SalesforceError("INVALID_TRANSPORT")
        if transport.mode == "external-salesforce" and not self.config["external_enabled"]:
            raise SalesforceError("EXTERNAL_DISABLED")
        self.ledger = ledger if ledger is not None else {"requests": 0, "received_bytes": 0, "active_milliseconds": 0}
        if (set(self.ledger) != {"requests", "received_bytes", "active_milliseconds"} or
                any(type(n) is not int or not 0 <= n <= 86400000 for n in self.ledger.values()) or
                self.ledger["requests"] > self.config["max_requests"] or
                self.ledger["received_bytes"] > self.config["max_total_bytes"]):
            raise SalesforceError("INVALID_ACCOUNTING")
        self.deadline = None

    def _check(self):
        if self.cancelled():
            raise SalesforceError("CANCELLED")
        if self.deadline is None or self.clock() >= self.deadline:
            raise SalesforceError("DEADLINE")

    def _get(self, url, *, quota=True):
        self._check()
        if not allowed_url(self.config, url):
            raise SalesforceError("SCOPE_DENIED")
        if self.ledger["requests"] >= self.config["max_requests"]:
            raise SalesforceError("REQUEST_LIMIT")
        remaining_bytes = self.config["max_total_bytes"] - self.ledger["received_bytes"]
        if remaining_bytes <= 0:
            raise SalesforceError("BODY_LIMIT")
        self.ledger["requests"] += 1  # an attempted dispatch remains charged after ambiguous failure
        try:
            reply = self.transport.get(url, deadline=min(self.deadline, self.clock() + self.config["request_seconds"]),
                max_bytes=min(self.config["max_response_bytes"], remaining_bytes), cancelled=self.cancelled)
        except SalesforceError:
            raise
        except TimeoutError:
            raise SalesforceError("DEADLINE") from None
        except Exception:
            raise SalesforceError("TRANSPORT_ERROR") from None
        self._check()
        if (not isinstance(reply, HttpReply) or type(reply.status) is not int or not 100 <= reply.status <= 599
                or not isinstance(reply.body, bytes) or not isinstance(reply.headers, dict)):
            raise SalesforceError("INVALID_RESPONSE")
        self.ledger["received_bytes"] += len(reply.body)
        if len(reply.body) > self.config["max_response_bytes"] or self.ledger["received_bytes"] > self.config["max_total_bytes"]:
            raise SalesforceError("BODY_LIMIT")
        headers = {}
        for key, value in reply.headers.items():
            if not isinstance(key, str) or not isinstance(value, str) or key.lower() in headers:
                raise SalesforceError("INVALID_RESPONSE")
            headers[key.lower()] = value
        if len(headers) > 32 or sum(len(k)+len(v) for k,v in headers.items()) > 16384:
            raise SalesforceError("INVALID_RESPONSE")
        try:
            packet = strict_json(reply.body.decode("utf-8"))
        except (ValueError, UnicodeError):
            raise SalesforceError("INVALID_RESPONSE") from None
        codes = set()
        if isinstance(packet, list):
            for item in packet:
                if not isinstance(item, dict) or not isinstance(item.get("errorCode"), str):
                    raise SalesforceError("INVALID_RESPONSE")
                codes.add(item["errorCode"])
        if "REQUEST_LIMIT_EXCEEDED" in codes or reply.status == 429:
            raise SalesforceError("QUOTA_EXCEEDED")
        if reply.status in {401, 403}:
            raise SalesforceError("AUTH_DENIED")
        if reply.status != 200:
            raise SalesforceError("UPSTREAM_ERROR")
        if headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            raise SalesforceError("INVALID_RESPONSE")
        if not isinstance(packet, dict):
            raise SalesforceError("INVALID_RESPONSE")
        if quota:
            match = re.fullmatch(r"api-usage=([0-9]{1,12})/([0-9]{1,12})(?:;\s*[^;\r\n]{1,256})*",
                                 headers.get("sforce-limit-info", ""))
            if not match:
                raise SalesforceError("QUOTA_UNKNOWN")
            used, maximum = map(int, match.groups())
            reserve = self.config["min_quota_headroom"] + self.config["max_requests"] - self.ledger["requests"]
            if used > maximum or maximum - used < reserve:
                raise SalesforceError("QUOTA_EXCEEDED")
        self._check()
        return packet

    def _describe(self, kind):
        base = self.config["instance_url"] + "/services/data/" + self.config["api_version"]
        packet = self._get(base + "/sobjects/" + kind + "/describe/")
        fields = packet.get("fields")
        if (packet.get("name") != kind or packet.get("queryable") is not True or
                not isinstance(fields, list) or not 1 <= len(fields) <= 512):
            raise SalesforceError("SCHEMA_DRIFT")
        observed = {}
        for item in fields:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str) or item["name"] in observed:
                raise SalesforceError("SCHEMA_DRIFT")
            observed[item["name"]] = item
        for name, (kind_type, length) in FIELDS[kind].items():
            field = observed.get(name, {})
            if (field.get("type") != kind_type or type(field.get("length")) is not int or field["length"] != length
                    or type(field.get("updateable")) is not bool):
                raise SalesforceError("SCHEMA_DRIFT")
        return hashlib.sha256(encode({n: {k: observed[n][k] for k in ("type","length","updateable")}
                                      for n in FIELDS[kind]})).hexdigest()

    def _record(self, kind, row):
        if not isinstance(row, dict) or set(row) - (set(FIELDS[kind]) | {"attributes"}) or not set(FIELDS[kind]) <= set(row):
            raise SalesforceError("SCHEMA_DRIFT")
        expected = self.config["opportunity_ids"] if kind == "Opportunity" else self.config["task_ids"]
        if row["Id"] not in expected or (kind == "Task" and row["WhatId"] not in self.config["opportunity_ids"]):
            raise SalesforceError("SCOPE_DENIED")
        if "attributes" in row and (not isinstance(row["attributes"], dict) or row["attributes"].get("type") != kind):
            raise SalesforceError("SCHEMA_DRIFT")
        for name, (kind_type, length) in FIELDS[kind].items():
            value = row[name]
            nullable = name in {"NextStep", "Description", "ActivityDate"}
            if value is None and nullable:
                continue
            if not isinstance(value, str) or any(ord(c) < 32 and c not in "\n\t" for c in value):
                raise SalesforceError("SCHEMA_DRIFT")
            try:
                if kind_type in {"string", "textarea"}:
                    if len(value) > length:
                        raise ValueError()
                elif kind_type == "date":
                    if date.fromisoformat(value).isoformat() != value:
                        raise ValueError()
                elif kind_type == "datetime":
                    parsed = datetime.fromisoformat(value.replace("Z","+00:00"))
                    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
                        raise ValueError()
                elif len(value) != length or re.fullmatch(r"[A-Za-z0-9]{18}", value) is None:
                    raise ValueError()
            except ValueError:
                raise SalesforceError("SCHEMA_DRIFT") from None
        return {key: row[key] for key in FIELDS[kind]}  # attributes/returned links never become authority

    def _query(self, kind):
        ids = self.config["opportunity_ids"] if kind == "Opportunity" else self.config["task_ids"]
        if not ids:
            return []
        url, seen_urls, seen_ids, rows, total = query_url(self.config, kind), set(), set(), [], None
        base = self.config["instance_url"]
        for page in range(self.config["max_pages"]):
            if url in seen_urls:
                raise SalesforceError("PAGINATION_ABUSE")
            seen_urls.add(url)
            packet = self._get(url)
            if set(packet) - {"records","done","totalSize","nextRecordsUrl"}:
                raise SalesforceError("INVALID_RESPONSE")
            n, done, records = packet.get("totalSize"), packet.get("done"), packet.get("records")
            if type(n) is not int or not 0 <= n <= len(ids) or type(done) is not bool or not isinstance(records, list):
                raise SalesforceError("PAGINATION_ABUSE")
            if total is not None and total != n:
                raise SalesforceError("PAGINATION_ABUSE")
            total = n
            if not done and not records:
                raise SalesforceError("PAGINATION_ABUSE")
            for row in records:
                row = self._record(kind, row)
                if row["Id"] in seen_ids:
                    raise SalesforceError("PAGINATION_ABUSE")
                seen_ids.add(row["Id"])
                rows.append(row)
                if len(rows) > total:
                    raise SalesforceError("PAGINATION_ABUSE")
            if done:
                if packet.get("nextRecordsUrl") is not None or len(rows) != total:
                    raise SalesforceError("PAGINATION_ABUSE")
                if seen_ids != set(ids):
                    raise SalesforceError("SCOPED_RECORD_MISSING")
                return sorted(rows, key=lambda row: row["Id"])
            locator = packet.get("nextRecordsUrl")
            prefix = "/services/data/" + self.config["api_version"] + "/query/"
            if not isinstance(locator, str) or not re.fullmatch(re.escape(prefix)+r"[A-Za-z0-9]{15,18}-[0-9]{1,9}", locator):
                raise SalesforceError("PAGINATION_ABUSE")
            url = base + locator
        raise SalesforceError("PAGE_LIMIT")

    def snapshot(self):
        start = self.clock()
        self.deadline = start + self.config["deadline_seconds"] - self.ledger["active_milliseconds"]/1000
        try:
            c = self.config
            identity_url = "https://" + c["identity_host"] + "/id/" + c["org_id"] + "/" + c["user_id"]
            identity = self._get(identity_url, quota=False)
            if (identity.get("organization_id") != c["org_id"] or identity.get("user_id") != c["user_id"]
                    or identity.get("id") != identity_url or identity.get("active") is not True
                    or identity.get("asserted_user") is not True):
                raise SalesforceError("IDENTITY_MISMATCH")
            base = c["instance_url"] + "/services/data/" + c["api_version"]
            quota = self._get(base + "/limits/").get("DailyApiRequests", {})
            if not isinstance(quota, dict):
                raise SalesforceError("QUOTA_UNKNOWN")
            maximum, remaining = quota.get("Max"), quota.get("Remaining")
            if type(maximum) is not int or type(remaining) is not int or not 0 <= remaining <= maximum:
                raise SalesforceError("QUOTA_UNKNOWN")
            if remaining < c["min_quota_headroom"] + c["max_requests"] - self.ledger["requests"]:
                raise SalesforceError("QUOTA_EXCEEDED")
            schemas = {kind: self._describe(kind) for kind in FIELDS}
            opportunities, tasks = self._query("Opportunity"), self._query("Task")
            self._check()
            return {"schema_version":1, "mode":self.transport.mode, "read_only":True,
                    "consistent_transaction":False, "local_write_authority":False,
                    "schema_fingerprints":schemas, "opportunities":[{"source_id":p["Id"],"name":p["Name"],
                        "close_date":p["CloseDate"],"next_step":p["NextStep"],"source_last_modified":p["LastModifiedDate"]}
                        for p in opportunities],
                    "tasks":[{"source_id":p["Id"],"opportunity_id":p["WhatId"],"subject":p["Subject"],
                        "description":p["Description"],"activity_date":p["ActivityDate"],
                        "source_last_modified":p["LastModifiedDate"]} for p in tasks]}
        finally:
            self.ledger["active_milliseconds"] += max(0, math.ceil((self.clock()-start)*1000))
            self.deadline = None

