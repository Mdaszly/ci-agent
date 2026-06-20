"""召回率自动化测试脚本 - 使用 recall_dataset.json 执行完整测试并收集 Bad Case。

执行流程：
1. 加载 recall_dataset.json 测试数据集
2. 对 lexical_only / vector_only / hybrid 三种模式运行完整测试
3. 计算 recall@k / precision@k / mrr / ndcg@k / f1@k 等指标
4. 自动识别 Bad Case（recall=0 或 precision=0 的用例）
5. 输出中文报告到 reports/recall_report_<timestamp>.json
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# 设置测试环境变量
os.environ.setdefault("AUTH_JWT_SECRET", "test-secret-key-for-unit-tests-32chars")
os.environ.setdefault("DB_USE_SQLITE", "true")
os.environ.setdefault("EMBEDDING_API_KEY", "")  # 不配置 embedding，走词面降级

# 确保可以导入 app 模块
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.recall_tester import recall_tester

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

FIXTURE_PATH = BACKEND_DIR / "tests" / "fixtures" / "recall_dataset.json"
REPORT_DIR = BACKEND_DIR / "reports"


def load_dataset() -> dict:
    """加载测试数据集。"""
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


def run_recall_test() -> dict:
    """执行完整召回率测试。"""
    logger.info("加载测试数据集: %s", FIXTURE_PATH)
    fixture = load_dataset()

    dataset_id = fixture["dataset_id"]
    logger.info("数据集 ID: %s", dataset_id)
    logger.info("记忆条目数: %d", len(fixture["memory_items"]))
    logger.info("测试用例数: %d", len(fixture["test_cases"]))

    # 加载数据集到记忆系统
    logger.info("正在加载记忆数据到系统...")
    load_result = recall_tester.load_dataset(
        dataset_id=dataset_id,
        memory_items=fixture["memory_items"],
        test_cases=fixture["test_cases"],
        clear_existing=True,
        dataset_version=fixture.get("dataset_version", "v1"),
        baseline_config=fixture.get("baseline_config", {}),
        description=fixture.get("description", ""),
    )
    logger.info("数据集加载完成: %d 条记忆, %d 个测试用例", load_result["memory_count"], load_result["test_case_count"])

    # 执行三种模式的测试
    modes = ["lexical_only", "vector_only", "hybrid"]
    top_k = fixture.get("baseline_config", {}).get("top_k", 5)

    logger.info("开始执行召回率测试 (modes=%s, top_k=%d)...", modes, top_k)
    result = recall_tester.run_test(
        dataset_id=dataset_id,
        modes=modes,
        top_k=top_k,
        detailed=True,
    )

    logger.info("测试完成, 耗时 %d ms", result["duration_ms"])
    return result


def collect_bad_cases(result: dict) -> list[dict]:
    """从测试结果中识别 Bad Case。

    Bad Case 判定标准：
    - recall@k = 0：完全未召回任何期望结果（漏召回）
    - precision@k = 0 且 expected_ids 非空：召回结果全部不相关（误召回）
    - negative_case 类别中 recall > 0：负例测试召回了无关结果（过度召回）
    """
    bad_cases: list[dict] = []
    top_k = result["config"]["top_k"]

    for detail in result.get("details", []):
        case_id = detail.get("case_id", "")
        query = detail.get("query", "")
        expected_ids = detail.get("expected_ids", [])
        category = detail.get("category", "unknown")

        for mode, mode_result in detail.get("results", {}).items():
            metrics = mode_result.get("metrics", {})
            raw_metrics = mode_result.get("raw_metrics", {})
            recall = raw_metrics.get(f"recall@{top_k}", 0.0)
            precision = raw_metrics.get(f"precision@{top_k}", 0.0)
            retrieved = mode_result.get("retrieved", [])

            is_bad = False
            bad_type = ""
            severity = ""
            description = ""

            if category == "negative_case":
                # 负例测试：不应召回任何结果
                if retrieved:
                    is_bad = True
                    bad_type = "over_recall"
                    severity = "medium"
                    description = f"负例测试召回了 {len(retrieved)} 条无关结果"
            elif expected_ids:
                # 正例测试：应召回期望结果
                if recall == 0.0:
                    is_bad = True
                    bad_type = "recall_failure"
                    severity = "high"
                    description = f"完全未召回期望结果 (expected={expected_ids})"
                elif precision == 0.0:
                    is_bad = True
                    bad_type = "false_positive"
                    severity = "medium"
                    description = f"召回结果全部不相关 (retrieved={[r['id'] for r in retrieved]})"

            if is_bad:
                bad_cases.append({
                    "case_id": case_id,
                    "mode": mode,
                    "category": category,
                    "query": query,
                    "expected_ids": expected_ids,
                    "retrieved_ids": [r["id"] for r in retrieved],
                    "retrieved_scores": [r["score"] for r in retrieved],
                    "bad_type": bad_type,
                    "severity": severity,
                    "description": description,
                    "metrics": {
                        "recall_at_k": recall,
                        "precision_at_k": precision,
                        "mrr": raw_metrics.get("mrr", 0.0),
                        "ndcg_at_k": raw_metrics.get(f"ndcg@{top_k}", 0.0),
                        "f1_at_k": raw_metrics.get(f"f1@{top_k}", 0.0),
                    },
                })

    return bad_cases


def generate_report(result: dict, bad_cases: list[dict]) -> dict:
    """生成中文测试报告。"""
    top_k = result["config"]["top_k"]
    summary = result.get("summary", {})

    # 汇总各模式指标
    mode_reports = {}
    for mode, metrics in summary.items():
        mode_reports[mode] = {
            "recall_at_k": round(metrics.get(f"recall@{top_k}", 0.0), 4),
            "precision_at_k": round(metrics.get(f"precision@{top_k}", 0.0), 4),
            "mrr": round(metrics.get("mrr", 0.0), 4),
            "ndcg_at_k": round(metrics.get(f"ndcg@{top_k}", 0.0), 4),
            "f1_at_k": round(metrics.get(f"f1@{top_k}", 0.0), 4),
            "avg_latency_ms": round(metrics.get("avg_latency_ms", 0.0), 2),
        }
        if mode == "hybrid":
            mode_reports[mode]["degraded_rate"] = metrics.get("degraded_rate", 0.0)
            mode_reports[mode]["vector_presence_rate"] = metrics.get("vector_presence_rate", 0.0)

    # 按类别汇总
    by_category = result.get("by_category", {})
    category_reports = {}
    for cat, mode_metrics in by_category.items():
        category_reports[cat] = {}
        for mode, metrics in mode_metrics.items():
            category_reports[cat][mode] = {
                "recall_at_k": round(metrics.get(f"recall@{top_k}", 0.0), 4),
                "precision_at_k": round(metrics.get(f"precision@{top_k}", 0.0), 4),
                "mrr": round(metrics.get("mrr", 0.0), 4),
                "ndcg_at_k": round(metrics.get(f"ndcg@{top_k}", 0.0), 4),
            }

    # Bad Case 统计
    bad_case_stats = {
        "total": len(bad_cases),
        "by_type": {},
        "by_severity": {},
        "by_mode": {},
    }
    for bc in bad_cases:
        bad_case_stats["by_type"][bc["bad_type"]] = bad_case_stats["by_type"].get(bc["bad_type"], 0) + 1
        bad_case_stats["by_severity"][bc["severity"]] = bad_case_stats["by_severity"].get(bc["severity"], 0) + 1
        bad_case_stats["by_mode"][bc["mode"]] = bad_case_stats["by_mode"].get(bc["mode"], 0) + 1

    report = {
        "report_id": f"recall_report_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "test_id": result["test_id"],
        "dataset_id": result["dataset_id"],
        "config": {
            "modes": result["config"]["modes"],
            "top_k": top_k,
            "total_cases": result["config"]["total_cases"],
            "fusion_strategy": result["config"]["fusion_strategy"],
            "vector_weight": result["config"]["vector_weight"],
            "lexical_weight": result["config"]["lexical_weight"],
        },
        "summary_by_mode": mode_reports,
        "summary_by_category": category_reports,
        "bad_case_stats": bad_case_stats,
        "bad_cases": bad_cases,
        "duration_ms": result["duration_ms"],
    }
    return report


def print_console_summary(report: dict) -> None:
    """在控制台打印中文摘要。"""
    print("\n" + "=" * 80)
    print("                        召回率测试报告")
    print("=" * 80)
    print(f"测试 ID: {report['test_id']}")
    print(f"数据集: {report['dataset_id']}")
    print(f"测试用例数: {report['config']['total_cases']}")
    print(f"top_k: {report['config']['top_k']}")
    print(f"耗时: {report['duration_ms']} ms")
    print()

    print("-" * 80)
    print("各模式汇总指标:")
    print("-" * 80)
    print(f"{'模式':<15} {'Recall@k':<12} {'Precision@k':<14} {'MRR':<10} {'NDCG@k':<12} {'F1@k':<10} {'延迟(ms)':<10}")
    for mode, metrics in report["summary_by_mode"].items():
        print(f"{mode:<15} {metrics['recall_at_k']:<12.4f} {metrics['precision_at_k']:<14.4f} "
              f"{metrics['mrr']:<10.4f} {metrics['ndcg_at_k']:<12.4f} {metrics['f1_at_k']:<10.4f} "
              f"{metrics['avg_latency_ms']:<10.2f}")
    print()

    print("-" * 80)
    print("按类别汇总 (Recall@k):")
    print("-" * 80)
    categories = report["summary_by_category"]
    if categories:
        modes = list(next(iter(categories.values())).keys())
        header = f"{'类别':<25}" + "".join(f"{m:<18}" for m in modes)
        print(header)
        for cat, mode_metrics in categories.items():
            row = f"{cat:<25}"
            for mode in modes:
                val = mode_metrics.get(mode, {}).get("recall_at_k", 0.0)
                row += f"{val:<18.4f}"
            print(row)
    print()

    print("-" * 80)
    print("Bad Case 统计:")
    print("-" * 80)
    stats = report["bad_case_stats"]
    print(f"总数: {stats['total']}")
    print(f"按类型: {stats['by_type']}")
    print(f"按严重度: {stats['by_severity']}")
    print(f"按模式: {stats['by_mode']}")
    print()

    if report["bad_cases"]:
        print("-" * 80)
        print("Bad Case 详情:")
        print("-" * 80)
        for bc in report["bad_cases"]:
            print(f"  [{bc['severity'].upper()}] {bc['case_id']} ({bc['mode']}/{bc['category']})")
            print(f"    查询: {bc['query']}")
            print(f"    类型: {bc['bad_type']}")
            print(f"    描述: {bc['description']}")
            print(f"    期望: {bc['expected_ids']}")
            print(f"    实际: {bc['retrieved_ids']}")
            print(f"    指标: recall={bc['metrics']['recall_at_k']:.4f}, "
                  f"precision={bc['metrics']['precision_at_k']:.4f}, "
                  f"mrr={bc['metrics']['mrr']:.4f}")
            print()

    print("=" * 80)


def main() -> int:
    """主入口。"""
    logger.info("=== 召回率自动化测试开始 ===")

    # 执行测试
    result = run_recall_test()

    # 收集 Bad Case
    logger.info("正在识别 Bad Case...")
    bad_cases = collect_bad_cases(result)
    logger.info("识别到 %d 个 Bad Case", len(bad_cases))

    # 生成报告
    report = generate_report(result, bad_cases)

    # 打印控制台摘要
    print_console_summary(report)

    # 保存报告到文件
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"{report['report_id']}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    logger.info("报告已保存到: %s", report_path)

    logger.info("=== 召回率自动化测试完成 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
