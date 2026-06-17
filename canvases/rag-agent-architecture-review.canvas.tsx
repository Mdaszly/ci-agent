/// <reference path="./react.d.ts" />
/// <reference path="./cursor-canvas.d.ts" />
import {
  Card,
  CardBody,
  CardHeader,
  Callout,
  Divider,
  Grid,
  H1,
  H2,
  H3,
  Pill,
  Row,
  Stack,
  Stat,
  Table,
  Text,
  useHostTheme,
  mergeStyle,
} from "cursor/canvas";

const React: any = {};

export default function RagAgentArchitectureReview() {
  const theme = useHostTheme();

  const shell = {
    border: `1px solid ${theme.stroke.tertiary}`,
    background: theme.surface.primary,
    padding: 18,
  } as const;

  const panel = {
    border: `1px solid ${theme.stroke.secondary}`,
    background: theme.surface.secondary,
    padding: 16,
  } as const;

  const hero = mergeStyle(shell, {
    background: theme.surface.secondary,
  });

  const timeline = [
    { key: "1", title: "Task Submit", tone: "info" as const, desc: "前端只提交结构化请求，不吞错" },
    { key: "2", title: "Workflow", tone: "warning" as const, desc: "后端进入分析编排与状态机" },
    { key: "3", title: "DecisionPack", tone: "success" as const, desc: "输出版本化决策包" },
    { key: "4", title: "Qdrant", tone: "info" as const, desc: "写入可检索记忆块" },
    { key: "5", title: "Recall", tone: "warning" as const, desc: "召回旧结论、缺口与修正" },
    { key: "6", title: "Reviewer", tone: "info" as const, desc: "复核逻辑、覆盖率和风险" },
    { key: "7", title: "Retry / Stop", tone: "danger" as const, desc: "命中上限后终止循环" },
  ];

  const stackRows = [
    ["前端提交", "只做输入收敛", "把真实错误透传到界面，避免假成功"],
    ["后端入口", "校验预算 / URL / 图片名", "把请求边界放在服务端统一收口"],
    ["决策包", "Versioned Pack", "每轮产物都可回溯、可替换、可比较"],
    ["向量库", "Decision / Evidence / Repair / Review", "召回时不是找一段结论，而是找完整上下文"],
    ["Reviewer", "评分与纠偏", "把判错结果写回记忆层"],
  ];

  const memoryRows = [
    ["决策块", "定位建议 + 优先级 + 摘要 + 版本", "第二轮先对照旧结论"],
    ["证据块", "source / quote / confidence / freshness", "保证结论能回指到证据"],
    ["修正块", "冲突点 / 修复摘要 / 下一轮查询", "让循环聚焦补洞而不是重写"],
    ["复核块", "score / hallucination_risk / notes", "Reviewer 结果本身也入库"],
  ];

  const controlRows = [
    ["提交失败可见", "前端不做兜底伪造", "真实错误必须直接暴露"],
    ["回流边界", "DecisionPack / Evidence / Conflict / ReviewScore", "只让结构化对象进入记忆层"],
    ["终止条件", "coverage、citation_precision、max_iterations", "避免无限重试"],
    ["降幻觉方式", "检索 + 判错 + 回写 + 版本推进", "不是靠一次生成质量"],
  ];

  return (
    <Stack gap={16} style={{ padding: 20, color: theme.text.primary }}>
      <Card variant="default" size="lg" style={hero}>
        <CardBody>
          <Row align="center" justify="space-between" gap={12} wrap>
            <Stack gap={6}>
              <H1>任务提交失败修复与 Agent 闭环</H1>
              <Text tone="secondary">
                重点不是“生成一版更像样的答案”，而是把任务提交、决策包生成、向量回流、Reviewer 复核和重试终止做成一条可执行链路。
              </Text>
            </Stack>
            <Pill tone="info" active>
              先修提交，再补闭环
            </Pill>
          </Row>
        </CardBody>
      </Card>

      <Callout tone="warning" title="当前问题的真实形态">
        <Text>
          提交失败通常不是 Agent 不会分析，而是前端把后端拒绝原因吞掉了。先让错误可见，再让回流链路可复述、可回放、可收敛。
        </Text>
      </Callout>

      <Grid columns="1.2fr 0.8fr" gap={16}>
        <Card variant="default" size="lg" style={shell}>
          <CardHeader>闭环主链路</CardHeader>
          <CardBody>
            <Stack gap={14}>
              <Row gap={10} wrap>
                {timeline.map((step) => (
                  <Stat key={step.key} label={step.key} value={step.title} tone={step.tone} />
                ))}
              </Row>

              <Divider />

              <Stack gap={12}>
                <H2>执行顺序</H2>
                <Stack gap={10}>
                  {timeline.map((step) => (
                    <Stack key={step.key} gap={4}>
                      <Row align="center" gap={8} wrap>
                        <Pill tone={step.tone} active>
                          {step.key}
                        </Pill>
                        <Text weight="semibold">{step.title}</Text>
                      </Row>
                      <Text tone="secondary">{step.desc}</Text>
                    </Stack>
                  ))}
                </Stack>
              </Stack>
            </Stack>
          </CardBody>
        </Card>

        <Card variant="default" size="lg" style={panel}>
          <CardHeader>为什么能压幻觉</CardHeader>
          <CardBody>
            <Stack gap={12}>
              <Row gap={10} wrap>
                <Stat label="主约束" value="结构化" tone="success" />
                <Stat label="核心角色" value="Reviewer" tone="info" />
                <Stat label="记忆形态" value="带版本" tone="warning" />
              </Row>

              <Divider />

              <Stack gap={8}>
                <H3>必须同时存在的控制点</H3>
                <Row gap={8} wrap>
                  <Pill tone="warning">结构化</Pill>
                  <Pill tone="warning">可追溯</Pill>
                  <Pill tone="warning">带版本</Pill>
                  <Pill tone="warning">有上限</Pill>
                </Row>
                <Text tone="secondary">
                  单纯做向量召回只能提高“找回相关信息”的概率，不能替代事实校验。真正降低幻觉的是：检索、判错、回写、版本推进、终止条件一起成立。
                </Text>
              </Stack>

              <Divider />

              <Stack gap={8}>
                <H3>推荐模块切分</H3>
                <Text tone="secondary">入口层：FastAPI + Pydantic</Text>
                <Text tone="secondary">编排层：LangGraph</Text>
                <Text tone="secondary">记忆层：Qdrant + PostgreSQL + Redis</Text>
              </Stack>
            </Stack>
          </CardBody>
        </Card>
      </Grid>

      <H2>数据结构与数据流</H2>
      <Table headers={["组件", "作用", "为什么要这样拆"]} rows={stackRows} columnAlign={["left", "left", "left"]} />

      <H2>决策包写入向量库的粒度</H2>
      <Table headers={["块类型", "内容", "用途"]} rows={memoryRows} columnAlign={["left", "left", "left"]} />

      <H2>收敛规则</H2>
      <Table headers={["控制点", "约束", "结果"]} rows={controlRows} columnAlign={["left", "left", "left"]} />

      <Callout tone="info" title="最终表达">
        <Text>
          这条链路的核心不是“让模型多想一次”，而是让每一次输出都能被版本化、被召回、被复核、被终止。这样闭环才是可执行的，而不是只停留在叙述层。
        </Text>
      </Callout>
    </Stack>
  );
}