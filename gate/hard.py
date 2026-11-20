"""Deterministic checks on report data and submitted claim annotations."""
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
import subprocess
import unicodedata

from cases import old, source_rows

ARTIFACTS = {"candidate.md", "report.pdf", "report.docx"}
CITATIONS = re.compile(r"\[\[([A-Za-z0-9:_-]+)\]\]")


def normalized(text):
    return old.compact(unicodedata.normalize("NFKC", text)).replace("−", "-")


def anchored(needle, haystack):
    return isinstance(needle, str) and 3 <= len(normalized(needle)) and normalized(needle) in normalized(haystack)


def anchor_text(packet, locator):
    for field, name in (("line", "report_lines"), ("page", "pdf_pages")):
        if field in locator:
            value = locator[field]
            if type(value) is not int:
                break
            for row in packet[name]:
                if row[field] == value:
                    return row["text"]
    return ""


def check(rule, status, finding, location, action):
    return old.check(rule, status, location, finding, action)


def relation_check(bench, task, claim, evidence):
    """Verify only the annotated relation and its actual anchored evidence."""
    relation = claim["relation"]
    basis = claim["basis"]
    if not basis:
        status = "FAIL" if relation == "causal" else "NEEDS_REVIEW"
        return status, "未登记证据，无法支持此主张。"
    available = source_rows(bench, task)
    if task == "C" and (set(basis) & (bench.full.keys() - bench.market.keys())):
        return "FAIL", "绑定了明确撤销的累计出口证据。"
    if set(basis) - evidence.keys():
        return "FAIL", "证据 ID 未登记或不可用。"
    if relation == "inventory_flow":
        operands = set(claim.get("operands", []))
        annotated = {"surface": "正文", "relation": relation, "inputs": sorted(operands)}
        if bench.rules["rejected_surfaces"]([annotated]):
            return "FAIL", "非出口厂商批发/终端零售缺同渠道同期配对桥，无法推库存。"
        return "NEEDS_REVIEW", "核验同渠道的期初期末、实际入流与出流。"
    if relation == "wholesale_partition":
        if "retail" in claim.get("operands", []):
            return "FAIL", "终端零售不属于厂商批发构成。"
        if not set(basis) <= available.keys() or set(claim.get("operands", [])) != {"export", "wholesale"}:
            return "NEEDS_REVIEW", "未能绑定已登记的出口/厂商批发关系。"
        window = claim["window"]
        if window not in {"month", "ytd"}:
            return "FAIL", "批发分解期间不在冻结事实表。"
        tail = ":ytd" if window == "ytd" else ""
        required = {f"cpca:{year}-09:{kind}{tail}" for year in (2025, 2026) for kind in ("export", "wholesale")}
        if not required <= set(basis):
            return "FAIL", "出口分解需要同窗两年的批发和出口读数。"
        result = old.decomposition(bench, window, claim["values"], "结构化标注", "固定事实重算")
        return result["status"], result["finding"]
    if relation == "parallel_observation":
        return "PASS", "同窗的两个统计总体独立列示，关系允许。"
    if relation == "independent_corroboration":
        groups = claim.get("source_groups", [])
        if len(groups) != 2 or not all(group for group in groups):
            return "NEEDS_REVIEW", "需要指出两组独立证据的血缘。"
        sources = []
        for group in groups:
            if not set(group) <= set(basis):
                return "FAIL", "血缘组有未登记证据。"
            rows = [evidence[key] for key in group]
            ids = {row.get("source_id") for row in rows}
            if None in ids:
                return "NEEDS_REVIEW", "派生血缘不完整。"
            sources.append(ids)
        if sources[0] & sources[1]:
            return "FAIL", "作为独立印证的两组事实共享来源。"
        return "NEEDS_REVIEW", "来源 ID 不重叠，仍需核查数据是否独立及支持力度。"
    if relation == "observation":
        if len(basis) != 1 or basis[0] not in available:
            return "NEEDS_REVIEW", "需绑定一条已登记的事实表记录。"
        row = available[basis[0]]
        period = row.get("stat_period", row.get("period"))
        window = row.get("period_type", "ytd" if "/" in period else "month")
        scope = row.get("stat_scope", row.get("series"))
        field = claim.get("field", "value")
        if field not in {"value", "yoy_pct"} or field not in row or row[field] == "":
            return "FAIL", "事实表没有声明的字段。"
        unit = "%" if field == "yoy_pct" else row["unit"]
        expected = (period, window, scope, unit, Decimal(row[field]))
        actual = (claim["period"], claim["window"], claim["scope"], claim["unit"], Decimal(str(claim["value"])))
        if actual != expected:
            return "FAIL", f"期间、窗口、口径、单位或值不匹配。登记为 {expected}，主张为 {actual}。"
        return "PASS", "期间、口径、单位和读数均绑定冻结事实。"
    if relation == "causal" and not claim.get("causal_basis"):
        return "FAIL", "结构化强因果无机制或识别依据。"
    return "NEEDS_REVIEW", "当前规则没有证明该支持关系，交独立评审。"


