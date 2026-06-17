# ci-agent

`ci-agent` 是一个面向竞品情报与产品决策的多 Agent 闭环示例项目。它的目标不是做一个“看起来会回答问题”的聊天界面，而是把一条任务从提交、调研、证据归一、决策包生成、向量记忆写入、召回修订，到 Reviewer 复核与终止的链路完整跑通，并且让每一步都可解释、可观测、可回放。

## 项目定位

这个仓库适合展示三件事：

- 任务如何被拆成多个 Agent 阶段
- 决策如何进入记忆，再反向影响下一轮生成
- Reviewer 如何把高幻觉风险、证据不足和超限重写收口

## 闭环链路

当前主链路可以理解为：

```text
Task Submit
  → Planner
  → Research
  → Evidence Normalize
  → Coverage Gate
  → Conflict Resolver
  → Decision Pack Writer
  → Vector Memory Write
  → Memory Recall
  → Reviewer
  → Publish / Retry / Stop
```

### 这条链路里发生了什么

- **Planner**：把用户目标、竞品、补充说明、预算约束整理成任务上下文
- **Research**：采集和整理证据，形成可追踪的 evidence 集合
- **Coverage Gate**：检查关键维度是否覆盖，生成缺口信息
- **Conflict Resolver**：处理相互冲突或不一致的证据
- **Decision Pack Writer**：生成结构化决策包
- **Vector Memory**：把决策包拆成可检索块，写入记忆层
- **Memory Recall**：根据当前版本、轮次、风险和上下文召回修正块
- **Reviewer**：给出分数、风险和复写建议
- **Retry / Stop**：根据上限、评分和终止原因决定继续重写还是收口

## 仓库结构

```text
ci-agent/
├── backend/                # FastAPI + workflow + memory
├── frontend/               # Vue 3 控制台
├── infra/                  # 本地容器编排
├── canvases/               # 架构画布
└── README.md               # 当前文档
```

### 后端重点

- `backend/app/api/routes.py`：任务提交、任务查询、事件流入口
- `backend/app/worker/workflow.py`：主工作流、回流、终止、记忆写入与召回
- `backend/app/services/decision_memory.py`：决策记忆切块、写入、检索、标记 superseded
- `backend/app/models/schemas.py`：任务、证据、决策包、回流状态等结构定义
- `backend/tests/`：工作流和分析配置测试

### 前端重点

- `frontend/src/App.vue`：任务输入、状态机展示、Reviewer/回流/终止原因可视化
- `frontend/src/services/api.ts`：任务创建、任务查询、错误透传、事件订阅

## 启动方式

### 1. 后端

```bash
cd ci-agent/backend
pip install -e .
cp .env.example .env
uvicorn app.main:app --reload
```

### 2. 前端

```bash
cd ci-agent/frontend
npm install
npm run dev
```

### 3. 容器方式

如果你希望一键启动本地依赖，可以查看 `infra/` 下的 compose 配置：

```bash
cd ci-agent/infra
docker compose up --build
```

## 环境变量

后端主要依赖以下配置：

```env
LLM_API_KEY=your-api-key
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen-plus
LLM_TIMEOUT_SECONDS=60
LLM_MAX_RETRIES=2
LLM_TEMPERATURE=0.2
LLM_PROVIDER=dashscope

DB_HOST=localhost
DB_PORT=5432
DB_USERNAME=postgres
DB_PASSWORD=postgres
DB_DATABASE=ci_agent

SEARCH_API_KEY=
SEARCH_PROVIDER=serpapi
SEARCH_MAX_RESULTS=5
```

### 说明

- `LLM_API_KEY` 是生成真实决策包和 Reviewer 复核的关键配置
- `SEARCH_API_KEY` 用于未提供 URL 时的补证据场景
- 数据库未配置时，项目会使用降级存储路径，便于本地演示

## 示例输入

这是一个适合验证闭环的最小案例：

```text
产品目标：为一款手持小风扇产品寻找差异化定位和竞争策略，分析市场机会点

竞品：
几素高速节能手持小风扇
铁布衫手持风扇

评论：
几素风扇风力强劲但噪音略大，铁布衫风扇静音效果好但风力稍弱。消费者关注续航时间、便携性、噪音控制和价格。
```

## 运行后的可见结果

前端会显示：

- 当前 Agent 阶段
- 当前版本号
- 回流轮次
- 最近一次 Reviewer 结论
- 最近一次召回条目数
- 当前终止原因
- 任务事件流

后端会记录：

- task events
- decision history
- memory items
- review history
- memory_state

## 验证方式

### 后端测试

```bash
cd ci-agent/backend
python -m pytest -q
```

如果你要跑集成测试：

```bash
cd ci-agent/backend
python -m pytest -q -m integration
```

### 手工验收

1. 启动后端和前端
2. 提交一个包含竞品和目标的任务
3. 观察状态机是否从 Planner 进入 Research、Writer、Reviewer
4. 观察 `memory_state` 是否出现版本号、轮次、召回数和终止原因
5. 观察 Reviewer 低分时是否触发受控重写，而不是无限循环
6. 观察失败时是否能看到明确原因，而不是泛化错误

## 当前限制

这个项目是开源展示型闭环，不是完整生产系统，当前限制如下：

- **图片能力**：只保留元数据或轻量处理，不等同于完整视觉理解
- **评论采集**：不做平台级自动爬取，更多依赖用户输入或接入现有源
- **多租户**：未做完整租户隔离
- **视频分析**：不在当前范围
- **人工干预控制台**：目前仍以任务状态展示为主，尚不是完整操作台
- **真实效果依赖外部模型**：没有可用的 LLM 配置时，闭环能力会受限

## 代码口径

如果你在看源码，建议按这条顺序读：

1. `frontend/src/App.vue`
2. `backend/app/api/routes.py`
3. `backend/app/worker/workflow.py`
4. `backend/app/services/decision_memory.py`
5. `backend/app/models/schemas.py`
6. `backend/tests/test_workflow.py`

## 设计目标

这个项目现在追求的是：

- 输入和输出都结构化
- 任务状态能解释
- 决策包能回流
- 记忆能召回
- Reviewer 能收口
- 失败原因能说清楚

如果你是第一次接触这个仓库，先把它当作一个“多 Agent 闭环状态机 + 决策记忆系统”的展示项目来看，会比把它当作普通问答应用更准确。