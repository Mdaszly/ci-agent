# 竞品情报 Agent MVP

这是一个证据优先的竞品情报与产品决策 Agent 原型。首版目标是跑通 `URL/文本/单图元数据 -> Evidence -> Coverage Gate -> Decision Pack -> Reviewer` 的最小闭环。

## 目录

- `backend`：FastAPI + Pydantic + LangGraph worker。
- `frontend`：Vue3 + Vite + Element Plus dashboard。
- `infra`：Docker Compose，本地包含 backend、frontend、PostgreSQL。

## 本地启动

### 后端

```bash
cd ci-agent/backend
pip install -e ".[dev]"

# 创建 .env 文件并配置环境变量

cp .env.example .env
# 编辑 .env，配置 LLM_API_KEY
# LLM_API_KEY=your-api-key

uvicorn app.main:app --reload
```

### 前端

```bash
cd ci-agent/frontend
npm install
npm run dev
```

### Docker

```bash
cd ci-agent/infra
docker compose up --build
```

## 环境变量配置

在 `backend/.env` 文件中配置以下变量：

```env
# LLM 配置（必须配置才能生成真实决策包）
LLM_API_KEY=your-api-key
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen-plus
LLM_TIMEOUT_SECONDS=60
LLM_MAX_RETRIES=2
LLM_TEMPERATURE=0.2
LLM_PROVIDER=dashscope

# 数据库配置（可选，不配置则使用内存存储）
DB_HOST=localhost
DB_PORT=5432
DB_USERNAME=postgres
DB_PASSWORD=postgres
DB_DATABASE=ci_agent

# 搜索配置（可选，不配置则跳过自动搜索补证据）
SEARCH_API_KEY=
SEARCH_PROVIDER=serpapi
SEARCH_MAX_RESULTS=5
```

## 真实能力 vs Mock 能力


| 能力            | 状态   | 说明                                 |
| ------------- | ---- | ---------------------------------- |
| **LLM 决策包生成** | 真实   | 配置 `LLM_API_KEY` 后调用阿里云百炼          |
| **LLM 智能复核**  | 真实   | 配置 `LLM_API_KEY` 后调用大模型复核          |
| **网页抓取**      | 真实   | 使用 `URLAdapter` 真实抓取网页内容           |
| **评论聚类**      | 真实   | 使用 `CommentAdapter` 将长评论拆成多条用户反馈 Evidence |
| **冲突裁决**      | 真实   | `ConflictResolver` 会检测价格/抓取状态/有无对立冲突 |
| **证据评分**      | 真实   | 使用 `EvidenceScorer` 计算可信度/相关性/质量评分 |
| **规则校验**      | 真实   | Evidence ID 校验、维度匹配校验              |
| **数据库持久化**    | 条件真实 | PostgreSQL 可用时使用，否则自动降级内存存储        |
| **搜索补证据**    | 条件真实 | 配置 `SEARCH_API_KEY` 且未提供 URL 时自动搜索补证据 |
| **图片 OCR**    | Mock | 仅存储文件名，无视觉分析                       |
| **评论爬取**      | Mock | 仍需手动粘贴评论内容，不做平台自动爬取                |


## 首版能力

- **输入安全层**：URL 协议校验、私网地址阻断、图片 MIME 和大小限制、任务来源预算。
- **结构化证据**：`Evidence`、`Claim`、`Conflict`、`CoverageGateResult`、`DecisionPack`、`ReviewScore`。
- **工作流**：`Planner -> Research -> EvidenceNormalizer -> CoverageGate -> ConflictResolver -> Writer -> Reviewer`。
- **Research 阶段**：支持 URL 抓取、评论聚类；当未提供 URL 且配置 `SEARCH_API_KEY` 时，可自动搜索补证据。
- **前端展示**：任务表单、阶段进度、证据覆盖、质量分、冲突结果、决策包和 Reviewer 分数。
- **LLM 集成**：支持真实大模型调用，输出自动校验 Evidence ID。

## 工作流说明

