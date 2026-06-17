# TRAE 主计划：2026 多 Agent 竞品情报系统（v6）

> 更新日期：2026-06-14  
> 基于源码核对 + `pytest` 实测（**56 passed / 1 deselected / 0 failed**，约 33s）  
> 对齐 Canvas：`multi-model-project-review` + `competitive-intelligence-workflow`

---

## 0. 给 TRAE 的一句话

**Sprint A/B/C/C-2/D 已基本完成。不要再做 Search 接线、Demo 按钮、冲突展示、README 同步——这些都已落地。**

当前目标：把现有「线性 7 节点 LangGraph 闭环」升级为 **2026 风格多 Agent 系统**——并行调研、条件回环、人工干预控制台、SSE 可观测、Reflexion 重写。

---

## 1. 现状校准（以代码为准，不是旧文档）

### 1.1 已完成 ✅

| 能力 | 状态 | 证据 |
|------|------|------|
| Evidence/Claim/Conflict/DecisionPack schema | ✅ | `schemas.py` |
| 输入安全层 + TaskBudget | ✅ | `security.py`, `routes.py` |
| LLM Writer/Reviewer + evidence_ids 校验 | ✅ | `workflow.py`, `test_writer_validation.py` |
| URLAdapter 真实抓取 | ✅ | `url_adapter.py` |
| CommentAdapter 评论聚类 | ✅ | `comment_adapter.py` |
| SearchAdapter + 工作流接线 | ✅ | `workflow.py:150-163`, `test_workflow.py` |
| ConflictResolver | ✅ | `conflict_resolver()` |
| Coverage Gate + gap_queries | ✅ | `coverage_gate()` |
| 前端质量分/缺口/冲突/Demo 按钮 | ✅ | `App.vue` |
| `.env.example` SEARCH_* | ✅ | `backend/.env.example` |
| README 与代码一致 | ✅ | `ci-agent/README.md` |
| Docker Compose | ✅ | `infra/docker-compose.yml` |
| Intervention API（仅记录） | ✅ | `routes.py:111-128` |

### 1.2 与两个 Canvas 的差距 ❌（2026 升级项）

| Canvas 要求 | 当前实现 | 差距 |
|-------------|----------|------|
| **Parallel Research**（并行采集） | `research()` 串行 for 循环 | 无并行、无子 Agent |
| **Coverage Gate → Research 回环** | 只生成 `gap_queries`，不补搜 | 无 LangGraph 条件边 |
| **Reflexion 重写**（Reviewer 低分回 Writer） | Reviewer 只评分，不重写 | 无循环边 |
| **Human Console**（任务/证据/输出级干预） | API 只写 event，无 UI，不触发重跑 | 前端无干预面板 |
| **SSE 实时进度** | 同步 `createTask` 等完成 | 无 EventSource |
| **Supervisor 动态编排** | Planner 只算预算 | 无 ResearchPlan 结构化输出 |
| **Policy/Style Gateway** | 无 | Writer prompt 固定 |
| **6 类决策产物** | 仅 positioning + mvp_priorities | 缺定价/Battlecard/预警 |
| **可观测仪表盘** | 有 budget_usage 文本 | 无时延/trace/修改率统计 |
| **工程抛光** | 1 个 pytest warning | httpx 弃用警告 |

### 1.3 架构对照图

```text
【当前】线性 DAG（已实现）
Planner → Research → EvidenceNormalizer → CoverageGate → ConflictResolver → Writer → Reviewer → END

【目标】2026 动态编排（待实现）
                    ┌── URL Agent ──┐
Planner → Supervisor ─┼─ Search Agent ─┼→ EvidenceNormalizer → CoverageGate ─┐
                    └── Comment Agent ┘                                        │
                              ↑ gap_queries 未达标且预算允许                    │
                              └────────────────────────────────────────────────┘
ConflictResolver → Writer → Reviewer ─┐ score < 阈值
                                       └→ Writer（Reflexion，最多 1 次）
                    Human Console ← 可随时 approve/reject/force_rerun
                    SSE ← 各节点 event 实时推送
```

---

## 2. 剩余 Sprint 路线图

