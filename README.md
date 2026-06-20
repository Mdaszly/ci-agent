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
├── docs/                   # 文档与界面截图
└── README.md               # 当前文档
```

### 后端重点

- `backend/app/api/routes.py`：任务提交、任务查询、事件流入口
- `backend/app/worker/workflow.py`：主工作流、回流、终止、记忆写入与召回
- `backend/app/services/decision_memory.py`：决策记忆切块、写入、检索、标记 superseded
- `backend/app/models/schemas.py`：任务、证据、决策包、回流状态等结构定义
- `backend/tests/`：工作流和分析配置测试

### 核心技术：混合检索与 RRF 融合

本项目采用 **RRF（Reciprocal Rank Fusion）** 作为混合检索融合策略，选择理由如下：

#### 为什么需要混合检索？

竞品分析场景需要同时处理两种查询类型：
1. **精确匹配**：如产品名称（"iPhone 15"）、型号等
2. **语义理解**：如功能描述（"拍照好的手机"）、需求表达

单一检索方式无法满足需求：
- 纯向量检索擅长语义匹配，但对精确关键词召回效果差
- 纯关键词检索擅长精确匹配，但无法理解语义

#### 为什么选择 RRF？

RRF 通过**排名而非分数**来融合多路检索结果，具有以下优势：

| 特性 | RRF 的优势 | 项目价值 |
|------|------------|----------|
| **无需分数归一化** | BM25 分数和向量相似度量纲不同，RRF 直接比较排名 | 避免人工调参，降低维护成本 |
| **自适应权重** | 文档在多路检索中排名越靠前，总分越高 | 天然偏好"既有关键词匹配又有语义相关"的结果 |
| **开箱即用** | 无需训练数据，算法本身就是最优策略 | 快速上线，无需额外标注工作 |
| **稳定可靠** | 不依赖固定权重比例（如 0.7/0.3） | 在不同查询类型下表现一致 |

**核心公式**：
```
RRF(d) = Σ [1 / (k + rank_i(d))]
```
其中 `k=60`（缓和参数），`rank_i(d)` 是文档在第 i 路检索中的排名。

#### 配置方式

```env
MEMORY_FUSION_STRATEGY=rrf    # 可选: weighted / rrf
MEMORY_RRF_K=60              # RRF 参数
MEMORY_VECTOR_WEIGHT=0.7     # 线性加权时的向量权重
MEMORY_LEXICAL_WEIGHT=0.3    # 线性加权时的词面权重
```

### 前端页面

前端已经补齐多页面控制台，具体界面与截图放在 `docs/ui-gallery.md`，避免主文档继续膨胀。

可直接跳转到以下页面：

- 首页：`/`
- 召回测试：`/recall`
- 异常样本管理：`/bad-cases`
- 记忆浏览器：`/memory`
- 上下文监控：`/context`
- 检查点时间线：`/checkpoints`
- Agent 增强：`/agent-enhancements`

## 启动方式

### 1. 后端

```bash
cd ci-agent/backend
pip install -e .
cp .env.example .env
python -m app.run_backend --reload
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

容器启动时会自动执行数据库迁移（`alembic upgrade head`），无需手动建表。后端容器以非 root 用户运行，并通过 `/health/live` 和 `/health/ready` 端点提供健康检查。

**首次部署检查清单**：
1. 复制 `backend/.env.example` 为 `backend/.env` 并修改 `AUTH_JWT_SECRET`、`AUTH_DEFAULT_ADMIN_PASSWORD`
2. 生产环境设置 `ENVIRONMENT=production` 和 `CORS_ALLOWED_ORIGINS`
3. 执行 `docker compose up --build` 启动
4. 验证 `curl http://localhost:8000/health/ready` 返回 `{"status":"ok"}`

## 界面总览

前端界面截图和页面说明单独放在 `docs/ui-gallery.md`：

- 首页：任务提交、状态机总览、事件流和决策包展示
- 召回测试：召回基准测试、历史对比和结果评估
- 异常样本管理：异常样本登记、筛选、处理和汇总
- 记忆浏览器：记忆块检索、过滤、展开查看
- 上下文监控：各节点 token 使用情况与上下文占比
- 检查点时间线：工作流检查点、状态快照与回溯信息
- Agent 增强：运行时增强能力展示

## 环境变量

后端主要依赖以下配置：

### LLM Provider 配置

系统支持多 Provider 切换，通过 `LLM_PROVIDER` 环境变量指定：

**阿里云百炼（默认）**：
```env
LLM_PROVIDER=dashscope
LLM_API_KEY=your-dashscope-api-key
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen-plus
```

**OpenAI**：
```env
LLM_PROVIDER=openai
LLM_OPENAI_API_KEY=your-openai-api-key
LLM_OPENAI_BASE_URL=https://api.openai.com/v1
LLM_OPENAI_MODEL=gpt-4o
```

**通用配置**：
```env
LLM_TIMEOUT_SECONDS=60
LLM_MAX_RETRIES=2
LLM_TEMPERATURE=0.2
```

### 数据库配置

```env
DB_HOST=localhost
DB_PORT=5432
DB_USERNAME=postgres
DB_PASSWORD=postgres
DB_DATABASE=ci_agent
DB_USE_SQLITE=true  # 开发环境可设为 true 使用 SQLite
```

### 搜索配置

```env
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