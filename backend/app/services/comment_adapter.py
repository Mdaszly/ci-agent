"""评论聚类适配器 - 将长评论聚类为多条 user_feedback Evidence"""
from __future__ import annotations

import hashlib
import logging
from typing import List, Dict, Any

from app.models.schemas import Evidence, EvidenceDimension, SourceType
from app.services.llm import LLMClient, LLMError, LLMNotConfiguredError

logger = logging.getLogger(__name__)


class CommentAdapter:
    """评论聚类适配器"""
    
    def __init__(self, llm_client: LLMClient = None):
        self.llm_client = llm_client or LLMClient()
    
    def cluster(self, comments: str, competitor: str, dimension: EvidenceDimension = EvidenceDimension.user_feedback) -> List[Evidence]:
        """
        将长评论聚类为多条 Evidence，按 dimension 指定维度
        
        Args:
            comments: 用户粘贴的长评论文本
            competitor: 竞品名称
            dimension: 证据维度（默认 user_feedback）
            
        Returns:
            多条指定维度的 Evidence
        """
        if not comments or len(comments.strip()) < 10:
            logger.warning("评论内容过短，返回单条 Evidence")
            return self._create_single_evidence(comments, competitor, dimension)
        
        try:
            themes = self._extract_themes_with_llm(comments, dimension)
            return self._create_evidence_from_themes(themes, competitor, dimension)
        except LLMNotConfiguredError:
            logger.info("LLM 未配置，使用关键词聚类")
            themes = self._extract_themes_by_keywords(comments, dimension)
            return self._create_evidence_from_themes(themes, competitor, dimension)
        except LLMError as e:
            logger.warning(f"LLM 调用失败: {e}, 使用关键词聚类")
            themes = self._extract_themes_by_keywords(comments, dimension)
            return self._create_evidence_from_themes(themes, competitor, dimension)
        except Exception as e:
            logger.error(f"评论聚类失败: {e}")
            return self._create_single_evidence(comments, competitor, dimension)
    
    def _extract_themes_with_llm(self, comments: str, dimension: EvidenceDimension = EvidenceDimension.user_feedback) -> List[Dict[str, Any]]:
        """使用 LLM 提取评论主题"""
        prompt = self._build_cluster_prompt(comments, dimension)
        
        messages = [
            {
                "role": "system",
                "content": "你是一个专业的评论分析助手，擅长从用户评论中提取关键主题和情感倾向。"
            },
            {"role": "user", "content": prompt}
        ]
        
        result = self.llm_client.chat_completion_json_sync(messages)
        
        if "themes" not in result:
            raise LLMError("LLM 返回格式错误，缺少 themes 字段")
        
        return result["themes"]
    
    def _build_cluster_prompt(self, comments: str, dimension: EvidenceDimension = EvidenceDimension.user_feedback) -> str:
        """构建聚类 prompt，按维度聚焦提取"""
        dimension_focus = {
            EvidenceDimension.pricing: "定价、价格、促销、性价比",
            EvidenceDimension.feature: "产品功能、参数、规格、性能表现",
            EvidenceDimension.user_feedback: "用户使用体验、满意度、优缺点反馈",
        }.get(dimension, "用户反馈")

        prompt_parts = [
            f"请从以下用户评论中提取与【{dimension_focus}】相关的关键主题，每个主题包含：",
            "- theme: 主题名称（简洁，如定价偏高、风力强劲）",
            "- quote: 支持该主题的原评论片段（不超过 100 字）",
            "- sentiment: 情感倾向（positive/negative/neutral）",
            "",
            "评论内容：",
            comments[:2000],
            "",
            "请严格按照以下 JSON 格式输出，不要输出任何其他内容：",
            "",
            '{"themes": [{"theme": "主题名称", "quote": "原评论片段", "sentiment": "negative"}]}',
            "",
            "规则：",
            "1. 提取 2-5 个最显著的主题",
            "2. 每个主题必须有明确的用户反馈支撑",
            "3. quote 必须是评论原文的片段，不能编造",
            "4. 如果评论内容不足以提取多个主题，返回 1 个概括性主题",
        ]
        return "\n".join(prompt_parts)
    
    def _extract_themes_by_keywords(self, comments: str, dimension: EvidenceDimension = EvidenceDimension.user_feedback) -> List[Dict[str, Any]]:
        """使用关键词提取评论主题（LLM 未配置时的 fallback）"""
        themes = []
        
        # 按维度选择关键词模式
        if dimension == EvidenceDimension.pricing:
            keyword_patterns = [
                ("价格定位", ["价格", "定价", "元", "价", "贵", "便宜", "性价比", "cost", "price", "pricing", "¥", "$"]),
                ("促销优惠", ["促销", "优惠", "折扣", "打折", "活动", "coupon", "discount"]),
            ]
        elif dimension == EvidenceDimension.feature:
            keyword_patterns = [
                ("风力性能", ["风力", "风速", "风量", "wind", "speed", "档位", "档"]),
                ("噪音控制", ["噪音", "分贝", "静音", "noise", "db", "声"]),
                ("续航能力", ["续航", "电池", "充电", "电量", "battery", "Type-C", "USB"]),
                ("便携设计", ["便携", "重量", "轻", "克", "portable", "weight", "手持"]),
            ]
        else:
            keyword_patterns = [
                ("定价偏高", ["贵", "定价", "价格", "太贵", "收费", "付费", "subscription", "price"]),
                ("模板同质化", ["模板", "同质化", "千篇一律", "模板化", "template"]),
                ("中文支持不足", ["中文", "本地化", "语言", "中文支持", "Chinese"]),
                ("功能限制", ["功能", "限制", "免费版", "基础版", "功能少", "feature"]),
                ("用户体验", ["体验", "界面", "操作", "使用", "UX", "UI"]),
                ("效果不佳", ["效果", "不好", "不满意", "没用", "效果差"]),
            ]
        
        for theme_name, keywords in keyword_patterns:
            matched_quotes = []
            for keyword in keywords:
                if keyword.lower() in comments.lower():
                    # 提取包含关键词的句子片段
                    start = comments.lower().find(keyword.lower())
                    if start != -1:
                        # 找到句子边界
                        sentence_start = max(0, comments.rfind(".", 0, start) + 1)
                        sentence_end = min(len(comments), comments.find(".", start) + 1)
                        if sentence_end == 0:
                            sentence_end = min(len(comments), start + 100)
                        quote = comments[sentence_start:sentence_end].strip()
                        if len(quote) > 10:
                            matched_quotes.append(quote[:100])
            
            if matched_quotes:
                themes.append({
                    "theme": theme_name,
                    "quote": matched_quotes[0],
                    "sentiment": "negative" if theme_name in ["定价偏高", "模板同质化", "中文支持不足", "功能限制", "效果不佳"] else "neutral"
                })
        
        if not themes:
            themes.append({
                "theme": "用户反馈",
                "quote": comments[:100],
                "sentiment": "neutral"
            })
        
        return themes[:5]
    
    def _create_evidence_from_themes(self, themes: List[Dict[str, Any]], competitor: str, dimension: EvidenceDimension = EvidenceDimension.user_feedback) -> List[Evidence]:
        """从主题列表创建 Evidence"""
        evidences = []
        
        for theme in themes:
            sentiment = theme.get("sentiment", "neutral")
            confidence = 0.7 if sentiment == "negative" else 0.6
            
            evidence = Evidence(
                source_type=SourceType.text,
                competitor=competitor,
                dimension=dimension,
                claim=f"用户反馈显示 {theme['theme']} 问题",
                quote=theme.get("quote", ""),
                confidence=confidence,
                freshness="user-provided",
                content_hash=hashlib.sha256(theme["theme"].encode()).hexdigest()[:16],
                credibility_score=0.55,
                quality_score=0.6,
            )
            evidences.append(evidence)
        
        return evidences
    
    def _create_single_evidence(self, comments: str, competitor: str, dimension: EvidenceDimension = EvidenceDimension.user_feedback) -> List[Evidence]:
        """创建单条 Evidence（评论过短或聚类失败时）"""
        content = comments if comments else "empty"
        return [
            Evidence(
                source_type=SourceType.text,
                competitor=competitor,
                dimension=dimension,
                claim="用户评论中出现了可用于产品机会判断的反馈信号",
                quote=comments[:240] if comments else "",
                confidence=0.68,
                freshness="user-provided",
                content_hash=hashlib.sha256(content.encode()).hexdigest()[:16],
                credibility_score=0.55,
                quality_score=0.6,
            )
        ]


comment_adapter = CommentAdapter()