```text
✅ Sprint A/B/C/C-2/D（MVP 闭环）—— 已完成
→ Sprint E（2026 编排核心，1-1.5 天）     ← 最优先
→ Sprint F（Human Console + SSE，0.5-1 天）
→ Sprint G（展示版决策包 + 可观测，0.5-1 天）
→ Sprint H（工程抛光，0.5 天）
```

**比赛叙事升级点**：从「能跑通的 Agent 流水线」→「模拟数字调研小组：并行采集、证据闸门、冲突裁决、人工可控、自纠错」。

---

## 3. 每个 Step 的 TRAE Plan 指令（直接复制）

> 用法：在 TRAE 中对每个 Step 单独开 Plan，把对应「Plan 启动指令」整段粘贴进去。  
> 每个 Step 完成后运行：`cd ci-agent/backend && python -m pytest -q`

---

### Step E1：Supervisor + ResearchPlan 结构化规划

**目标**：让 Planner 输出可执行的 `ResearchPlan`，而不只是预算估算。

**涉及文件**：
- `ci-agent/backend/app/models/schemas.py` — 新增 `ResearchPlan`, `ResearchTask`
- `ci-agent/backend/app/worker/workflow.py` — 改造 `planner()`
- `ci-agent/backend/tests/test_workflow.py` — 新增 planner 测试

**实现要点**：
1. 新增 schema：
   ```python
   class ResearchTask(BaseModel):
       competitor: str
       source_type: Literal["url", "search", "comment", "image"]
       query_or_url: str
       dimension: EvidenceDimension
       priority: int = 1

   class ResearchPlan(BaseModel):
       tasks: list[ResearchTask]
       dimensions: list[EvidenceDimension]
       keywords: list[str]
   ```
2. `planner()` 根据 `request.competitors/urls/comments/image_names` 生成 `task.research_plan`
3. 无 URL 时为每个竞品生成 search 任务；有 URL 则映射到对应竞品和维度
4. `TaskRecord` 增加 `research_plan: ResearchPlan | None = None`

**验收**：
- [ ] `test_workflow_planner_generates_research_plan` 通过
- [ ] 有 URL 时 plan 含 url 任务；无 URL 时 plan 含 search 任务
- [ ] `pytest -q` 全绿

**TRAE Plan 启动指令**：
```text
请为 ci-agent 实现 Step E1：Supervisor + ResearchPlan。

背景：planner() 目前只算 budget_usage，Canvas 要求输出结构化 ResearchPlan 供下游并行执行。

任务：
1. 在 schemas.py 新增 ResearchTask、ResearchPlan，TaskRecord 增加 research_plan 字段
2. 改造 workflow.py 的 planner()，根据 competitors/urls/comments/images 生成 ResearchPlan
3. 规则：有 URL → 生成 url 任务；无 URL → 每竞品生成 search 任务；有 comments → 生成 comment 任务
4. 新增 test_workflow.py 用例覆盖两种输入场景
5. 不改 research() 执行逻辑（E2 再做），本步只冻结 plan 契约

约束：最小 diff；pytest 默认全绿；不要开始做并行或 SSE。
```

---

### Step E2：Parallel Research（并行子 Agent 执行）

**目标**：`research()` 按 `ResearchPlan` 并行执行采集任务。

**涉及文件**：
- `ci-agent/backend/app/worker/workflow.py` — 改造 `research()`
- `ci-agent/backend/app/services/research_executor.py` — 新建（推荐）
- `ci-agent/backend/tests/test_workflow.py`

**实现要点**：
1. 新建 `research_executor.py`，每个 `ResearchTask` 对应一个执行函数：
   - `url` → `url_adapter.fetch()`
   - `search` → `search_adapter.search()`
   - `comment` → `comment_adapter.cluster()`
   - `image` → 现有 image Evidence 逻辑
2. 用 `concurrent.futures.ThreadPoolExecutor(max_workers=4)` 并行执行
3. 单个任务失败不阻塞整体，记录 warning event
4. 保持现有 Evidence schema 不变

**验收**：
- [ ] `test_research_executes_plan_in_parallel`（mock 各 adapter，验证并发调用）
- [ ] 失败子任务不导致整任务崩溃
- [ ] research event 消息含「并行执行 N 个采集任务」

