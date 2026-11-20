"""Intake for separate Codex reviewers and optional bounded CLI transport."""
from pathlib import Path

from cases import AB_CRITERIA, RULES, old
from hard import anchor_text, anchored

VERDICTS = {"PASS", "PARTIAL", "FAIL", "UNSURE"}
SEVERITIES = {"CRITICAL", "MAJOR", "MINOR", "STRENGTH"}
ROLES = {
    "evidence": "Check actual evidence support, statistical scope, period, denominator, alternatives to a causal claim and source independence.",
    "reader": "Check how it answers the task, analysis depth, alternatives, action conditions, natural prose, concision, and utility to this reader.",
    "visual": "Look at EVERY attached actual PDF page; assess figure and table alignment, rationale, axis/units, information density and readability.",
}
ROW_FORMAT = (
    '{"criterion":"criterion","verdict":"PASS|PARTIAL|FAIL|UNSURE","confidence":0.8,'
    '"findings":[{"severity":"CRITICAL|MAJOR|MINOR|STRENGTH","page":1,"quote":"literal excerpt",'
    '"evidence_ids":[],"reason":"why","improvement":"action"}]}'
)


def validate_findings(packet, findings):
    if not isinstance(findings, list) or not findings:
        return [], ["findings missing"]
    good, errors = [], []
    for entry in findings:
        if not isinstance(entry, dict):
            errors.append("finding must be object")
            continue
        keys = {"severity", "quote", "evidence_ids", "reason", "improvement"}
        locator = {"line", "page"} & entry.keys()
        ids = entry.get("evidence_ids")
        if (len(locator) != 1 or set(entry) != keys | locator or entry["severity"] not in SEVERITIES
                or not isinstance(ids, list) or any(not isinstance(i, str) or i not in packet["evidence"] for i in ids)
                or not all(isinstance(entry[k], str) and entry[k].strip() for k in ("reason", "improvement"))
                or not anchored(entry["quote"], anchor_text(packet, entry))):
            errors.append(f"unbound finding { {k: entry.get(k) for k in ('page', 'line', 'quote', 'evidence_ids')} }")
        else:
            good.append(entry)
    return good, errors


def validate_case(packet, rows, role, viewed=None):
    findings = {}
    errors = []
    expected = RULES[role]
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or set(row) != {"criterion", "verdict", "confidence", "findings"}:
            errors.append("review criterion schema")
            continue
        criterion, value, confidence = row["criterion"], row["verdict"], row["confidence"]
        if (criterion not in expected or criterion in findings or value not in VERDICTS
                or type(confidence) not in {int, float} or not 0 <= confidence <= 1):
            errors.append(f"bad/duplicate criterion or verdict {criterion}")
            continue
        bound, unbound = validate_findings(packet, row["findings"])
        errors.extend(unbound)
        findings[criterion] = {**row, "findings": bound}
    if set(findings) != set(expected):
        errors.append(f"missing criteria {sorted(set(expected) - findings.keys())}")
    if role == "visual":
        pages = [p["page"] for p in packet["pdf_pages"]]
        if viewed != pages or not pages:
            errors.append("every physical page must be viewed in order")
        if any("page" not in f for row in findings.values() for f in row["findings"]):
            errors.append("visual findings require physical page indices")
    return {"status": "ERROR" if errors else "OK", "errors": errors, "criteria": findings}


def import_responses(root, role, cases, path, execution):
    results = {}
    try:
        response = old.read_json(path) if path.is_file() else None
    except (ValueError, OSError) as exc:
        return {key: {"status": "ERROR", "reason": str(exc), "execution": execution} for key in cases}
    for key in cases:
        case_dir = root / "packets" / key
        packet = old.read_json(case_dir / "packet.json")
        if response is None or not execution or execution.get("status") != "OK":
            results[key] = {"status": "NOT_RUN", "reason": "缺独立评审执行记录或响应。"}
            continue
        try:
            result = validate_case(packet, response["cases"][key], role,
                                   response.get("viewed_pages", {}).get(key))
            if role == "visual" and any(old.digest(case_dir / p["image"]) != p["image_sha256"]
                                        for p in packet["pdf_pages"]):
                result["errors"].append("page image changed")
                result["status"] = "ERROR"
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            result = {"status": "ERROR", "errors": [str(exc)], "criteria": {}}
        results[key] = {**result, "execution": execution, "response_sha256": old.digest(path),
                        "packet_sha256": old.digest(case_dir / "packet.json")}
    return results


def validate_ab(raw, pair):
    if not isinstance(raw, dict) or set(raw) != {"preference", "findings"}:
        raise ValueError("A/B reply schema")
    if raw["preference"] not in {"A", "B", "TIE", "UNSURE"}:
        raise ValueError("A/B preference")
    criteria = {}
    for item in raw["findings"]:
        if set(item) != {"criterion", "better", "A", "B"} or item["criterion"] not in AB_CRITERIA:
            raise ValueError("A/B criterion schema")
        key = item["criterion"]
        if key in criteria or item["better"] not in {"A", "B", "TIE", "UNSURE"}:
            raise ValueError("A/B duplicate criterion or bad preference")
        for label in ("A", "B"):
            _, errors = validate_findings(old.read_json(pair / (label + ".json")), [item[label]])
            if errors:
                raise ValueError(str(errors))
        criteria[key] = item
    if set(criteria) != set(AB_CRITERIA):
        raise ValueError("A/B incomplete criteria")
    return raw


def compare_orders(root, executions):
    resolved = {}
    for order, labels in (("first", {"A": "baseline", "B": "candidate"}),
                          ("swapped", {"A": "candidate", "B": "baseline"})):
        path = root / f"pair-{order}-response.json"
        execution = executions.get(f"pair-{order}")
        if not path.is_file() or not execution or execution.get("status") != "OK":
            return {"status": "NOT_RUN", "preference": "UNSURE", "orders": resolved}
        try:
            value = validate_ab(old.read_json(path), root / f"pair-{order}")
        except (KeyError, TypeError, ValueError, OSError) as exc:
            return {"status": "ERROR", "preference": "UNSURE", "orders": resolved, "reason": str(exc)}
        pick = value["preference"]
        resolved[order] = {"execution": execution, "sha256": old.digest(path), "raw": value,
                           "preference": labels.get(pick, pick),
                           "dimensions": {i["criterion"]: labels.get(i["better"], i["better"])
                                          for i in value["findings"]}}
    first, swapped = resolved.values()
    conflict = first["preference"] != swapped["preference"]
    scores = {key: first["dimensions"][key] if first["dimensions"][key] == swapped["dimensions"][key] else "UNSURE"
              for key in AB_CRITERIA}
    return {"status": "OK", "preference": "UNSURE" if conflict else first["preference"],
            "order_conflict": conflict, "dimensions": scores, "orders": resolved}
