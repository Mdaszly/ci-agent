# CI-Agent · 竞品情报决策 Agent

> Evidence-first multi-agent workflow for competitive intelligence decisions.
>
> 证据优先的多 Agent 竞品情报分析系统——从任务提交、并行调研、证据归一、决策包生成、向量记忆写入与召回、Reviewer 复核，到最终发布或受控重写，全链路可解释、可观测、可回放。

---

## 目录

- [1. 项目概述](#1-项目概述)
- [2. 技术架构](#2-技术架构)
- [3. 环境要求](#3-环境要求)
- [4. 安装与启动指南](#4-安装与启动指南)
- [5. 模块详解](#5-模块详解)
- [6. 多 Agent 项目流程](#6-多-agent-项目流程)
- [7. 决策包自主更新升级机制](#7-决策包自主更新升级机制)
- [8. 使用示例](#8-使用示例)
- [9. 常见问题与解决方案](#9-常见问题与解决方案)
- [10. 贡献指南](#10-贡献指南)
- [11. 许可证信息](#11-许可证信息)

---

## 1. 项目概述

### 1.1 核心功能

CI-Agent 是一个面向竞品情报与产品决策的多 Agent 闭环系统，核心能力包括：

| 能力 | 说明 |
|------|------|
| **多阶段工作流** | 7 个 Agent 节点串联（Planner → Research → Evidence → Coverage Gate → Conflict Resolver → Writer → Reviewer），支持条件回环和受控重写 |
| **证据优先架构** | 所有决策必须引用 Evidence ID，Writer 强校验引用合法性，Reviewer 核验引用真实性，无证据不编造 |
| **并行证据采集** | 基于 ResearchPlan 并行执行 URL 抓取、搜索引擎补搜、评论聚类、图片识别等多源采集任务 |
| **决策包版本化** | 每次修复生成新版本（version+1），旧版本标记 superseded，完整历史可追溯 |
| **决策记忆回流** | 通过 lexical 召回历史决策/冲突/修复/复核记忆，辅助新一轮生成，实现"越用越准" |
| **智能复核闭环** | Reviewer 结合规则校验 + LLM 智能评分，低分自动触发回流修复，高分自动发布 |
| **人工干预** | 支持 approve / reject / revise / force_rerun 四种干预动作，可从任意阶段强制重跑 |
| **实时可观测** | SSE 事件流实时推送各节点执行状态，前端可视化展示 Agent 决策全过程 |
| **多策略分析** | 内置 4 种分析策略（性价比/定价优势/产品力/自定义），支持五维权重调节和关注属性聚焦 |

### 1.2 设计理念

```
┌─────────────────────────────────────────────────────────────┐
│                    Evidence-First 设计理念                    │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  用户输入 ──→ 证据采集 ──→ 证据评分 ──→ 覆盖检查             │
│                  ↓              ↓              ↓             │
│              结构化归一    三维评分     缺口补搜              │
│                  ↓              ↓              ↓             │
│              冲突裁决 ←── 决策生成 ←── 记忆召回              │
│                  ↓              ↓              ↓             │
│              版本归档    Reviewer复核   记忆写入              │
│                  ↓              ↓              ↓             │
│              发布/重写 ←── 终止判断 ←── 回流修复             │
│                                                             │
│  核心原则：无证据不决策，低分必回流，每步可追溯              │
└─────────────────────────────────────────────────────────────┘
```

### 1.3 应用场景

- **新品立项前的竞品扫描**：快速了解竞品的功能、定价、用户反馈，找到差异化机会点
- **产品迭代中的策略调整**：基于最新市场证据，调整产品定位和功能优先级
- **投资决策前的尽职调查**：结构化整理竞品情报，生成可辩护的决策建议
- **营销战役前的对手分析**：生成 Battlecard，明确己方优势和对位策略

---

## 2. 技术架构

### 2.1 系统架构图

```
┌──────────────────────────────────────────────────────────────────────┐
│                        CI-Agent 系统架构                             │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌─────────────┐     HTTP/SSE      ┌──────────────────────────────┐ │
│  │             │ ←────────────────→ │                              │ │
│  │   Frontend  │    /api/tasks     │        Backend (FastAPI)     │ │
│  │   (Vue 3)   │                   │                              │ │
│  │             │    /api/tasks/    │  ┌────────────────────────┐  │ │
│  │  - 输入表单  │    {id}/events/   │  │     API Layer          │  │ │
│  │  - 流程可视化│    stream (SSE)   │  │  routes.py (12 endpoints)│  │ │
│  │  - 事件追踪  │                   │  └───────────┬────────────┘  │ │
│  │  - 指标面板  │                   │              ↓               │ │
│  └─────────────┘                   │  ┌────────────────────────┐  │ │
│                                    │  │   Workflow Engine       │  │ │
│                                    │  │  workflow.py (7 nodes)  │  │ │
│                                    │  └───────────┬────────────┘  │ │
│                                    │              ↓               │ │
│                                    │  ┌────────────────────────┐  │ │
│                                    │  │    Service Layer        │  │ │
│                                    │  │  - LLM Client           │  │ │
│                                    │  │  - URL Adapter          │  │ │
│                                    │  │  - Search Adapter       │  │ │
│                                    │  │  - Comment Adapter      │  │ │
│                                    │  │  - Evidence Scorer      │  │ │
│                                    │  │  - Research Executor    │  │ │
│                                    │  │  - Decision Memory      │  │ │
│                                    │  └───────────┬────────────┘  │ │
│                                    │              ↓               │ │
│                                    │  ┌────────────────────────┐  │ │
│                                    │  │    Storage Layer        │  │ │
│                                    │  │  - InMemoryTaskStore    │  │ │
│                                    │  │  - SQLite / PostgreSQL  │  │ │
│                                    │  │  - pgvector (可选)      │  │ │
│                                    │  └────────────────────────┘  │ │
│                                    └──────────────────────────────┘ │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                    External Services                         │   │
│  │  - LLM API (DashScope/OpenAI compatible)                    │   │
│  │  - Search API (SerpAPI)                                      │   │
│  └──────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────┘
```

### 2.2 技术栈

| 层级 | 技术 | 版本 | 用途 |
|------|------|------|------|
| **前端框架** | Vue 3 | latest | Composition API + `<script setup>` |
| **前端构建** | Vite | latest | 开发服务器 + 生产构建 |
| **前端语言** | TypeScript | latest | strict 模式，端到端类型安全 |
| **UI 组件库** | Element Plus | latest | 表单/反馈/布局组件 |
| **API 客户端** | openapi-fetch | ^0.17.0 | 基于 OpenAPI 规范的类型安全 HTTP 客户端 |
| **类型生成** | openapi-typescript | ^7.13.0 | 从后端 OpenAPI JSON 自动生成 TS 类型 |
| **后端框架** | FastAPI | >=0.115.0 | 异步 API 框架 |
| **后端语言** | Python | 3.11+ | 类型注解 + async/await |
| **工作流引擎** | LangGraph | >=0.2.0 | 状态图编排（含降级 fallback） |
| **数据模型** | Pydantic | >=2.8.0 | 运行时校验 + 序列化 |
| **ORM** | SQLAlchemy | >=2.0.0 | 异步 ORM（DeclarativeBase） |
| **数据库** | SQLite / PostgreSQL | - | 开发用 SQLite，生产用 PostgreSQL |
| **向量扩展** | pgvector | pg16 | PostgreSQL 向量相似度检索（可选） |
| **HTTP 客户端** | httpx | >=0.27.0 | 异步 HTTP 请求 |
| **HTML 解析** | BeautifulSoup4 + lxml | >=4.12.0 | 网页内容提取 |
| **容器化** | Docker + Docker Compose | - | 多服务编排 |
| **前端部署** | Nginx | 1.27-alpine | 静态文件服务 |

### 2.3 系统组件

#### 后端组件

```
ci-agent/backend/app/
├── main.py                         # FastAPI 应用入口（lifespan + CORS + 路由挂载）
├── api/
│   └── routes.py                   # 12 个 API 端点（任务CRUD + SSE + 干预 + 上传）
├── core/
│   ├── config.py                   # 配置管理（LLM / DB / Search 三组配置）
│   └── security.py                 # 安全校验（SSRF防护 / 文件上传 / 预算估算）
├── db/
│   ├── models.py                   # SQLAlchemy ORM（tasks / evidence / results 三表）
│   └── session.py                  # 异步会话工厂 + 自动建表 + SQLite schema 迁移
├── models/
│   └── schemas.py                  # Pydantic 数据模型（30+ 模型/枚举）
├── services/
│   ├── llm.py                      # LLM 客户端（OpenAI 兼容协议，同步+异步）
│   ├── url_adapter.py              # URL 抓取适配器（httpx + BeautifulSoup）
│   ├── search_adapter.py           # 搜索适配器（SerpAPI）
│   ├── comment_adapter.py          # 评论聚类适配器（LLM + 关键词降级）
│   ├── evidence_scorer.py          # 证据评分器（三维评分：可信度/相关性/质量）
│   ├── research_executor.py        # 研究任务执行器（并行分发 + 类型路由）
│   ├── decision_memory.py          # 决策记忆服务（切块/写入/召回/归档）
│   └── store.py                    # 任务存储（内存+DB双模式，自动降级）
└── worker/
    └── workflow.py                 # 工作流引擎（7节点 + 条件路由 + 记忆回流）
```

#### 前端组件

```
ci-agent/frontend/src/
├── main.ts                         # 应用入口（注册 Element Plus）
├── App.vue                         # 根组件（输入表单 + 流程可视化 + 事件追踪）
├── styles.css                      # 全局样式（设计令牌 + 主题覆盖 + 动画）
├── env.d.ts                        # Vite 环境类型声明
└── services/
    ├── api.ts                      # API 客户端封装（SSE + 轮询降级 + 错误解析）
    └── openapi.d.ts                # OpenAPI 自动生成的类型定义
```

---

## 3. 环境要求

### 3.1 开发环境

| 组件 | 最低版本 | 推荐版本 | 说明 |
|------|---------|---------|------|
| **Python** | 3.11 | 3.12 | 后端运行时 |
| **Node.js** | 18 | 22 | 前端构建 |
| **npm** | 9 | 10 | 前端包管理 |
| **Docker** | 24.0 | latest | 容器化部署（可选） |
| **Docker Compose** | 2.20 | latest | 多服务编排（可选） |
| **Git** | 2.30 | latest | 版本控制 |

### 3.2 外部服务（可选但推荐）

| 服务 | 用途 | 未配置时的降级策略 |
|------|------|-------------------|
| **LLM API** | 决策包生成 + 智能复核 + 评论聚类 | Writer 生成规则版决策包；Reviewer 仅规则校验；评论降级为关键词聚类 |
| **Search API** | 无 URL 时补充搜索证据 | 跳过补搜，仅依赖用户输入 |
| **PostgreSQL** | 生产数据持久化 | 自动降级为 SQLite（`data/ci_agent.db`） |

### 3.3 支持的 LLM 提供商

系统兼容 OpenAI Chat Completions API 协议，已测试以下提供商：

| 提供商 | base_url | 默认模型 |
|--------|----------|---------|
| 阿里云 DashScope | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| 其他兼容服务 | 自定义 | 自定义 |

---

## 4. 安装与启动指南

### 4.1 方式一：Docker Compose 一键启动（推荐）

```bash
# 1. 克隆仓库
git clone https://github.com/Mdaszly/ci-agent.git
cd ci-agent

# 2. 配置环境变量
cp ci-agent/backend/.env.example ci-agent/backend/.env
# 编辑 .env 文件，填入 LLM_API_KEY 等配置

# 3. 一键启动所有服务
cd ci-agent/infra
docker compose up --build

# 服务地址：
# - 前端：http://localhost:5173
# - 后端 API：http://localhost:8000
# - API 文档：http://localhost:8000/docs
# - PostgreSQL：localhost:5432
```

### 4.2 方式二：本地开发模式

#### 4.2.1 启动后端

```bash
# 1. 进入后端目录
cd ci-agent/backend

# 2. 创建虚拟环境（推荐）
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

# 3. 安装依赖（含开发依赖）
pip install -e ".[dev]"

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env，至少配置 LLM_API_KEY

# 5. 启动开发服务器
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

#### 4.2.2 启动前端

```bash
# 1. 进入前端目录
cd ci-agent/frontend

# 2. 安装依赖
npm install

# 3. 生成 API 类型定义（后端需先启动）
npm run gen:api

# 4. 启动开发服务器
npm run dev

# 前端地址：http://localhost:5173
```

### 4.3 环境变量配置

在 `ci-agent/backend/.env` 文件中配置：

```env
# ========== LLM 配置 ==========
LLM_API_KEY=your-api-key-here
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen-plus
LLM_TIMEOUT_SECONDS=60
LLM_MAX_RETRIES=2
LLM_TEMPERATURE=0.2
LLM_PROVIDER=dashscope

# ========== 数据库配置 ==========
# 开发模式使用 SQLite（无需额外安装）
DB_USE_SQLITE=true

# 生产模式使用 PostgreSQL
# DB_USE_SQLITE=false
# DB_HOST=localhost
# DB_PORT=5432
# DB_USERNAME=ci_agent
# DB_PASSWORD=ci_agent_dev
# DB_DATABASE=ci_agent

# ========== 搜索配置 ==========
SEARCH_API_KEY=your-serpapi-key
SEARCH_PROVIDER=serpapi
SEARCH_MAX_RESULTS=5

# ========== 运行模式 ==========
ENVIRONMENT=development
TASK_SYNC_MODE=false
```

### 4.4 验证安装

```bash
# 健康检查
curl http://localhost:8000/health
# 预期输出：{"status":"ok"}

# 运行后端测试
cd ci-agent/backend
python -m pytest -q
# 预期输出：XX passed

# 运行集成测试（需要真实 LLM API Key）
python -m pytest -q -m integration
```

---

## 5. 模块详解

### 5.1 API 层（`app/api/routes.py`）

提供 12 个 RESTful API 端点：

| 方法 | 路径 | 功能 | 关键特性 |
|------|------|------|---------|
| `POST` | `/api/tasks` | 创建分析任务 | 支持同步/异步模式；严格校验（URL公网可达性、预算预估、文件名合法性） |
| `GET` | `/api/tasks/{id}` | 获取任务详情 | 返回完整 TaskRecord（含证据、决策包、复核结果等） |
| `GET` | `/api/tasks/{id}/history` | 获取任务历史 | 决策历史、记忆状态、复核结果、事件流 |
| `GET` | `/api/tasks/{id}/memory` | 获取任务记忆 | 含 memory_items 列表 |
| `GET` | `/api/tasks/{id}/events/stream` | SSE 实时事件流 | 每 0.5s 轮询；完成/失败时推送 `[DONE]` |
| `GET` | `/api/tasks/{id}/evidence` | 列出证据 | coverage、evidence、claims、conflicts |
| `GET` | `/api/tasks/{id}/decision-pack` | 获取决策包 | 决策包内容 + 复核结果 |
| `POST` | `/api/tasks/{id}/cancel` | 取消任务 | 设置 cancelled 状态 |
| `POST` | `/api/tasks/{id}/interventions` | 人工干预 | approve/reject/revise/force_rerun |
| `POST` | `/api/uploads/images` | 图片校验 | 类型+大小校验 |
| `POST` | `/api/uploads/files` | 批量上传 | 图片+文档 |
| `POST` | `/api/uploads/file` | 单文件上传 | - |

### 5.2 工作流引擎（`app/worker/workflow.py`）

核心工作流引擎，基于 LangGraph StateGraph 实现 7 节点状态图：

```python
# 工作流拓扑
planner → research → evidence → coverage_gate → conflict_resolver → writer → reviewer
                                                                              ↓
                                                                       (条件路由)
                                                                       ├─ writer (回流修复)
                                                                       └─ END
```

**关键阈值常量**：

| 常量 | 值 | 含义 |
|------|-----|------|
| `QUALITY_THRESHOLD` | 0.5 | 证据质量分阈值 |
| `CREDIBILITY_THRESHOLD` | 0.4 | 证据可信度阈值 |
| `COVERAGE_PASS_THRESHOLD` | 0.7 | 覆盖率通过阈值 |
| `REVIEW_RETRY_THRESHOLD` | 0.6 | 触发回流修复的分数阈值 |
| `REVIEW_PUBLISH_THRESHOLD` | 0.8 | 自动发布的分数阈值 |

### 5.3 服务层（`app/services/`）

#### 5.3.1 LLM 客户端（`llm.py`）

```python
# 同步调用（工作流中使用）
result = llm_client.chat_completion_json_sync([
    {"role": "system", "content": "你是竞品情报分析师"},
    {"role": "user", "content": prompt},
])

# 异步调用（API 层可使用）
result = await llm_client.chat_completion_json(messages)
```

- 兼容 OpenAI Chat Completions API 协议
- 内置重试机制（`max_retries`）和超时控制
- 自动去除 markdown code fence，返回纯 JSON
- 异常类型：`LLMError`、`LLMNotConfiguredError`

#### 5.3.2 URL 抓取适配器（`url_adapter.py`）

- 使用 `httpx.Client` 抓取网页（follow_redirects, max_redirects=5）
- 伪装浏览器 UA，添加 Host header 防 DNS rebinding
- BeautifulSoup + lxml + SoupStrainer 高效解析
- 提取 title、text、price_info（正则匹配价格模式）
- HTML 转义防 XSS，文本截断至 8000 字符

#### 5.3.3 搜索适配器（`search_adapter.py`）

- 调用 SerpAPI 执行搜索
- `search_for_competitor()`：自动生成 pricing/features/reviews 三个查询
- 维度关键词映射：根据查询关键词推断 Evidence 维度
- 默认置信度：confidence=0.65, credibility=0.5, quality=0.55

#### 5.3.4 评论聚类适配器（`comment_adapter.py`）

- 优先使用 LLM 提取主题（2-5个）
- LLM 未配置时降级为关键词聚类
- 关键词模式覆盖：定价偏高、模板同质化、中文支持不足、功能限制等
- 每个主题生成一条 Evidence，confidence 根据情感倾向调整

#### 5.3.5 证据评分器（`evidence_scorer.py`）

三维评分体系，所有评分收敛到 [0, 1]：

| 维度 | 评分因素 |
|------|---------|
| **credibility_score**（可信度） | 来源类型（url +0.2）、可信域名（tmall/jd/小红书等 +0.1）、HTTPS、freshness、license_risk、confidence |
| **relevance_score**（相关性） | 上下文关键词匹配、focus_attributes 命中（+0.08/个，上限+0.24）、维度匹配、quote 长度 |
| **quality_score**（质量） | quote/claim 长度、confidence、untrusted 标记、特殊字符数量 |

#### 5.3.6 研究任务执行器（`research_executor.py`）

```python
# 根据 source_type 分发到对应 Adapter
def execute_task(task: ResearchTask) -> list[Evidence]:
    if task.source_type == "url":
        return _execute_url_task(task)      # → url_adapter
    elif task.source_type == "search":
        return _execute_search_task(task)   # → search_adapter
    elif task.source_type == "comment":
        return _execute_comment_task(task)  # → comment_adapter
    elif task.source_type == "image":
        return _execute_image_task(task)    # → 占位 Evidence
```

#### 5.3.7 决策记忆服务（`decision_memory.py`）

详见 [第 7 节：决策包自主更新升级机制](#7-决策包自主更新升级机制)。

#### 5.3.8 任务存储（`store.py`）

- **双模式存储**：内存缓存 + 数据库持久化
- 启动时检测数据库连接，失败则自动降级为纯内存模式
- `_run_async()`：在同步/异步上下文中安全执行协程
- 方法：`create` / `get` / `update` / `append_event` / `update_status` / `cancel`

### 5.4 数据模型（`app/models/schemas.py`）

#### 5.4.1 核心枚举

```python
class TaskStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    cancelled = "cancelled"
    failed = "failed"

class EvidenceDimension(str, Enum):
    feature = "feature"           # 产品特性
    pricing = "pricing"           # 定价
    positioning = "positioning"   # 市场定位
    user_feedback = "user_feedback"  # 用户反馈
    risk = "risk"                 # 风险/短板

class AnalysisStrategy(str, Enum):
    cost_leadership = "cost_leadership"  # 定价优势
    performance = "performance"          # 产品力优势
    hybrid = "hybrid"                    # 性价比导向
    custom = "custom"                    # 自定义权重
```

#### 5.4.2 数据模型关系

```
TaskCreateRequest
  ├── product_goal: str (8-800字)
  ├── competitors: list[str] (1-3个)
  ├── urls: list[str] (最多5)
  ├── competitor_urls: list[dict] (最多5)
  ├── comments: str
  ├── image_names: list[str] (最多3)
  ├── analysis_profile: AnalysisProfile
  └── budget: TaskBudget
        ↓
TaskRecord
  ├── evidence: list[Evidence]
  ├── claims: list[Claim]
  ├── conflicts: list[Conflict]
  ├── coverage: CoverageGateResult
  ├── decision_pack: DecisionPack
  │     ├── positioning: list[DecisionAction]
  │     ├── mvp_priorities: list[DecisionAction]
  │     ├── pricing_insights: list[DecisionAction]
  │     ├── battlecard: list[DecisionAction]
  │     └── summary: str
  ├── review: ReviewScore
  ├── events: list[TaskEvent]
  ├── decision_history: list[DecisionPackVersion]
  ├── memory_state: WorkflowMemoryState
  └── metrics: TaskMetrics
```

### 5.5 数据库层（`app/db/`）

#### 5.5.1 ORM 模型（三张表）

| 表名 | 主键 | 核心字段 | 索引 |
|------|------|---------|------|
| `tasks` | id (String) | product_goal, competitors(JSON), status, claims(JSON), events(JSON), decision_history(JSON), memory_state(JSON), review(JSON), coverage(JSON) | status, created_at |
| `evidence` | id (String) | task_id(FK), source_type, competitor, dimension, claim, quote, confidence, credibility_score, relevance_score, quality_score, content_hash | task_id, competitor, dimension, content_hash |
| `results` | id (String) | task_id(unique FK), positioning(JSON), mvp_priorities(JSON), summary, review_score, citation_precision, hallucination_risk, budget_usage(JSON) | task_id, generated_at |

#### 5.5.2 会话管理

- 使用 `create_async_engine` 创建异步引擎
- `init_db()`：调用 `Base.metadata.create_all` 建表
- SQLite 模式下额外执行 `_sync_sqlite_schema` 自动补齐缺失列（ALTER TABLE ADD COLUMN）
- SQLite 路径自动创建父目录

### 5.6 安全模块（`app/core/security.py`）

| 函数 | 功能 |
|------|------|
| `validate_public_url()` | SSRF 防护：校验协议、hostname 可解析、禁止危险端口、拒绝私网/本机/保留地址 |
| `validate_image_upload()` | 图片校验：png/jpeg/webp/gif/bmp，最大 10MB |
| `validate_file_upload()` | 文件校验：图片 + pdf/doc/docx/txt/md |
| `validate_image_name()` | 文件名合法性：正则 `^[\w.\- ]{1,180}$`，禁止路径穿越 |
| `estimate_budget_usage()` | 预估 token（1200 + source_count*900）和成本 |

### 5.7 前端模块（`App.vue`）

#### 5.7.1 功能区域

| 区域 | 功能 |
|------|------|
| **任务输入卡片** | 产品目标、竞品列表、用户评论、分析策略选择、关注属性多选、我方产品提示、回流控制、自定义权重 |
| **Agent 运作流程卡片** | 七阶段流水线轨道、进度条、运行指标（版本/轮次/召回/Reviewer状态）、终止状态盒、事件轨迹 |
| **当前输入摘要卡片** | 竞品数量、关注属性、回流策略、终止条件 |

#### 5.7.2 分析策略

| 策略 | 标签 | 维度权重 | 必达维度 |
|------|------|---------|---------|
| `hybrid` | 性价比导向 | feature:0.30, pricing:0.25, user_feedback:0.25, positioning:0.10, risk:0.10 | feature, pricing, user_feedback |
| `cost_leadership` | 定价优势 | pricing:0.40, feature:0.25, user_feedback:0.20, positioning:0.10, risk:0.05 | pricing, feature, user_feedback |
| `performance` | 产品力优势 | feature:0.35, user_feedback:0.25, pricing:0.20, positioning:0.10, risk:0.10 | feature, user_feedback |
| `custom` | 自定义权重 | 用户自定义（自动归一化） | 权重 top3 |

---

## 6. 多 Agent 项目流程

### 6.1 工作流总览

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         多 Agent 工作流程                                 │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────────┐     │
│  │ Planner  │───→│ Research │───→│ Evidence │───→│ Coverage Gate│     │
│  │  规划器   │    │  调研器   │    │  评分器   │    │   覆盖检查    │     │
│  └──────────┘    └──────────┘    └──────────┘    └──────┬───────┘     │
│                                                         ↓              │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────────┐     │
│  │ Reviewer │←──│  Writer  │←──│ Conflict │←──│    补搜      │     │
│  │  复核器   │    │  写作器   │    │ Resolver │    │ (if needed)  │     │
│  └────┬─────┘    └──────────┘    └──────────┘    └──────────────┘     │
│       │                                                                │
│       ↓                                                                │
│  ┌──────────────────────────────────┐                                  │
│  │         条件路由决策              │                                  │
│  ├─ score >= 0.8 & risk=low → 发布  │                                  │
│  ├─ score < 0.6 or risk=high → 重写 │                                  │
│  └─ 其他 → 终止                     │                                  │
│  └──────────────────────────────────┘                                  │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### 6.2 各 Agent 节点详解

#### 6.2.1 Planner（规划器）

**职责**：拆解竞品范围和输入来源，生成可执行的 ResearchPlan。

**输入**：`TaskRecord`（含产品目标、竞品列表、URL、评论、图片、分析策略）

**输出**：`ResearchPlan`（含 ResearchTask 列表）

**任务生成规则**：

| 输入类型 | 生成任务 | source_type | dimension | priority |
|---------|---------|-------------|-----------|----------|
| URL 绑定 | 每个竞品-URL 对 | `url` | feature | 1 |
| 竞品名称 | 每个竞品生成搜索任务 | `search` | feature | 2 |
| 关注属性含价格 | 额外生成定价搜索 | `search` | pricing | 2 |
| 用户评论 | 生成评论分析任务 | `comment` | user_feedback | 1 |
| 图片名称 | 生成图片识别任务 | `image` | positioning | 1 |

**搜索查询构建**：
- 无 focus_attributes：`{competitor} {product_goal}`
- 有 focus_attributes：`_build_focus_search_query()` 构建聚焦查询

#### 6.2.2 Research（调研器）

**职责**：并行执行 ResearchPlan 中的所有采集任务。

**执行方式**：
```python
# 并行执行（ThreadPoolExecutor, max_workers=4）
with ThreadPoolExecutor(max_workers=4) as executor:
    future_to_task = {executor.submit(execute_task, item): item for item in tasks}
    for future in as_completed(future_to_task):
        evidence.extend(future.result())
```

**任务分发**：
- `url` → URLAdapter（网页抓取 + 价格提取）
- `search` → SearchAdapter（SerpAPI 搜索）
- `comment` → CommentAdapter（LLM 聚类 / 关键词降级）
- `image` → 占位 Evidence（confidence=0.58）

#### 6.2.3 Evidence Normalizer（证据归一器）

**职责**：对采集到的证据进行三维评分，并转换为结构化 Claim。

**评分流程**：
1. 调用 `evidence_scorer.score()` 计算三维评分
2. 标记不可信来源（用户输入标记为 untrusted）
3. 将 Evidence 转换为 Claim（提取核心观点）

#### 6.2.4 Coverage Gate（覆盖检查器）

**职责**：检查证据是否覆盖所有必达维度，未达标时触发补搜。

**覆盖计算**：
```python
# 高质量证据：quality_score >= 0.5 AND credibility_score >= 0.4
high_quality_evidence = [ev for ev in evidence if ...]

# 加权覆盖率
weighted_score = sum(weights[d] for d in covered_high_quality) / sum(weights[d] for d in mandatory)

# 通过条件
passed = weighted_score >= 0.7 AND len(missing_dimensions) == 0 AND len(low_quality_dimensions) == 0
```

**补搜策略**：
- 仅在 `research_round < 1` 时触发（最多补搜一轮）
- 为每个竞品和缺失维度生成 gap_queries
- 补搜后重新计算覆盖率

#### 6.2.5 Conflict Resolver（冲突裁决器）

**职责**：检测并裁决同竞品同维度的证据冲突。

**冲突检测类型**：
- 价格不一致（同竞品不同价格）
- 抓取成功/失败对立
- 功能有/无对立

**裁决策略**：按 credibility_score 排序，保留高可信度证据

#### 6.2.6 Writer（决策包写作器）

**职责**：基于证据和策略生成结构化决策包。

**生成流程**：
1. 构建 LLM prompt（含策略指引、品类提示、修正指引）
2. 调用 LLM 生成 JSON 格式决策包
3. 校验 evidence_ids 有效性（所有引用必须是真实存在的 Evidence ID）
4. 校验 pricing_insights 必须引用定价维度证据
5. 持久化决策包版本到 decision_history
6. 写入决策记忆块

**降级策略**：LLM 未配置时，调用 `_build_rule_based_decision_pack()` 生成规则版决策包

#### 6.2.7 Reviewer（复核器）

**职责**：对决策包进行规则校验 + LLM 智能复核。

**规则校验**：
- 引用核验：所有 evidence_ids 必须存在
- 维度匹配：DecisionAction.dimension 必须与 Evidence 维度兼容
- 空值检查：recommendation 不能为空
- pricing/battlecard 证据合法性

**LLM 智能复核**：
- 输入：决策包内容 + 证据清单 + 历史复核意见
- 输出：`score_adjustment`（-0.2 ~ +0.2）、`hallucination_risk`、`notes`
- 最终评分 = 规则评分 + LLM 调整

### 6.3 Agent 间通信机制

#### 6.3.1 状态传递

Agent 间通过 `WorkflowState` 传递状态：

```python
class WorkflowState(TypedDict):
    task: TaskRecord  # 完整任务记录，包含所有中间状态
```

每个 Agent 节点接收 `state`，修改 `task` 的相应字段，返回更新后的 `state`。

#### 6.3.2 事件流

每个 Agent 节点通过 `_add_event()` 记录执行事件：

```python
_add_event(task, "planner", "已拆解竞品范围、输入来源；分析策略=性价比导向")
```

事件通过 SSE 实时推送到前端，也可通过 `GET /api/tasks/{id}/history` 查询。

#### 6.3.3 条件路由

```python
# Reviewer 后的条件路由
def route_after_reviewer(state: WorkflowState) -> str:
    task = state["task"]
    if task.review.score >= 0.8 and task.review.hallucination_risk == "low":
        return END                          # 发布
    if state.current_iteration >= 1:        # 已修复过一次
        return END                          # 终止
    if task.review.score < 0.6 or task.review.hallucination_risk == "high":
        return "writer"                     # 回流修复
    return END                              # 终止
```

### 6.4 任务分配策略

| 策略 | 说明 |
|------|------|
| **并行采集** | Research 阶段使用 ThreadPoolExecutor（4 workers）并行执行所有 ResearchTask |
| **按类型分发** | research_executor 根据 source_type 路由到对应 Adapter |
| **按优先级** | ResearchTask.priority（1=高优先, 2=普通）影响执行顺序 |
| **按维度** | 每个任务绑定 EvidenceDimension，确保多维度覆盖 |
| **预算控制** | max_sources 限制最大证据数量，超限停止采集 |

### 6.5 协同工作模式

```
                    ┌─────────────────────┐
                    │   用户提交任务       │
                    └──────────┬──────────┘
                               ↓
                    ┌─────────────────────┐
                    │  Planner 生成计划    │
                    │  (URL+搜索+评论+图片)│
                    └──────────┬──────────┘
                               ↓
              ┌────────────────┼────────────────┐
              ↓                ↓                ↓
     ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
     │ URL Adapter  │ │Search Adapter│ │Comment Adapter│
     │  (并行执行)   │ │  (并行执行)   │ │  (并行执行)   │
     └──────┬───────┘ └──────┬───────┘ └──────┬───────┘
            └────────────────┼────────────────┘
                             ↓
                    ┌─────────────────────┐
                    │  Evidence Scorer    │
                    │  (三维评分+归一化)   │
                    └──────────┬──────────┘
                               ↓
                    ┌─────────────────────┐
                    │  Coverage Gate      │
                    │  (覆盖检查+补搜)     │
                    └──────────┬──────────┘
                               ↓
                    ┌─────────────────────┐
                    │  Conflict Resolver  │
                    │  (冲突检测+裁决)     │
                    └──────────┬──────────┘
                               ↓
                    ┌─────────────────────┐
                    │  Memory Recall      │
                    │  (召回历史记忆)      │
                    └──────────┬──────────┘
                               ↓
                    ┌─────────────────────┐
                    │  Writer             │
                    │  (LLM生成决策包)     │
                    └──────────┬──────────┘
                               ↓
                    ┌─────────────────────┐
                    │  Memory Write       │
                    │  (写入决策记忆)      │
                    └──────────┬──────────┘
                               ↓
                    ┌─────────────────────┐
                    │  Reviewer           │
                    │  (规则+LLM复核)      │
                    └──────────┬──────────┘
                               ↓
                    ┌─────────────────────┐
                    │  路由决策            │
                    ├─ 发布 (score>=0.8)  │
                    ├─ 重写 (score<0.6)  │
                    └─ 终止 (其他)        │
                    └─────────────────────┘
```

---

## 7. 决策包自主更新升级机制

### 7.1 决策包结构设计

```python
class DecisionPack(BaseModel):
    # 决策内容
    positioning: list[DecisionAction]      # 定位建议
    mvp_priorities: list[DecisionAction]   # MVP 优先级
    pricing_insights: list[DecisionAction] # 定价洞察
    battlecard: list[DecisionAction]       # 竞品对比卡
    summary: str                           # 摘要

    # 版本管理
    pack_id: str                           # 决策包唯一ID
    version: int                           # 版本号（从1开始递增）
    parent_pack_id: str | None             # 父版本ID
    superseded_by: str | None              # 被哪个版本替代
    status: DecisionPackStatus             # draft/approved/rejected/superseded

class DecisionAction(BaseModel):
    title: str                             # 动作标题
    dimension: EvidenceDimension           # 所属维度
    recommendation: str                    # 具体建议
    rationale: str                         # 推理依据
    evidence_ids: list[str]                # 引用的证据ID（强校验）
    priority: str                          # P0/P1/P2
```

### 7.2 版本管理策略

```
版本演进时间线：

v1 (draft) ──→ Reviewer 低分 ──→ v1 (superseded) ──→ v2 (draft) ──→ Reviewer 通过 ──→ v2 (approved)
                  ↓                                        ↑
            Memory Recall                            Memory Write
            (召回历史记忆)                           (写入新记忆块)
                  ↓                                        ↑
            Repair Decision Pack                    Generate v2
```

**版本状态流转**：

| 状态 | 含义 | 触发条件 |
|------|------|---------|
| `draft` | 草稿 | Writer/Repair 生成新版本 |
| `approved` | 已批准 | Reviewer 评分 >= 0.8 且 hallucination_risk = low |
| `rejected` | 已拒绝 | Reviewer 明确拒绝 |
| `superseded` | 已替代 | 新版本生成时，旧版本自动标记 |

**版本归档流程**：

```python
def _archive_current_pack(task: TaskRecord) -> None:
    """归档当前版本，为修复版本做准备"""
    if task.decision_pack is None:
        return
    # 当前版本标记为 superseded
    task.decision_pack.status = DecisionPackStatus.superseded
    # 记录到 decision_history
    task.decision_history.append(DecisionPackVersion(
        pack_id=task.decision_pack.pack_id,
        version=task.decision_pack.version,
        status=DecisionPackStatus.superseded,
        stage="archive",
        ...
    ))
```

### 7.3 决策记忆系统

#### 7.3.1 记忆块结构

```python
class DecisionMemoryItem(BaseModel):
    id: str                           # 记忆块唯一ID
    task_id: str                      # 所属任务ID
    pack_id: str                      # 所属决策包ID
    version: int                      # 决策包版本
    chunk_type: DecisionChunkType     # 记忆块类型
    content: str                      # 记忆内容
    source_refs: str                  # 来源引用
    risk_level: str                   # 风险等级
    created_at: datetime              # 创建时间
```

#### 7.3.2 记忆块类型

| chunk_type | 含义 | 来源 |
|------------|------|------|
| `decision` | 决策内容 | Writer 生成的决策包 |
| `evidence` | 证据摘要 | Evidence Normalizer |
| `conflict` | 冲突记录 | Conflict Resolver |
| `repair` | 修复信息 | Repair 节点 |
| `reviewer_feedback` | 复核反馈 | Reviewer |

#### 7.3.3 记忆写入流程

```python
def _commit_decision_memory(task: TaskRecord, stage: str) -> None:
    """将决策包拆分为记忆块并写入"""
    chunks = build_memory_chunks(task, stage)  # 拆分为多个 chunk
    upsert_decision_memory(task.id, chunks)    # 写入记忆索引
```

**记忆块拆分逻辑**：

```python
def build_memory_chunks(task: TaskRecord, stage: str) -> list[DecisionMemoryItem]:
    chunks = []
    # 1. 决策内容块
    for action in task.decision_pack.positioning + task.decision_pack.mvp_priorities:
        chunks.append(DecisionMemoryItem(
            chunk_type=DecisionChunkType.decision,
            content=f"{action.title}: {action.recommendation}",
            ...
        ))
    # 2. 证据块
    for evidence in task.evidence:
        chunks.append(DecisionMemoryItem(
            chunk_type=DecisionChunkType.evidence,
            content=f"{evidence.dimension}: {evidence.claim}",
            ...
        ))
    # 3. 冲突块
    for conflict in task.conflicts:
        chunks.append(DecisionMemoryItem(
            chunk_type=DecisionChunkType.conflict,
            content=conflict.description,
            ...
        ))
    return chunks
```

#### 7.3.4 记忆召回机制

```python
def _recall_decision_memory(task: TaskRecord) -> list[DecisionMemoryItem]:
    """召回与当前任务相关的历史记忆"""
    context = _build_recall_context(task)  # 构建召回上下文
    recalled = search_decision_memory(
        task_id=task.id,
        query=context,
        top_k=5,                           # 召回 top 5
        chunk_types=[DecisionChunkType.decision,
                     DecisionChunkType.reviewer_feedback,
                     DecisionChunkType.conflict],
    )
    return recalled
```

**召回评分算法**：

```python
def search_decision_memory(
    task_id: str,
    query: str,
    top_k: int = 5,
    chunk_types: list[DecisionChunkType] | None = None,
) -> list[DecisionMemoryItem]:
    """基于词面重叠的 lexical 召回"""

    for item in all_items:
        # 1. Lexical 评分（词面重叠度）权重 0.58
        lexical_score = compute_lexical_overlap(query, item.content)

        # 2. Direct Match 评分（精确匹配）权重 0.24
        direct_score = compute_direct_match(query, item.content)

        # 3. Boost 因子
        version_boost = 1.0 if item.version == current_version else 0.8  # 同版本优先
        stage_boost = 1.2 if item.chunk_type == "reviewer_feedback" else 1.0  # 复核反馈加权
        recency_boost = compute_recency(item.created_at)  # 时间衰减

        # 4. 综合评分
        final_score = (lexical_score * 0.58 + direct_score * 0.24) * version_boost * stage_boost * recency_boost

    # 返回 top_k
    return sorted(all_items, key=lambda x: x.final_score, reverse=True)[:top_k]
```

### 7.4 自主更新升级流程

```
┌─────────────────────────────────────────────────────────────────┐
│                   决策包自主更新升级流程                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. Writer 生成 v1 决策包                                       │
│     ↓                                                           │
│  2. 写入决策记忆（decision + evidence + conflict chunks）       │
│     ↓                                                           │
│  3. Reviewer 复核 v1                                            │
│     ├─ score >= 0.8 & risk=low → 发布 v1 (approved)             │
│     └─ score < 0.6 or risk=high → 进入修复流程                  │
│     ↓                                                           │
│  4. 归档 v1 (superseded)                                       │
│     ↓                                                           │
│  5. 召回历史记忆（top 5）                                       │
│     - 同版本的 decision chunks                                  │
│     - 历史的 reviewer_feedback chunks                           │
│     - 相关的 conflict chunks                                    │
│     ↓                                                           │
│  6. 基于召回记忆 + Reviewer 意见生成 v2 决策包                   │
│     - prompt 注入召回的记忆块内容                               │
│     - prompt 注入 Reviewer 的 notes                             │
│     - prompt 注入缺失维度提示                                   │
│     ↓                                                           │
│  7. 写入 v2 决策记忆                                            │
│     ↓                                                           │
│  8. Reviewer 复核 v2                                            │
│     ├─ score >= 0.8 & risk=low → 发布 v2 (approved)             │
│     └─ 仍未达标 → 终止（最多 1 轮修复）                         │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 7.5 向量数据库与调优策略

#### 7.5.1 当前实现

当前版本使用**内存级 lexical 召回**（基于词面重叠度），通过 `_MEMORY_INDEX` 字典维护任务级记忆索引，线程安全（RLock 保护）。

**优势**：
- 零依赖，无需额外部署向量数据库
- 响应速度快（内存操作）
- 适合中小规模任务

**局限**：
- 语义理解能力有限（仅词面匹配）
- 不支持跨任务记忆共享
- 大规模数据下性能下降

#### 7.5.2 向量数据库扩展方案

项目已预留 PostgreSQL + pgvector 扩展支持（`docker-compose.yml` 中使用 `pgvector/pgvector:pg16` 镜像）：

```yaml
# docker-compose.yml
postgres:
  image: pgvector/pgvector:pg16  # 带 pgvector 扩展的 PostgreSQL
  environment:
    POSTGRES_DB: ci_agent
    POSTGRES_USER: ci_agent
    POSTGRES_PASSWORD: ci_agent_dev
```

**扩展路径**：

1. **Embedding 生成**：将决策记忆块内容通过 embedding 模型转换为向量
2. **向量存储**：使用 pgvector 的 `vector` 类型存储 embedding
3. **相似度检索**：使用 `<=>`（余弦距离）或 `<->`（L2 距离）进行向量相似度检索
4. **混合检索**：结合 lexical 召回 + 向量召回，取并集后重排序

```sql
-- 向量检索示例（未来扩展）
SELECT id, content, content_embedding <=> $1 AS distance
FROM decision_memory_items
WHERE task_id = $2
ORDER BY content_embedding <=> $1
LIMIT 5;
```

#### 7.5.3 调优策略

| 策略 | 参数 | 默认值 | 调优建议 |
|------|------|--------|---------|
| **召回数量** | `top_k` | 5 | 复杂任务可增加到 8-10，简单任务可减少到 3 |
| **Lexical 权重** | - | 0.58 | 语义匹配重要时降低，关键词匹配重要时提高 |
| **Direct Match 权重** | - | 0.24 | 精确匹配场景可提高 |
| **版本偏好** | `version_boost` | 同版本 1.0 / 跨版本 0.8 | 跨版本参考重要时可调平 |
| **阶段加权** | `stage_boost` | reviewer_feedback 1.2 | 可根据场景调整不同 chunk_type 的权重 |
| **时间衰减** | `recency_boost` | 指数衰减 | 长期记忆重要时可减弱衰减 |
| **最大修复轮次** | `max_iterations` | 3 | 可通过前端配置（1-8） |
| **发布阈值** | `REVIEW_PUBLISH_THRESHOLD` | 0.8 | 严格场景可提高到 0.85，宽松场景可降低到 0.7 |
| **重试阈值** | `REVIEW_RETRY_THRESHOLD` | 0.6 | 低于此分触发修复 |
| **覆盖阈值** | `COVERAGE_PASS_THRESHOLD` | 0.7 | 严格场景可提高到 0.8 |
| **质量阈值** | `QUALITY_THRESHOLD` | 0.5 | 过滤低质量证据 |
| **可信度阈值** | `CREDIBILITY_THRESHOLD` | 0.4 | 过滤低可信度证据 |

---

## 8. 使用示例

### 8.1 基础用法：提交竞品分析任务

```bash
# 通过 API 创建任务
curl -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "product_goal": "为一款手持小风扇产品寻找差异化定位和竞争策略，分析市场机会点",
    "competitors": ["几素高速节能手持小风扇", "铁布衫手持风扇"],
    "comments": "用户反馈：几素风扇风力强劲但噪音略大，铁布衫风扇静音效果好但风力稍弱。消费者关注续航时间、便携性、噪音控制和价格。",
    "analysis_profile": {
      "strategy": "hybrid",
      "focus_attributes": ["风力", "噪音", "续航", "价格"]
    },
    "budget": {
      "max_sources": 8,
      "max_tokens": 12000,
      "max_cost_usd": 1,
      "timeout_seconds": 90
    }
  }'
```

### 8.2 带竞品 URL 的分析

```bash
curl -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "product_goal": "分析在线教育平台的竞争格局和差异化机会",
    "competitors": ["Coursera", "Udemy"],
    "competitor_urls": [
      {"competitor": "Coursera", "url": "https://www.coursera.org"},
      {"competitor": "Udemy", "url": "https://www.udemy.com"}
    ],
    "analysis_profile": {
      "strategy": "performance",
      "focus_attributes": ["课程质量", "价格", "认证"]
    }
  }'
```

### 8.3 自定义权重分析

```bash
curl -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "product_goal": "评估智能手表市场的进入策略和产品定位",
    "competitors": ["Apple Watch", "华为手表"],
    "analysis_profile": {
      "strategy": "custom",
      "dimension_weights": {
        "feature": 0.40,
        "pricing": 0.15,
        "user_feedback": 0.25,
        "positioning": 0.10,
        "risk": 0.10
      },
      "focus_attributes": ["健康监测", "续航", "生态兼容"]
    }
  }'
```

### 8.4 查询任务状态和决策包

```bash
# 获取任务详情（含决策包）
curl http://localhost:8000/api/tasks/{task_id}

# 获取决策包
curl http://localhost:8000/api/tasks/{task_id}/decision-pack

# 获取证据列表
curl http://localhost:8000/api/tasks/{task_id}/evidence

# 获取任务历史
curl http://localhost:8000/api/tasks/{task_id}/history
```

### 8.5 SSE 实时事件订阅

```javascript
// 前端 JavaScript 示例
const eventSource = new EventSource('http://localhost:8000/api/tasks/{task_id}/events/stream');

eventSource.onmessage = (event) => {
  if (event.data === '[DONE]') {
    eventSource.close();
    return;
  }
  const data = JSON.parse(event.data);
  console.log(`[${data.stage}] ${data.message}`);
};

eventSource.onerror = (error) => {
  console.error('SSE 连接错误:', error);
  eventSource.close();
};
```

### 8.6 人工干预

```bash
# 批准决策包
curl -X POST http://localhost:8000/api/tasks/{task_id}/interventions \
  -H "Content-Type: application/json" \
  -d '{"target": "decision", "action": "approve"}'

# 拒绝决策包
curl -X POST http://localhost:8000/api/tasks/{task_id}/interventions \
  -H "Content-Type: application/json" \
  -d '{"target": "decision", "action": "reject", "reason": "证据不足"}'

# 强制从 Writer 阶段重跑
curl -X POST http://localhost:8000/api/tasks/{task_id}/interventions \
  -H "Content-Type: application/json" \
  -d '{"target": "task", "action": "force_rerun", "stage": "writer"}'
```

### 8.7 Python SDK 示例

```python
import httpx

# 创建客户端
client = httpx.Client(base_url="http://localhost:8000")

# 提交任务
response = client.post("/api/tasks", json={
    "product_goal": "分析智能家居市场的竞争格局",
    "competitors": ["小米", "华为"],
    "analysis_profile": {
        "strategy": "hybrid",
        "focus_attributes": ["价格", "生态", "兼容性"]
    }
})
task = response.json()
task_id = task["id"]

# 轮询任务状态
import time
while True:
    task = client.get(f"/api/tasks/{task_id}").json()
    print(f"状态: {task['status']}")
    if task["status"] in ("completed", "failed", "cancelled"):
        break
    time.sleep(2)

# 获取决策包
if task.get("decision_pack"):
    pack = task["decision_pack"]
    print(f"决策包 v{pack['version']}")
    print(f"摘要: {pack['summary']}")
    for action in pack.get("positioning", []):
        print(f"  - {action['title']}: {action['recommendation']}")
```

---

## 9. 常见问题与解决方案

### 9.1 任务失败：`name 'planner' is not defined`

**原因**：workflow.py 中存在孤儿代码（缺少函数定义头的重复实现）

**解决方案**：已修复，确保 workflow.py 中只有一个 `planner` 函数定义。如遇到类似错误，检查是否有重复的函数定义。

### 9.2 任务状态为 `failed`，评分低于 0.6

**原因**：证据覆盖不足，Reviewer 评分未达标

**解决方案**：
1. 提供更多输入数据源（如竞品官网 URL）
2. 增加关注属性关键词以引导搜索
3. 提供用户评论以补充 user_feedback 维度
4. 配置 `SEARCH_API_KEY` 启用搜索补搜功能

### 9.3 LLM 未配置时行为异常

**原因**：`LLM_API_KEY` 未设置

**解决方案**：
- 配置 `.env` 文件中的 `LLM_API_KEY`
- 未配置时系统会降级：Writer 生成规则版决策包，Reviewer 仅规则校验，评论降级为关键词聚类

### 9.4 数据库连接失败

**原因**：PostgreSQL 未启动或配置错误

**解决方案**：
- 系统会自动降级为 SQLite（`data/ci_agent.db`）
- 如需使用 PostgreSQL，确保 `DB_USE_SQLITE=false` 并正确配置连接信息
- 使用 Docker Compose 可一键启动 PostgreSQL

### 9.5 前端无法连接后端

**原因**：CORS 或代理配置问题

**解决方案**：
- 开发模式：Vite 已配置 `/api` 代理到 `http://localhost:8000`
- 生产模式：后端 CORS 仅允许 `http://localhost:5173` 和 `http://127.0.0.1:5173`
- 自定义端口：修改 `VITE_API_BASE_URL` 环境变量和后端 CORS 配置

### 9.6 SSE 事件流不工作

**原因**：浏览器不支持 EventSource 或网络代理问题

**解决方案**：
- 系统已内置降级机制：SSE 超时 10 秒后自动切换为轮询模式
- 开发模式默认使用轮询（`forcePolling = true`）
- 可通过 `subscribeTaskEvents` 的 `forcePolling` 参数控制

### 9.7 Docker 构建失败

**原因**：网络问题或依赖版本冲突

**解决方案**：
```bash
# 清理 Docker 缓存重新构建
docker compose build --no-cache backend

# 查看构建日志
docker compose up --build 2>&1 | tee build.log
```

### 9.8 测试失败

**原因**：依赖缺失或环境配置问题

**解决方案**：
```bash
# 确保安装开发依赖
cd ci-agent/backend
pip install -e ".[dev]"

# 运行测试（排除集成测试）
python -m pytest -q

# 运行特定测试
python -m pytest tests/test_workflow.py::TestPlanner -v
```

---

## 10. 贡献指南

### 10.1 代码规范

#### Python 后端

- 遵循 PEP 8 规范
- 使用类型注解（`from __future__ import annotations`）
- 函数和类添加 docstring
- 使用 Pydantic v2 进行数据校验
- 测试覆盖核心逻辑

#### TypeScript 前端

- 使用 `<script setup lang="ts">` 语法
- strict 模式，禁止 `any` 类型
- 使用 Composition API
- API 类型通过 `npm run gen:api` 自动生成，禁止手动修改 `openapi.d.ts`

### 10.2 开发流程

```bash
# 1. Fork 仓库并克隆
git clone https://github.com/your-username/ci-agent.git
cd ci-agent

# 2. 创建功能分支
git checkout -b feature/your-feature-name

# 3. 安装依赖
cd ci-agent/backend && pip install -e ".[dev]"
cd ../frontend && npm install

# 4. 开发并测试
# 后端测试
cd ci-agent/backend && python -m pytest -q

# 前端类型检查
cd ci-agent/frontend && npm run build

# 5. 提交代码
git add .
git commit -m "feat: 简要描述你的改动"

# 6. 推送并创建 Pull Request
git push origin feature/your-feature-name
```

### 10.3 提交规范

使用 Conventional Commits 规范：

| 前缀 | 用途 |
|------|------|
| `feat:` | 新功能 |
| `fix:` | Bug 修复 |
| `docs:` | 文档更新 |
| `refactor:` | 代码重构 |
| `test:` | 测试相关 |
| `chore:` | 构建/工具相关 |

### 10.4 测试要求

- 新增功能必须附带测试
- 测试文件放在 `ci-agent/backend/tests/` 目录
- 使用 pytest 框架
- 集成测试使用 `@pytest.mark.integration` 标记
- 确保所有测试通过：`python -m pytest -q`

---

## 11. 许可证信息

本项目采用 **MIT License** 开源协议。

```
MIT License

Copyright (c) 2026 CI-Agent Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## 附录

### A. 项目仓库

- **GitHub**: [https://github.com/Mdaszly/ci-agent](https://github.com/Mdaszly/ci-agent)

### B. 推荐阅读顺序

如果你是第一次接触这个仓库，建议按以下顺序阅读源码：

1. `ci-agent/frontend/src/App.vue` — 前端界面，理解用户交互
2. `ci-agent/backend/app/api/routes.py` — API 端点，理解接口契约
3. `ci-agent/backend/app/models/schemas.py` — 数据模型，理解领域建模
4. `ci-agent/backend/app/worker/workflow.py` — 工作流引擎，理解核心逻辑
5. `ci-agent/backend/app/services/decision_memory.py` — 决策记忆，理解回流机制
6. `ci-agent/backend/tests/test_workflow.py` — 测试用例，理解预期行为

### C. 关键设计决策

| 决策 | 理由 |
|------|------|
| Evidence-first 架构 | 确保决策可追溯、可辩护，避免 LLM 幻觉 |
| 双存储模式（内存+DB） | 平衡开发便利性和生产可靠性 |
| LLM 降级策略 | 无 LLM 时仍可运行（规则版），降低使用门槛 |
| 限制 1 轮修复 | 避免无限循环，保证任务可收敛 |
| lexical 召回优先 | 零依赖，快速验证，预留向量扩展 |
