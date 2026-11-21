"""Hosted verification of the evaluator using the public frozen reference and controls."""
import argparse
from pathlib import Path
import shutil
import sys

from cases import Benchmark, HERE, digest, read_json, read_packet
from hard import verify_package
from pr_gate import ROOT, calibration, feedback, pair, quality_gate, require, validate_group, write
from provider import Reviewer


def run(out):
    out.mkdir()
    provider = Reviewer()
    profile = read_json(HERE / "task.json")
    frozen = read_json(ROOT / "best/best.json")
    require(frozen["input_sha256"] == digest(HERE / "task.json"), "冻结资料身份改变")
    reference = out / "reference"
    reference.mkdir()
    for name, expected in frozen["artifacts"].items():
        require(digest(ROOT / "best" / name) == expected, f"冻结报告改变：{name}")
        shutil.copyfile(ROOT / "best" / name, reference / name)
    report = read_packet(reference, "reference", profile)
    basis = [f"cpca:{year}-09:{kind}:ytd" for year in (2025, 2026) for kind in ("wholesale", "export")]
    claim = {"page": 1, "quote": "出口是批发的一部分，零售另计", "basis": basis,
             "operands": ["export", "wholesale"], "window": "ytd",
             "relation": "wholesale_partition", "values": ["100.4", "226.0", "-125.6"]}
    hard = verify_package(Benchmark(), profile["task"], reference, dict(frozen, author_kind="human"),
                          report, [claim], [{"page": 1, "caption": claim["quote"], "evidence_ids": basis}])
    write(out, "hard-checks.json", hard)
    require(all(c["status"] == "PASS" for c in hard), f"冻结参考硬检查失败：{hard}")

    try:
        alive = provider.ask("Return JSON with the boolean key alive set to true.",
                             {"purpose": "minimal hosted Judge preflight"})
        require(alive.get("alive") is True, "模型预检未返回预期结果")
        fixtures = read_json(HERE / "calibration/cases.json")
        calibrated = calibration(provider, fixtures, out)
        visual_files = [reference / page["image"] for page in report["pdf_pages"]]
        reviews = {}
        for role in ("evidence", "reader", "visual"):
            images = visual_files if role == "visual" else ()
            response = provider.role(role, [report], images)
            reviews[role] = validate_group(response, [report], role)["reference"]
        write(out, "judge-review.json", reviews)
        ab = pair(provider, report, report, out)
        write(out, "comparison.json", ab)
        gate_ok, reason = quality_gate(reviews, ab, calibrated, hard)
        complete = len(provider.receipts) == 9 and all(
            receipt["model"] == "gpt-6.2-sol" and receipt["reported_effort"] == "medium"
            for receipt in provider.receipts)
        write(out, "decision.json", {
            "execution": "OK" if complete else "UNVERIFIED_MODEL",
            "mode": "MAINTAINER_REFERENCE_REPLAY",
            "artifact_identity": "A and B contain the identical frozen reference",
            "calibration_passed": calibrated, "promotion": "HOLD",
            "promotion_reason": "同一参考稿与自身比较是无增益对照。",
            "gate_rule_result": bool(gate_ok and complete), "gate_detail": reason,
            "phase_one_required_status_written": False,
        })
        feedback(out, "GPT‑6.2 Sol 托管参考稿复跑。A/B 是同一稿，晋级保留 HOLD。", reviews)
        return 0 if complete else 4
    finally:
        write(out, "model-executions.json", provider.receipts)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        status = run(args.out)
    except Exception as exc:
        args.out.mkdir(exist_ok=True)
        write(args.out, "error.json", {"execution": "ERROR", "reason": str(exc)})
        print(f"Judge rehearsal failed: {type(exc).__name__}", file=sys.stderr)
        status = 4
    raise SystemExit(status)