**TRAE Plan 启动指令**：
```text
请实现 Step E2：Parallel Research。

前置：E1 已完成，TaskRecord.research_plan 可用。

任务：
1. 新建 app/services/research_executor.py，按 ResearchTask.source_type 分发到 url_adapter/search_adapter/comment_adapter
2. 改造 research()：读取 task.research_plan.tasks，ThreadPoolExecutor 并行执行，合并 Evidence
3. 若 research_plan 为空（兼容旧路径），fallback 到现有串行逻辑
4. 新增 test_workflow.py：mock 三个 adapter，断言并行调用且 evidence 合并正确
5. 单任务异常捕获，写 warning 级别 event，不 raise

约束：max_workers=4；不改 CoverageGate；pytest 全绿。
```

---

### Step E3：Coverage Gate → Research 条件回环

**目标**：证据不足时自动补搜一次，体现 Canvas 的「质量闸门 + 回环」。

**涉及文件**：
- `ci-agent/backend/app/worker/workflow.py` — LangGraph 条件边
- `ci-agent/backend/app/models/schemas.py` — `TaskRecord` 增加 `research_round: int = 0`
- `ci-agent/backend/tests/test_workflow.py`

**实现要点**：
1. 在 `build_graph()` 中把固定边 `coverage_gate → conflict_resolver` 改为条件边：
   ```python
   def route_after_coverage(state):
       task = state["task"]
       if (not task.coverage.passed 
           and task.research_round < 1 
           and task.request.budget.max_sources > len(task.evidence)):
           return "research"  # 补搜一轮
       return "conflict_resolver"
   ```
2. 回环前：用 `gap_queries` 调用 `search_adapter.search(query, competitor)` 补充 Evidence
3. `research_round += 1` 防止无限循环
4. `run_fallback()` 也需模拟此逻辑（或简化为最多 2 轮顺序执行）

**验收**：
- [ ] `test_coverage_gate_triggers_research_loop` — mock search，缺口时补证据
- [ ] `research_round` 不超过 1
- [ ] 前端 gap_queries 可见且第二轮后边覆盖可能改善

**TRAE Plan 启动指令**：
```text
请实现 Step E3：Coverage Gate 条件回环。

背景：Canvas 要求「不足则补搜」，当前只生成 gap_queries 不执行。

任务：
1. TaskRecord 增加 research_round: int = 0
2. 在 coverage_gate 后增加补搜逻辑：未达标且 round<1 时，用 gap_queries 调 search_adapter
3. LangGraph build_graph() 增加条件边 coverage_gate → research | conflict_resolver
4. run_fallback() 支持最多 2 轮 research（兼容无 LangGraph 环境）
5. 新增 test：coverage 未达标 → 触发补搜 → evidence 增加

约束：最多回环 1 次；无 SEARCH_API_KEY 时静默跳过补搜；pytest 全绿。
```

---

### Step E4：Reflexion 重写循环（Reviewer → Writer）

**目标**：Reviewer 低分时自动触发 Writer 重写一次。

**涉及文件**：
- `ci-agent/backend/app/worker/workflow.py`
- `ci-agent/backend/app/models/schemas.py` — `rewrite_round: int = 0`
- `ci-agent/backend/tests/test_workflow.py`

**实现要点**：
1. 条件边 `reviewer → writer | END`：
   ```python
   def route_after_reviewer(state):
       task = state["task"]
       if (task.review and task.review.score < 0.6 
           and task.rewrite_round < 1 
           and task.status != TaskStatus.failed):
           return "writer"
       return END
   ```
2. 重写时 Writer prompt 追加 `review.notes` 和 `hallucination_risk`
3. `rewrite_round += 1`

**验收**：
- [ ] `test_reviewer_triggers_rewrite_on_low_score`
- [ ] 只重写 1 次，不会死循环
- [ ] 前端 events 可见「Reviewer 触发重写」

**TRAE Plan 启动指令**：
```text
请实现 Step E4：Reflexion 重写循环。

背景：Canvas 的 Reflexion 模式——Critic 低分触发 rewrite。

任务：
1. TaskRecord 增加 rewrite_round: int = 0
2. reviewer() 后增加 LangGraph 条件边：score < 0.6 且 rewrite_round < 1 → writer，否则 END
3. _build_writer_prompt() 在重写时注入 review.notes 作为修正指引
4. run_fallback() 模拟此逻辑
5. 新增 test：mock LLM 返回低分 → 触发第二次 writer 调用

约束：最多重写 1 次；pytest 全绿。
```