def structured_checks(bench, task, packet, claims):
    out = []
    for index, claim in enumerate(claims, 1):
        location = f"{packet['case']} claim {index}"
        try:
            if not isinstance(claim["basis"], list) or any(not isinstance(key, str) for key in claim["basis"]):
                raise ValueError("basis 必须为证据 ID 列表")
            if sum(key in claim for key in ("line", "page")) != 1 or not anchored(claim["quote"], anchor_text(packet, claim)):
                status, finding = "FAIL", "主张未绑定实际正文或 PDF。"
            else:
                location += f" · {'页 ' + str(claim['page']) if 'page' in claim else '行 ' + str(claim['line'])}"
                status, finding = relation_check(bench, task, claim, packet["evidence"])
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            status, finding = "FAIL", f"结构化声明无效：{exc}"
        out.append(check("claim.support", status, finding, location, "重核事实原件和支持关系。"))
    return out or [check("claim.coverage", "NEEDS_REVIEW", "没有结构化主张，不能将覆盖视为通过。",
                         packet["case"], "提交标注并进行语义评读。")]


def verify_package(bench, task, folder, manifest, packet, claims, charts=()):
    out = structured_checks(bench, task, packet, claims)
    artifact_pins = manifest.get("artifacts", {})
    if set(artifact_pins) - ARTIFACTS or not {"report.pdf", "candidate.md"} <= set(artifact_pins):
        out.append(check("artifact.manifest", "FAIL", "需声明 Markdown/PDF，DOCX 若交付也必须列明。",
                         "manifest", "提供固定文件名与 SHA-256。"))
    for name in sorted(set(artifact_pins) & ARTIFACTS):
        path = folder / name
        good = path.is_file() and not path.is_symlink() and old.digest(path) == artifact_pins[name]
        if good:
            try:
                good = any(text.strip() for text in old.extract(path))
            except Exception as exc:
                out.append(check("artifact.parse", "FAIL", str(exc), name, "重新导出并检查工件。"))
                continue
        out.append(check("artifact.identity", "PASS" if good else "FAIL", "核对冻结字节与可读内容。",
                         name, "重新提交原样工件。"))

    if claims and packet["pdf_pages"]:
        pdf_text = re.sub(r"〔\d+〕", "", "".join(p["text"] for p in packet["pdf_pages"]))
        for claim in claims:
            if "line" in claim and isinstance(claim.get("quote"), str):
                quote = CITATIONS.sub("", claim["quote"])
                bound = anchored(quote, pdf_text)
                if not bound:
                    out.append(check("artifact.rendition", "NEEDS_REVIEW", "Markdown 主张未直接锚定交付 PDF。",
                                     f"正文第 {claim['line']} 行", "复核正文与导出的实质一致性。"))
    used = {key for row in packet["report_lines"] for key in CITATIONS.findall(row["text"])}
    visible = packet["evidence"]
    bad_ids = used - visible.keys()
    revoked = (bench.full.keys() - bench.market.keys()) if task == "C" else set()
    bad_ids |= used & revoked
    out.append(check("source.ids", "FAIL" if bad_ids else "PASS", f"扫描 {len(used)} 个显式引用，失效 {sorted(bad_ids)}。",
                     "候选正文", "为引用提供有效证据并清除撤销内容。"))
    late = []
    cutoff = date.fromisoformat(packet["cutoff"])
    for key in used & visible.keys():
        value = visible[key].get("public_available_at") or visible[key].get("published_at")
        if value and date.fromisoformat(value) > cutoff:
            late.append(key)
    if late:
        out.append(check("source.cutoff", "FAIL", f"资料超出截止日期：{sorted(late)}。",
                         "候选正文", "替换为截止日前已公开资料。"))
    if manifest.get("asserted_verdict") or manifest.get("release_authorization") or (
            manifest.get("mock") is True and manifest.get("author_kind") != "mock"):
        out.append(check("provenance", "FAIL", "投稿夹带验收声明或模型身份冲突。",
                         "manifest", "由验证者生成判定，按真实生成身份登记。"))
    elif manifest.get("author_kind") not in {"model", "human", "mock", "offline-edit"}:
        out.append(check("provenance", "NEEDS_REVIEW", "生成身份未登记。", "manifest", "记录实际生成方式。"))
    elif manifest.get("author_kind") == "model":
        out.append(check("provenance", "NEEDS_REVIEW", "作者声明由模型生成，待核对验收方可审计执行记录。",
                         "manifest", "附由可信执行环境绑定的生成凭据。"))
    if charts:
        result = subprocess.run(["pdfimages", "-list", str(folder / "report.pdf")], check=True,
                                capture_output=True, text=True, timeout=30)
        images = Counter(int(p[0]) for line in result.stdout.splitlines() if (p := line.split())
                         and p[0].isdigit() and len(p) > 2 and p[2] == "image")
        for chart in charts:
            page = chart["page"]
            caption = chart["caption"]
            keys = chart["evidence_ids"]
            anchor = anchored(caption, anchor_text(packet, {"page": page}))
            status = "FAIL" if not anchor or not keys or set(keys) - visible.keys() else (
                "PASS" if images[page] else "NEEDS_REVIEW")
            out.append(check("chart.binding", status, f"检查图注、证据 ID 与第 {page} 页图片，轴和值另做视觉复核。",
                             f"PDF p.{page}", "核对图表数据、图注、单位和导出。"))
    return out
