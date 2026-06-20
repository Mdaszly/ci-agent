"""召回率测试 - 启用相似度阈值过滤，验证 Bad Case 修复效果。

对比基线测试（threshold=0.0）和阈值过滤测试（threshold=0.3），
展示 min_similarity_threshold 如何解决负例过度召回问题。
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("AUTH_JWT_SECRET", "test-secret-key-for-unit-tests-32chars")
os.environ.setdefault("DB_USE_SQLITE", "true")
os.environ.setdefault("EMBEDDING_API_KEY", "")

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import memory_settings
from app.services.recall_tester import recall_tester

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

FIXTURE_PATH = BACKEND_DIR / "tests" / "fixtures" / "recall_dataset.json"
REPORT_DIR = BACKEND_DIR / "reports"


def load_dataset() -> dict:
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


def run_test_with_threshold(threshold: float) -> dict:
    """使用指定相似度阈值运行测试。"""
    fixture = load_dataset()
    dataset_id = fixture["dataset_id"]

    # 重置数据集
    recall_tester._datasets.clear()
    recall_tester._history.clear()

    recall_tester.load_dataset(
        dataset_id=dataset_id,
        memory_items=fixture["memory_items"],
        test_cases=fixture["test_cases"],
        clear_existing=True,
        dataset_version=fixture.get("dataset_version", "v1"),
        baseline_config=fixture.get("baseline_config", {}),
        description=fixture.get("description", ""),
    )

    # 临时设置阈值
    original_threshold = memory_settings.min_similarity_threshold
    memory_settings.min_similarity_threshold = threshold

    try:
        result = recall_tester.run_test(
            dataset_id=dataset_id,
            modes=["lexical_only", "vector_only", "hybrid"],
            top_k=5,
            detailed=True,
        )
    finally:
        memory_settings.min_similarity_threshold = original_threshold

    return result


def collect_bad_cases(result: dict) -> list[dict]:
    """识别 Bad Case。"""
    bad_cases = []
    top_k = result["config"]["top_k"]

    for detail in result.get("details", []):
        case_id = detail.get("case_id", "")
        query = detail.get("query", "")
        expected_ids = detail.get("expected_ids", [])
        category = detail.get("category", "unknown")

        for mode, mode_result in detail.get("results", {}).items():
            raw_metrics = mode_result.get("raw_metrics", {})
            recall = raw_metrics.get(f"recall@{top_k}", 0.0)
            precision = raw_metrics.get(f"precision@{top_k}", 0.0)
            retrieved = mode_result.get("retrieved", [])

            is_bad = False
            bad_type = ""
            severity = ""
            description = ""

            if category == "negative_case":
                if retrieved:
                    is_bad = True
                    bad_type = "over_recall"
                    severity = "medium"
                    description = f"负例测试召回了 {len(retrieved)} 条无关结果"
            elif expected_ids:
                if recall == 0.0:
                    is_bad = True
                    bad_type = "recall_failure"
                    severity = "high"
                    description = f"完全未召回期望结果 (expected={expected_ids})"
                elif precision == 0.0:
                    is_bad = True
                    bad_type = "false_positive"
                    severity = "medium"
                    description = f"召回结果全部不相关"

            if is_bad:
                bad_cases.append({
                    "case_id": case_id,
                    "mode": mode,
                    "category": category,
                    "query": query,
                    "bad_type": bad_type,
                    "severity": severity,
                    "description": description,
                    "retrieved_count": len(retrieved),
                    "metrics": {
                        "recall_at_k": recall,
                        "precision_at_k": precision,
                    },
                })

    return bad_cases


def main() -> int:
    logger.info("=== 阈值过滤对比测试开始 ===")

    # 测试 1: 基线（无阈值）
    logger.info("运行基线测试 (threshold=0.0)...")
    baseline_result = run_test_with_threshold(0.0)
    baseline_bad = collect_bad_cases(baseline_result)

    # 测试 2: 启用阈值过滤
    logger.info("运行阈值过滤测试 (threshold=0.3)...")
    threshold_result = run_test_with_threshold(0.3)
    threshold_bad = collect_bad_cases(threshold_result)

    # 对比报告
    top_k = 5
    print("\n" + "=" * 80)
    print("              相似度阈值过滤对比测试报告")
    print("=" * 80)

    print("\n--- 基线 (threshold=0.0) ---")
    for mode, metrics in baseline_result["summary"].items():
        print(f"  {mode:<15} Recall@{top_k}={metrics.get(f'recall@{top_k}', 0):.4f}  "
              f"NDCG@{top_k}={metrics.get(f'ndcg@{top_k}', 0):.4f}  "
              f"MRR={metrics.get('mrr', 0):.4f}")
    print(f"  Bad Cases: {len(baseline_bad)}")

    print("\n--- 阈值过滤 (threshold=0.3) ---")
    for mode, metrics in threshold_result["summary"].items():
        print(f"  {mode:<15} Recall@{top_k}={metrics.get(f'recall@{top_k}', 0):.4f}  "
              f"NDCG@{top_k}={metrics.get(f'ndcg@{top_k}', 0):.4f}  "
              f"MRR={metrics.get('mrr', 0):.4f}")
    print(f"  Bad Cases: {len(threshold_bad)}")

    # 改善情况
    print("\n--- 改善情况 ---")
    for mode in ["lexical_only", "vector_only", "hybrid"]:
        base_metrics = baseline_result["summary"].get(mode, {})
        thresh_metrics = threshold_result["summary"].get(mode, {})
        base_recall = base_metrics.get(f"recall@{top_k}", 0)
        thresh_recall = thresh_metrics.get(f"recall@{top_k}", 0)
        base_ndcg = base_metrics.get(f"ndcg@{top_k}", 0)
        thresh_ndcg = thresh_metrics.get(f"ndcg@{top_k}", 0)
        print(f"  {mode:<15} Recall: {base_recall:.4f} -> {thresh_recall:.4f} (delta={thresh_recall-base_recall:+.4f})  "
              f"NDCG: {base_ndcg:.4f} -> {thresh_ndcg:.4f} (delta={thresh_ndcg-base_ndcg:+.4f})")

    print(f"\n  Bad Case 减少: {len(baseline_bad)} -> {len(threshold_bad)} (减少 {len(baseline_bad)-len(threshold_bad)})")

    # 保存对比报告
    report = {
        "report_id": f"threshold_comparison_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "baseline": {
            "threshold": 0.0,
            "summary": {mode: {k: v for k, v in metrics.items() if k.startswith(("recall", "precision", "mrr", "ndcg", "f1"))}
                        for mode, metrics in baseline_result["summary"].items()},
            "bad_case_count": len(baseline_bad),
            "bad_cases": baseline_bad,
        },
        "with_threshold": {
            "threshold": 0.3,
            "summary": {mode: {k: v for k, v in metrics.items() if k.startswith(("recall", "precision", "mrr", "ndcg", "f1"))}
                        for mode, metrics in threshold_result["summary"].items()},
            "bad_case_count": len(threshold_bad),
            "bad_cases": threshold_bad,
        },
    }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"{report['report_id']}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    logger.info("对比报告已保存到: %s", report_path)

    print("\n" + "=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