---

### Step F1：SSE 实时任务进度

**目标**：前端实时看到各 Agent 节点进度，而不是等全任务完成。

**涉及文件**：
- `ci-agent/backend/app/api/routes.py` — 新增 `GET /tasks/{id}/events/stream`
- `ci-agent/backend/app/worker/workflow.py` — 异步执行（或线程 + queue）
- `ci-agent/frontend/src/services/api.ts` — EventSource 封装
- `ci-agent/frontend/src/App.vue` — 接入 SSE

**实现要点**：
1. 任务创建改为异步：`POST /tasks` 返回 `task_id` + `status=queued`
2. 后台线程执行 `run_competitive_intelligence_workflow`
3. SSE endpoint 推送 `TaskEvent` JSON
4. 前端 `submitTask` 后连接 SSE，实时更新 `currentTask.events` 和 `status`
5. 保留同步路径作为 fallback（或完全切换异步）

**验收**：
- [ ] 提交任务后 1s 内看到 planner event
- [ ] 任务完成时 SSE 关闭
- [ ] `test_api.py` 增加 SSE smoke test

**TRAE Plan 启动指令**：
```text
请实现 Step F1：SSE 实时进度。

背景：Canvas MVP 要求 SSE 进度，当前是同步等待。

任务：
1. POST /tasks 改为异步：立即返回 task_id，后台线程跑 workflow
2. 新增 GET /tasks/{id}/events/stream（text/event-stream）
3. 前端 api.ts 封装 subscribeTaskEvents(taskId, onEvent)
4. App.vue submitTask 后订阅 SSE，实时更新 stages 和 events
5. 完成/失败时关闭 EventSource

约束：不破坏现有 API 测试；提供轮询 fallback；pytest 全绿。
```

---

### Step F2：Human Console 人工干预面板

**目标**：把已有 Intervention API 做成可演示的前端功能。

**涉及文件**：
- `ci-agent/frontend/src/App.vue` — 干预面板 UI
- `ci-agent/frontend/src/services/api.ts` — `createIntervention()`
- `ci-agent/backend/app/api/routes.py` — 增强 `force_rerun` 逻辑
- `ci-agent/backend/tests/test_api.py`

**实现要点**：
1. 前端新增「人工干预」折叠面板：
   - 目标选择：任务 / 单条 Evidence / 决策动作
   - 操作：approve / reject / revise / force_rerun
   - 原因输入框（必填，4-500 字）
2. `force_rerun` 时：从指定 stage 重新执行 workflow（至少支持从 `writer` 重跑）
3. 所有干预写入 `human_intervention` event，前端展示干预历史
4. 展示「人工修改率」= 干预次数 / 完成任务数（任务级统计）

**验收**：
- [ ] 可对 Evidence 执行 reject 并记录
- [ ] `force_rerun` 从 writer 重跑成功
- [ ] 前端可见干预历史时间线

**TRAE Plan 启动指令**：
```text
请实现 Step F2：Human Console。

背景：Canvas 要求任务级/证据级/输出级干预；API 已有但无 UI 且无 force_rerun 执行。

任务：
1. api.ts 增加 createIntervention(taskId, payload)
2. App.vue 增加干预面板：选目标、选操作、填原因、提交
3. 后端增强 create_intervention：action=force_rerun 时从 writer 阶段重跑 workflow
4. 前端展示 human_intervention events 为审计时间线
5. 新增 test_api.py 覆盖 force_rerun

约束：不做复杂权限；最小 UI；pytest 全绿。
```

---

### Step G1：决策包扩展（定价 + Battlecard）

**目标**：对齐 Canvas「6 类决策产物」，在 MVP 上增加 2 类。

**涉及文件**：
- `ci-agent/backend/app/models/schemas.py` — `DecisionPack` 扩展
- `ci-agent/backend/app/worker/workflow.py` — Writer prompt + 解析
- `ci-agent/frontend/src/App.vue` — 展示新字段
- `ci-agent/backend/tests/test_workflow.py`

