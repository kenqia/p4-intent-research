"""Trusted PR gate. Reads four Git blobs as data. Never runs a PR's code or tests."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

from cases import HERE, BENCH, Benchmark, digest, read_json, read_packet, RULES, AB_CRITERIA
from hard import ARTIFACTS, anchored, verify_package
from judges import validate_case, validate_ab, ROW_FORMAT
from provider import Reviewer

ROOT = HERE.parent
SUBMISSION = ARTIFACTS | {"manifest.json"}
HASH = re.compile(r"[a-f0-9]{40}")


def require(condition, detail):
    if not condition:
        raise ValueError(detail)


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args], timeout=45)


def classify(paths, author, trusted):
    if not paths:
        raise ValueError("PR 无变更。")
    if all(p.startswith("product/") or p.startswith("submission/") for p in paths):
        return "experiment"
    if (author in trusted and not any(p.startswith(("product/", "submission/")) for p in paths)
            and all(p.startswith(("gate/", ".github/", "best/")) or p in {"README.md", ".gitignore"} for p in paths)):
        return "owner-maintenance"
    raise ValueError("实验 PR 不得改动验收规则、工作流、冻结测试或最佳版本。维护改动必须另开 PR。")


def materialize(head, target):
    target.mkdir()
    current = git("ls-tree", "-z", head, "submission/").decode().split("\0")
    for item in current:
        if item:
            info, name = item.split("\t")
            require(info.startswith("100644 blob ") and name.removeprefix("submission/") in SUBMISSION,
                    "submission/ 只接受报告三件套及 manifest.json。")
    for name in sorted(SUBMISSION):
        ref = f"{head}:submission/{name}"
        length = int(git("cat-file", "-s", ref))
        require(0 < length <= (10_000_000 if name != "manifest.json" else 120_000), f"投稿体积不合法：{name}")
        (target / name).write_bytes(git("show", ref))


def write(out, name, value):
    (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def quality_gate(candidate, ab, calibrated, hard):
    if not hard or any(c["status"] != "PASS" for c in hard):
        return False, "硬约束存在 FAIL 或待核项。"
    if not calibrated:
        return False, "评审校准未达标。"
    if any(value["status"] != "OK" for value in candidate.values()) or set(candidate) != set(RULES):
        return False, "至少一项评审缺失或锚点无效。"
    if any(x["verdict"] != "PASS" for x in candidate["evidence"]["criteria"].values()):
        return False, "L1 事实与推理均需通过。"
    if any(x["verdict"] in {"FAIL", "UNSURE"} or
           any(f["severity"] == "CRITICAL" for f in x["findings"])
           for r in ("reader", "visual") for x in candidate[r]["criteria"].values()):
        return False, "读者质量或视觉仍有关键问题。"
    if not ab or any(a["preference"] == "UNSURE" for a in ab):
        return False, "匿名对照缺失或不确定。"
    for order, record in enumerate(ab):
        candidate_label, baseline_label = ("B", "A") if order == 0 else ("A", "B")
        if record["preference"] != candidate_label or any(
                row["better"] in {baseline_label, "UNSURE"} for row in record["findings"]):
            return False, "A/B 对调存在关键退步、分歧或缺少足够增益。"
    return True, "通过实验内容与同条件对照门槛，最佳版本仍由维护者单独更新。"


def validate_group(raw, packets, role):
    return {p["case"]: validate_case(p, raw.get("cases", {}).get(p["case"]), role,
              raw.get("viewed_pages", {}).get(p["case"])) for p in packets}


def calibration(provider, fixtures, output):
    reference = fixtures["reference"]
    packets = fixtures["packets"]
    reviews = {}
    for batch in (fixtures["short_cases"], fixtures["evidence_full"]):
        scope = [packets[key] for key in batch]
        result = provider.role("evidence", scope)
        reviews.setdefault("evidence", {}).update(validate_group(result, scope, "evidence"))
    scope = [packets[key] for key in fixtures["reader"]]
    reviews["reader"] = validate_group(provider.role("reader", scope), scope, "reader")
    checks = []
    for case, role, criterion, verdict, triggers in reference:
        result = reviews[role][case]
        current = result.get("criteria", {}).get(criterion, {})
        detected = all(any(anchored(needle, f["quote"]) or anchored(f["quote"], needle)
                       for f in current.get("findings", []) if f["severity"] in {"MAJOR", "CRITICAL"})
                       for needle in triggers)
        checks.append({"case":case, "role":role, "criterion":criterion, "expected":verdict,
                       "observed":current.get("verdict"), "anchors": detected,
                       "pass":result["status"] == "OK" and current.get("verdict") in verdict and detected})
    write(output, "calibration.json", {"checks":checks, "reviews":reviews})
    return all(c["pass"] for c in checks)


def pair(provider, best, candidate, output):
    answers = []
    for idx, order in enumerate(((best,candidate),(candidate,best))):
        folder = output / f"pair-{idx}"
        folder.mkdir()
        anonymized = {}
        for label, report in zip(("A","B"), order):
            p = dict(report, case=label)
            p["pdf_pages"] = [{"page":page["page"], "text":"\n".join(t for t in page["text"].splitlines()
                                  if not any(word in t for word in ("Stage 05", "离线改稿", "匿名评读")))}
                                  for page in report["pdf_pages"]]
            anonymized[label] = p
            write(folder, label + ".json", p)
        response = provider.ask(
            f"Compare these two anonymous Chinese reports in order A/B for {AB_CRITERIA}. "
            "Use exact page/line quotes and concrete grounds for BOTH reports. "
            "Return object with preference A|B|TIE|UNSURE and findings rows with criterion, better, and A and B findings. "
            f"Finding format is the entry inside this criterion row: {ROW_FORMAT}", anonymized)
        answers.append(validate_ab(response, folder))
    return answers


def feedback(output, message, reviews=None):
    lines = ["# P4 评审反馈", "", message, ""]
    for role, result in (reviews or {}).items():
        for name,row in result.get("criteria",{}).items():
            lines += [f"## {role} · {name} · {row['verdict']}", ""]
            for f in row["findings"]:
                loc = "页 " + str(f["page"]) if "page" in f else "行 " + str(f["line"])
                lines.append(f"- {loc}「{f['quote']}」。{f['reason']} {f['improvement']}")
            lines.append("")
    (output / "feedback.md").write_text("\n".join(lines)+"\n", encoding="utf-8")


def assess(base, head, author, fork, output):
    output.mkdir()
    config = read_json(HERE / "config.json")
    require(HASH.fullmatch(base) and HASH.fullmatch(head), "invalid PR OID")
    require(git("rev-parse", "HEAD").decode().strip() == base, "runner checkout must be trusted event base")
    require(git("merge-base", base, head).decode().strip() == base, "请先基于最新 main 更新 PR。")
    changed = git("diff", "--no-renames", "--name-only", "-z", base, head).decode().strip("\0").split("\0")
    lane = classify(changed, author, config["maintainers"])
    write(output, "scope.json", {"base":base, "head":head, "paths":changed, "lane":lane})
    if lane == "owner-maintenance":
        feedback(output, "仅规则维护。该检查通过不晋级产品，仍需 CODEOWNERS 审核。")
        return True
    bench = Benchmark()
    facts = subprocess.run([sys.executable,"-B","-m","pytest","-q","-p","no:cacheprovider",str(BENCH)],
                           timeout=50, capture_output=True, text=True)
    (output / "benchmark.txt").write_text(facts.stdout + facts.stderr, encoding="utf-8")
    require(facts.returncode == 0, "冻结事实基准失败。")
    target = output / "submission"
    materialize(head, target)
    manifest = read_json(target / "manifest.json")
    allowed = {"author_kind", "environment", "artifacts", "input_sha256", "claims", "charts"}
    require(set(manifest) <= allowed and set(manifest["artifacts"]) == ARTIFACTS, "投稿身份字段无效或夹带通过声明。")
    profile = read_json(HERE / "task.json")
    require(manifest["input_sha256"] == digest(HERE / "task.json"), "基线与候选任务/冻结资料不一致。")
    best_manifest = read_json(ROOT / "best/best.json")
    require(best_manifest["state"] == "VERIFIED_REFERENCE"
            and best_manifest["input_sha256"] == manifest["input_sha256"]
            and best_manifest["environment"] == manifest["environment"], "缺少同条件最佳参考。")
    best_dir = output / "baseline"
    best_dir.mkdir()
    for name, expected in best_manifest["artifacts"].items():
        require(name in ARTIFACTS and digest(ROOT / "best" / name) == expected, "最佳版本字节被替换。")
        (best_dir / name).write_bytes((ROOT / "best" / name).read_bytes())
    candidate = read_packet(target, "candidate", profile)
    best = read_packet(best_dir, "best", profile)
    hard = verify_package(bench, profile["task"], target, manifest, candidate, manifest["claims"], manifest.get("charts", []))
    write(output, "hard-checks.json", hard)
    for c in hard:
        require(c["status"] == "PASS", f"{c['location']} · {c['finding']} {c['next_step']}")
    if config["require_fork"]:
        require(fork, "作者须从独立 fork 提交，以防同库工作流伪造状态。")
    provider = Reviewer()
    try:
        fixtures = read_json(HERE / "calibration/cases.json")
        calibrated = calibration(provider, fixtures, output)
        visual_p = dict(best, case="best")
        images = [target / p["image"] for p in candidate["pdf_pages"]] + [best_dir / p["image"] for p in best["pdf_pages"]]
        reviews = {role: validate_group(provider.role(role, [candidate]), [candidate], role)["candidate"]
                   for role in ("evidence", "reader")}
        visual = validate_group(provider.role("visual", [candidate, visual_p], images), [candidate, visual_p], "visual")
        reviews["visual"] = visual["candidate"]
        baseline_visual = visual["best"]
        calibrated &= baseline_visual["status"] == "OK" and (
            baseline_visual["criteria"]["chart_argument"]["verdict"] in {"PASS", "PARTIAL"})
        write(output, "judge-review.json", reviews)
        ab = pair(provider, best, candidate, output)
        write(output, "comparison.json", ab)
        ok, detail = quality_gate(reviews, ab, calibrated, hard)
        feedback(output, detail, reviews)
        return ok
    finally:
        write(output, "model-executions.json", provider.receipts)


def fail(out, message):
    if not out.exists():
        out.mkdir(parents=True)
    if not (out / "feedback.md").exists():
        feedback(out, message)
    print("P4 gate blocked; see feedback artifact", file=sys.stderr)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    for arg in ("base","head","author"):
        p.add_argument("--"+arg, required=True)
    p.add_argument("--fork", choices=("true","false"), required=True)
    p.add_argument("--out", type=Path, required=True)
    opts = p.parse_args()
    try:
        ok = assess(opts.base, opts.head, opts.author, opts.fork == "true", opts.out)
    except Exception as exc:
        fail(opts.out, str(exc))
        raise SystemExit(4)
    raise SystemExit(0 if ok else 3)