```
用户提交任务
    ↓
Planner (规划任务)
    ↓
Research (网页抓取 / 评论聚类 / 可选搜索补证据)
    ↓
EvidenceNormalizer (证据评分)
    ↓
CoverageGate (维度覆盖检查 + 质量门)
    ↓
ConflictResolver (冲突检测与裁决)
    ↓
Writer (生成决策包) → 需要 LLM_API_KEY，否则任务失败
    ↓
Reviewer (复核评分)
    ↓
任务完成/失败
```

## 手动测试用例

```text
产品目标：
为一个 AI 简历优化工具寻找差异化定位和首版 MVP 功能优先级

竞品：
ResumeWorded, Kickresume

URL：
https://www.resumeworded.com/
https://www.kickresume.com/en/pricing/

评论：
用户常抱怨模板同质化、定价偏高、中文场景支持不足，希望获得更具体的求职反馈。

图片文件名：
competitor-homepage.png
```

**预期结果**：

- 配置 `LLM_API_KEY`：Writer 真实生成决策包，内容基于证据，不编造事实。
- 未配置 `LLM_API_KEY`：任务失败，前端明确提示需配置 API key。
- URL 真实抓取：Evidence 包含页面标题/正文/价格片段。
- 长评论会被拆成多条 `user_feedback` Evidence。
- 若存在矛盾证据，系统会输出 `conflicts` 裁决结果。
- Coverage 未达标时：Reviewer 降低评分并提示补证据。
- 伪造 `evidence_id`：LLM 输出被拒绝，任务失败。

## 测试

### 默认离线测试

```bash
cd ci-agent/backend
python -m pytest -q
```

默认会跳过 `integration` 标记的外网/真实 LLM 测试。

### 集成测试

```bash
cd ci-agent/backend
python -m pytest -q -m integration
```

适用于已经配置 `LLM_API_KEY`，并且当前环境可访问外网的场景。

## Docker 5 分钟体验

```bash
cd ci-agent/backend
cp .env.example .env
```

编辑 `.env`：

- 必填：`LLM_API_KEY`
- 可选：`SEARCH_API_KEY`

然后执行：

```bash
cd ../infra
docker compose up --build
```

启动后：

1. 打开前端页面
2. 点击“加载 Demo 用例”
3. 点击“运行分析”
4. 查看 Evidence、Coverage、Conflict、Decision Pack 和 Reviewer Score

## 当前限制

- **图片分析**：仅存储文件名元数据，无 OCR/视觉模型。
- **搜索能力**：仅在未提供 URL 且配置 `SEARCH_API_KEY` 时自动补证据，不做复杂搜索编排。
- **评论来源**：需用户手动粘贴评论，无平台自动爬取。
- **视频解析**：不在首版范围。
- **多租户**：不在首版范围。

## 代码结构

``` 
backend/
├── app/
│   ├── core/config.py      # 配置管理
│   ├── services/
│   │   ├── llm.py          # LLM 客户端
│   │   ├── url_adapter.py  # 网页抓取
│   │   ├── comment_adapter.py  # 评论聚类
│   │   ├── search_adapter.py   # 搜索补证据
│   │   ├── evidence_scorer.py  # 证据评分
│   │   └── store.py        # 数据存储
│   ├── worker/workflow.py  # 工作流定义
│   ├── db/models.py        # 数据库模型
│   ├── models/schemas.py   # Pydantic 模型
│   └── main.py             # FastAPI 入口
└── tests/
    ├── test_api.py         # API 测试
    ├── test_security.py    # 安全测试
    ├── test_llm.py         # LLM 测试
    ├── test_writer_validation.py  # Writer 校验测试
    ├── test_url_adapter.py  # URL 适配器测试
    ├── test_comment_adapter.py  # 评论聚类测试
    ├── test_conflict_resolver.py  # 冲突裁决测试
    ├── test_search_adapter.py  # 搜索适配器测试
    └── test_workflow.py  # 工作流测试
```

## 验收标准

1. ✅ 配置 `LLM_API_KEY` 后真实调用大模型
2. ✅ 未配置 `LLM_API_KEY` 时任务失败，明确提示用户
3. ✅ LLM 输出必须校验 `evidence_ids` 是否存在
4. ✅ LLM 输出必须通过 Pydantic 校验
5. ✅ Reviewer 识别 Coverage 未达标
6. ✅ 日志/响应不泄露 API key
7. ✅ pytest 测试通过