**实现要点**：
1. `DecisionPack` 增加：
   ```python
   pricing_insights: list[DecisionAction] = []
   battlecard: list[DecisionAction] = []
   ```
2. Writer prompt 要求输出 4 类：positioning / mvp_priorities / pricing_insights / battlecard
3. 无 pricing 证据时 pricing_insights 可为空，但 Writer 不得编造
4. 前端 Decision Pack 区域增加两个 Tab

**验收**：
- [ ] 有 pricing Evidence 时输出 pricing_insights
- [ ] battlecard 每条绑定 evidence_ids
- [ ] 前端 4 类产物均可展示

**TRAE Plan 启动指令**：
```text
请实现 Step G1：决策包扩展。

背景：Canvas 要求 6 类决策产物，当前只有 positioning + mvp_priorities。

任务：
1. DecisionPack 增加 pricing_insights、battlecard 字段
2. 更新 _build_writer_prompt() 和 writer() 解析逻辑
3. Reviewer 校验新字段的 evidence_ids
4. App.vue 增加定价和 Battlecard 展示
5. 新增 test：mock LLM 返回 4 类输出，断言解析正确

约束：无证据不编造；pytest 全绿。
```

---

### Step G2：可观测指标面板

**目标**：展示时延、成本、Citation 质量，支撑「可观测 + 可治理」叙事。

**涉及文件**：
- `ci-agent/backend/app/models/schemas.py` — `TaskMetrics`
- `ci-agent/backend/app/worker/workflow.py` — 记录各阶段耗时
- `ci-agent/frontend/src/App.vue` — 指标卡片

**实现要点**：
1. 新增 `TaskMetrics`：`total_duration_ms`, `stage_durations`, `evidence_count`, `conflict_count`, `intervention_count`
2. 每个 node 入口/出口记录时间戳
3. 前端新增「运行指标」卡片：总时延、证据数、冲突数、Reviewer 分数、Citation Precision

**验收**：
- [ ] 任务完成后 `task.metrics` 有值
- [ ] 前端展示时延和 citation_precision
- [ ] test 验证 metrics 字段存在

**TRAE Plan 启动指令**：
```text
请实现 Step G2：可观测指标面板。

任务：
1. schemas.py 新增 TaskMetrics，TaskRecord 增加 metrics 字段
2. workflow 各 node 记录 stage_durations
3. 任务完成时汇总 total_duration_ms、evidence_count、conflict_count
4. App.vue 增加「运行指标」Stat 卡片区域
5. 新增 test 验证 metrics 非空

约束：不改业务逻辑；pytest 全绿。
```

---

### Step H1：工程抛光

**目标**：清理 warning，提升代码现代性。

**涉及文件**：
- `ci-agent/backend/app/services/store.py` — 去除 `get_event_loop()`
- `ci-agent/backend/app/db/models.py` — `sqlalchemy.orm.declarative_base`
- `ci-agent/backend/app/main.py` — FastAPI lifespan 替代 `on_event`
- `ci-agent/backend/pyproject.toml` — 可选加 `httpx2` 或 pin 忽略

**验收**：
- [ ] `pytest -q` 0 warning（或只剩不可控的第三方 warning）
- [ ] `docker compose up --build` 正常
- [ ] README Docker 5 分钟指南实测通过

**TRAE Plan 启动指令**：
```text
请实现 Step H1：工程抛光。

任务：
1. store.py 替换 asyncio.get_event_loop().run_until_complete 为 asyncio.run 或纯 sync
2. models.py 改用 sqlalchemy.orm.declarative_base
3. main.py 用 lifespan context manager 替代 @app.on_event("startup")
4. 处理 httpx/starlette deprecation warning（优先升级 test client 依赖）
5. 验证 docker compose up --build 和 pytest -q

约束：不改业务行为；56+ tests 全绿。
```

---

## 4. 推荐执行顺序与依赖

```text
E1 ResearchPlan
 └→ E2 Parallel Research
     └→ E3 Coverage 回环
         └→ E4 Reflexion 重写
             ├→ F1 SSE（可并行）
             └→ F2 Human Console
                 └→ G1 决策包扩展
                     └→ G2 可观测面板
                         └→ H1 工程抛光
```

