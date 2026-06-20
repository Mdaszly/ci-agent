from pytest import raises

from app.models.schemas import (
    AnalysisProfile,
    AnalysisStrategy,
    EvidenceDimension,
    TaskCreateRequest,
)


def test_analysis_profile_custom_weights_normalize():
    """测试自定义权重归一化（支持百分比输入）"""
    profile = AnalysisProfile(
        strategy=AnalysisStrategy.custom,
        dimension_weights={
            "feature": 50,
            "pricing": 50,
            "user_feedback": 0,
            "positioning": 0,
            "risk": 0,
        },
    )
    weights = profile.resolved_weights()
    assert abs(sum(weights.values()) - 1.0) < 0.01
    assert weights["feature"] == weights["pricing"] == 0.5


def test_analysis_profile_percentage_input():
    """测试百分比输入自动转换"""
    profile = AnalysisProfile(
        strategy=AnalysisStrategy.custom,
        dimension_weights={
            "feature": 30,
            "pricing": 25,
            "user_feedback": 25,
            "positioning": 10,
            "risk": 10,
        },
    )
    weights = profile.resolved_weights()
    assert abs(sum(weights.values()) - 1.0) < 0.01
    assert weights["feature"] == 0.30
    assert weights["pricing"] == 0.25
    assert weights["user_feedback"] == 0.25
    assert weights["positioning"] == 0.10
    assert weights["risk"] == 0.10


def test_analysis_profile_decimal_input():
    """测试小数输入（0-1范围）仍然有效"""
    profile = AnalysisProfile(
        strategy=AnalysisStrategy.custom,
        dimension_weights={
            "feature": 0.30,
            "pricing": 0.25,
            "user_feedback": 0.25,
            "positioning": 0.10,
            "risk": 0.10,
        },
    )
    weights = profile.resolved_weights()
    assert abs(sum(weights.values()) - 1.0) < 0.01


def test_cost_leadership_mandatory_includes_pricing():
    """测试成本领先策略包含定价维度"""
    profile = AnalysisProfile(strategy=AnalysisStrategy.cost_leadership)
    assert EvidenceDimension.pricing in profile.mandatory_dimensions()


def test_performance_mandatory_includes_feature_and_feedback():
    """测试产品力优势策略包含产品特性和用户反馈维度"""
    profile = AnalysisProfile(strategy=AnalysisStrategy.performance)
    mandatory = profile.mandatory_dimensions()
    assert EvidenceDimension.feature in mandatory
    assert EvidenceDimension.user_feedback in mandatory


def test_hybrid_mandatory_includes_three_dimensions():
    """测试性价比导向策略包含三个维度"""
    profile = AnalysisProfile(strategy=AnalysisStrategy.hybrid)
    mandatory = profile.mandatory_dimensions()
    assert EvidenceDimension.feature in mandatory
    assert EvidenceDimension.pricing in mandatory
    assert EvidenceDimension.user_feedback in mandatory


def test_strategy_label():
    """测试策略标签获取"""
    profile = AnalysisProfile(strategy=AnalysisStrategy.cost_leadership)
    assert profile.strategy_label() == "定价优势"
    
    profile = AnalysisProfile(strategy=AnalysisStrategy.performance)
    assert profile.strategy_label() == "产品力优势"
    
    profile = AnalysisProfile(strategy=AnalysisStrategy.hybrid)
    assert profile.strategy_label() == "性价比导向"
    
    profile = AnalysisProfile(strategy=AnalysisStrategy.custom)
    assert profile.strategy_label() == "自定义权重"


def test_resolved_weights_uses_preset_for_non_custom():
    """测试非自定义策略使用预设权重"""
    profile = AnalysisProfile(strategy=AnalysisStrategy.cost_leadership)
    weights = profile.resolved_weights()
    assert weights["pricing"] == 0.40
    assert weights["feature"] == 0.20


def test_custom_weights_validation_all_zero():
    """测试自定义权重全为0时抛出异常"""
    with raises(ValueError, match="自定义权重总和必须大于 0"):
        AnalysisProfile(
            strategy=AnalysisStrategy.custom,
            dimension_weights={
                "feature": 0,
                "pricing": 0,
                "user_feedback": 0,
                "positioning": 0,
                "risk": 0,
            },
        )


def test_focus_attributes_normalize():
    """测试关注属性去重"""
    profile = AnalysisProfile(
        strategy=AnalysisStrategy.hybrid,
        focus_attributes=["风力", "噪音", "续航", "价格", "风力", "噪音", "便携"],
    )
    assert len(profile.focus_attributes) == 5
    assert "风力" in profile.focus_attributes
    assert "噪音" in profile.focus_attributes
    assert "续航" in profile.focus_attributes
    assert "价格" in profile.focus_attributes
    assert "便携" in profile.focus_attributes
    assert profile.focus_attributes == ["风力", "噪音", "续航", "价格", "便携"]


def test_custom_strategy_mandatory_based_on_weights():
    """测试自定义策略根据权重确定必达维度"""
    profile = AnalysisProfile(
        strategy=AnalysisStrategy.custom,
        dimension_weights={
            "feature": 0.5,
            "pricing": 0.3,
            "user_feedback": 0.1,
            "positioning": 0.05,
            "risk": 0.05,
        },
    )
    mandatory = profile.mandatory_dimensions()
    assert EvidenceDimension.feature in mandatory
    assert EvidenceDimension.pricing in mandatory
    assert EvidenceDimension.user_feedback in mandatory


def test_task_create_request_with_analysis_profile():
    """测试任务创建请求包含分析配置"""
    request = TaskCreateRequest(
        product_goal="测试产品目标足够长以满足最小长度要求",
        competitors=["竞品A"],
        analysis_profile=AnalysisProfile(
            strategy=AnalysisStrategy.performance,
            focus_attributes=["风力", "噪音"],
        ),
    )
    assert request.analysis_profile.strategy == AnalysisStrategy.performance
    assert request.analysis_profile.focus_attributes == ["风力", "噪音"]


def test_task_create_request_default_analysis_profile():
    """测试任务创建请求默认分析配置"""
    request = TaskCreateRequest(
        product_goal="测试产品目标足够长以满足最小长度要求",
        competitors=["竞品A"],
    )
    assert request.analysis_profile.strategy == AnalysisStrategy.hybrid
    assert request.analysis_profile.focus_attributes == []