**如果时间紧（比赛前 < 2 天）最低配**：
1. **必做**：E2 + E3 + F2（并行 + 回环 + 人工干预 = 2026 叙事核心）
2. **次优**：E4 + F1（自纠错 + 实时进度）
3. **加分**：G1 + G2 + H1

---

## 5. 手动验收用例（每个 Sprint 完成后跑一遍）

```text
产品目标：为一个 AI 简历优化工具寻找差异化定位和首版 MVP 功能优先级
竞品：ResumeWorded, Kickresume
URL：https://www.resumeworded.com/
     https://www.kickresume.com/en/pricing/
评论：用户常抱怨模板同质化、定价偏高、中文场景支持不足...
图片：competitor-homepage.png
```

**E 阶段完成后应看到**：
- events 含「并行执行」「补搜回环」（如触发）
- Reviewer 低分时自动重写（如触发）

**F 阶段完成后应看到**：
- 提交后 SSE 逐阶段刷新
- 人工干预面板可 force_rerun

**G 阶段完成后应看到**：
- 决策包含定价 + Battlecard
- 指标面板有时延和 citation 数据

---

## 6. 明确不做（防止 TRAE 失焦）

- OCR / 视觉模型
- 视频解析
- 全平台评论爬虫
- 多租户权限
- 向量库 / 长期记忆
- 大规模前端重构
- pgvector Hybrid Retrieval（比赛后再说）

---

## 7. 给 TRAE 的首次启动指令（复制即用）

```text
请先阅读仓库根目录 TRAE_MASTER_PLAN.md（v6），不要参考过时的 NEXT_STEPS_FOR_TRAE.md v5。

当前 Sprint A/B/C/C-2/D 已完成，pytest 56 passed。请从 Step E1 开始：
1. 实现 ResearchPlan schema 和 planner() 改造
2. 新增 test_workflow planner 测试
3. 完成后汇报并等待我确认再做 E2

约束：
- 最小 diff，匹配现有代码风格
- pytest 默认全绿
- 不做 OCR/视频/爬虫/大重构
- 参考 Canvas 要求：并行、回环、人工干预、SSE、Reflexion
```

---

## 8. 验收总表

| Step | 关键验收 | 状态 |
|------|----------|------|
| AP | AnalysisProfile 策略选择器 + 前端表单 + 测试 | ✅ |
| E1 | ResearchPlan schema + planner 测试 | ❌ |
| E2 | 并行 research_executor | ❌ |
| E3 | Coverage → Research 条件回环 | ❌ |
| E4 | Reviewer → Writer Reflexion | ❌ |
| F1 | SSE 实时进度 | ❌ |
| F2 | Human Console + force_rerun | ❌ |
| G1 | 定价 + Battlecard 决策包 | ❌ |
| G2 | TaskMetrics 可观测面板 | ❌ |
| H1 | 0 warning + Docker 验证 | ❌ |

---

## 附录：关键文件速查

```text
ci-agent/backend/app/worker/workflow.py      # 核心：所有 Agent 节点与 LangGraph
ci-agent/backend/app/models/schemas.py       # 数据契约扩展点
ci-agent/backend/app/api/routes.py           # API + SSE + Intervention
ci-agent/backend/app/services/               # 各 Adapter
ci-agent/backend/tests/test_workflow.py      # 工作流测试主战场
ci-agent/frontend/src/App.vue                # Demo + 展示 + Human Console
ci-agent/frontend/src/services/api.ts          # API + SSE 客户端
ci-agent/infra/docker-compose.yml            # 部署验证
```

## 附录：Canvas 核心约束摘要

**multi-model-project-review**：
- Evidence 契约优先（✅ 已完成）
- 输入安全层（✅ 已完成）
- Coverage Gate 进 MVP（⚠️ 缺回环）
- 人工干预要可演示（⚠️ 缺 UI）
- Week 1 收敛 URL+文本+单图（✅ 已收敛）

**competitive-intelligence-workflow**：
- 7 节点 Agent 流（✅ 线性版完成）
- Parallel Research（❌）
- 4 质量闸门（⚠️ G1-G3 有，G4 产物可用性弱）
- LangGraph + Reflexion + Human-in-the-loop（⚠️ 部分）
- 决策包 > 报告（⚠️ 仅 2/6 产物）
