<script setup lang="ts">
import { computed, onUnmounted, reactive, ref, watch } from "vue";
import { ElMessage } from "element-plus";

import {
  createTask,
  getTask,
  subscribeTaskEvents,
  type RichTaskCreateRequest,
  type RichTaskRecord,
  type SubscribeResult,
} from "./services/api";

type AnalysisStrategy = "hybrid" | "cost_leadership" | "performance" | "custom";
type DimensionKey = "feature" | "pricing" | "user_feedback" | "positioning" | "risk";
type AgentStageKey = "planner" | "research" | "scorer" | "resolver" | "writer" | "reviewer" | "publish";

const analysisStrategyOptions: Array<{ value: AnalysisStrategy; label: string; desc: string }> = [
  { value: "hybrid", label: "性价比导向", desc: "均衡比较参数与价格，适合常规竞品分析" },
  { value: "cost_leadership", label: "定价优势", desc: "优先看价格、促销和同价位替代关系" },
  { value: "performance", label: "产品力优势", desc: "优先看风力、噪音、续航等核心能力" },
  { value: "custom", label: "自定义权重", desc: "手动配置五维权重，适合精细调参" },
];

const focusOptions = ["风力", "噪音", "续航", "价格", "便携", "静音", "档位", "充电"];

const dimensionLabels: Record<DimensionKey, string> = {
  feature: "产品特性",
  pricing: "定价",
  user_feedback: "用户反馈",
  positioning: "市场定位",
  risk: "风险/短板",
};

const defaultWeights: Record<DimensionKey, number> = {
  feature: 30,
  pricing: 25,
  user_feedback: 25,
  positioning: 10,
  risk: 10,
};

const strategyDefaults: Record<Exclude<AnalysisStrategy, "custom">, { focus: string[]; hint: string }> = {
  hybrid: { focus: ["风力", "噪音", "续航", "价格"], hint: "同价位综合最优，兼顾体验与价格" },
  cost_leadership: { focus: ["价格", "续航", "便携"], hint: "把成本与价格优势放在首位" },
  performance: { focus: ["风力", "噪音", "续航"], hint: "把性能指标拉开差异" },
};

const stageRail: Array<{ key: AgentStageKey; label: string; note: string }> = [
  { key: "planner", label: "规划", note: "拆解问题与路径" },
  { key: "research", label: "调研", note: "并行收集证据" },
  { key: "scorer", label: "评分", note: "证据打分与筛选" },
  { key: "resolver", label: "裁决", note: "处理冲突与缺口" },
  { key: "writer", label: "写作", note: "汇总为决策稿" },
  { key: "reviewer", label: "复核", note: "Reviewer 纠偏" },
  { key: "publish", label: "发布", note: "输出最终结果" },
];

const form = reactive({
  productGoal: "为一款手持小风扇产品寻找差异化定位和竞争策略，分析市场机会点",
  competitorsText: "几素高速节能手持小风扇\n铁布衫手持风扇",
  comments: "用户反馈：几素风扇风力强劲但噪音略大，铁布衫风扇静音效果好但风力稍弱。消费者关注续航时间、便携性、噪音控制和价格。",
  analysisStrategy: "hybrid" as AnalysisStrategy,
  focusAttributesText: "风力, 噪音, 续航, 价格",
  ourProductHints: "",
  enableMemoryRecall: true,
  maxRepairRounds: 2,
  autoPublishFinal: true,
  customWeights: { ...defaultWeights },
});

const loading = ref(false);
const analysisRunning = ref(false);
const currentTask = ref<RichTaskRecord | null>(null);
const animationTick = ref(0);
const eventTrail = ref<Array<{ stage: AgentStageKey; title: string; detail: string }>>([]);
let taskSubscription: SubscribeResult | null = null;

const competitors = computed(() =>
  form.competitorsText
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean),
);

const focusAttributes = computed(() =>
  form.focusAttributesText
    .split(/[，,\s]+/)
    .map((item) => item.trim())
    .filter(Boolean),
);

const analysisProfile = computed(() => {
  const base = {
    strategy: form.analysisStrategy,
    focus_attributes: focusAttributes.value,
    our_product_hints: form.ourProductHints.trim() || undefined,
  };

  if (form.analysisStrategy === "custom") {
    const total = Object.values(form.customWeights).reduce((sum, value) => sum + value, 0) || 100;
    const normalizedWeights = Object.fromEntries(
      Object.entries(form.customWeights).map(([key, value]) => [key, Number((value / total).toFixed(3))]),
    ) as Record<DimensionKey, number>;

    return {
      ...base,
      dimension_weights: normalizedWeights,
    };
  }

  return base;
});

const currentPackVersion = computed(() => currentTask.value?.memory_state?.current_pack_version ?? null);
const currentIteration = computed(() => currentTask.value?.memory_state?.iteration ?? 0);
const maxIterations = computed(() => currentTask.value?.memory_state?.max_iterations ?? form.maxRepairRounds);
const reflowStatusLabel = computed(() => {
  const status = currentTask.value?.memory_state?.last_reviewer_status;
  if (!status) return "未进入回流";
  if (status === "approved") return "Reviewer 通过";
  if (status === "rejected") return "Reviewer 拒绝";
  if (status === "needs_retry") return "等待修复";
  return status;
});
const recallCount = computed(() => currentTask.value?.memory_state?.last_recall_count ?? currentTask.value?.memory_items?.length ?? 0);
const reflowHistory = computed(() => currentTask.value?.decision_history ?? []);
const recentRecallItems = computed(() => currentTask.value?.memory_items ?? []);
const reviewHistory = computed(() => currentTask.value?.review_history ?? []);
const taskEvents = computed(() => currentTask.value?.events ?? []);
const terminalReason = computed(() => {
  const status = currentTask.value?.status;
  const memory = currentTask.value?.memory_state;
  const review = currentTask.value?.review;
  const retries = memory?.iteration ?? 0;
  const maxRetries = memory?.max_iterations ?? form.maxRepairRounds;

  if (!currentTask.value) return "等待提交任务";
  if (status === "queued") return "任务已入队，等待执行";
  if (status === "running") {
    if (memory?.retry_reason) return `当前卡点：${memory.retry_reason}`;
    if (review?.hallucination_risk === "high") return "Reviewer 检测到高幻觉风险，正在回流修复";
    return "任务仍在运行中";
  }
  if (status === "completed") {
    return review?.score != null && review.score >= 0.8
      ? "Reviewer 达标并已发布"
      : "任务已完成，结果已收敛";
  }
  if (status === "failed") {
    if (memory?.retry_reason) return memory.retry_reason;
    if (review?.hallucination_risk === "high") return "规则校验或引用核验未通过";
    if (retries >= maxRetries) return "已达到最大重试轮次，任务停止";
    return "任务失败";
  }
  if (status === "cancelled") return "任务已取消";
  return "状态未知";
});
const terminalBadge = computed(() => {
  const status = currentTask.value?.status;
  if (status === "completed") return "已收口";
  if (status === "failed") return "已失败";
  if (status === "cancelled") return "已取消";
  if (status === "running") return "运行中";
  if (status === "queued") return "排队中";
  return "未开始";
});
const activeStage = computed<AgentStageKey>(() => {
  const latestEvent = taskEvents.value[taskEvents.value.length - 1];
  const raw = (currentTask.value?.memory_state?.stage ?? latestEvent?.stage ?? "").toLowerCase();
  return normalizeStage(raw) ?? (loading.value ? "planner" : "publish");
});
const stageIndex = computed(() => stageRail.findIndex((item) => item.key === activeStage.value));
const stageProgress = computed(() => Math.min(100, Math.max(0, ((Math.max(stageIndex.value, 0) + 1) / stageRail.length) * 100)));
const activeStageMeta = computed(() => stageRail[Math.max(stageIndex.value, 0)] ?? stageRail[0]);
const stageHint = computed(() => {
  const map: Record<AgentStageKey, string> = {
    planner: "任务被拆成可执行子问题，准备进入并行调研。",
    research: "多个证据源正在并行收集，优先补齐关键维度。",
    scorer: "对收集到的证据做可信度和相关性打分。",
    resolver: "处理冲突证据，保留可辩护结论。",
    writer: "将证据与判断汇总成可读决策稿。",
    reviewer: "Reviewer 在检查逻辑、覆盖率和风险。",
    publish: "已接近最终输出，准备定稿或自动发布。",
  };
  return map[activeStage.value];
});

watch(
  () => taskEvents.value.length,
  (next, prev) => {
    if (next === prev) return;
    animationTick.value += 1;
    const latest = taskEvents.value[taskEvents.value.length - 1];
    if (!latest) return;
    const stage = normalizeStage(latest.stage);
    if (!stage) return;
    eventTrail.value = [
      {
        stage,
        title: latest.stage,
        detail: latest.message,
      },
      ...eventTrail.value,
    ].slice(0, 6);
  },
);

watch(
  () => currentTask.value?.id,
  () => {
    if (!currentTask.value?.id) return;
    if (taskSubscription) {
      taskSubscription.unsubscribe();
      taskSubscription = null;
    }

    const seedEvents = currentTask.value.events ?? [];
    eventTrail.value = seedEvents
      .slice(-6)
      .reverse()
      .map((event) => {
        const stage = normalizeStage(event.stage) ?? "planner";
        return {
          stage,
          title: event.stage,
          detail: event.message,
        };
      });

    analysisRunning.value = true;
    taskSubscription = subscribeTaskEvents(
      currentTask.value.id,
      {
        onEvent: (event) => {
          if (!currentTask.value) return;
          currentTask.value = {
            ...currentTask.value,
            events: [...(currentTask.value.events ?? []), event],
          };
        },
        onComplete: async () => {
          if (!currentTask.value?.id) return;
          try {
            currentTask.value = await getTask(currentTask.value.id);
          } catch (error) {
            ElMessage.warning(error instanceof Error ? error.message : "任务完成，但刷新结果失败");
          } finally {
            analysisRunning.value = false;
          }
        },
        onError: (error) => {
          analysisRunning.value = false;
          ElMessage.error(error.message);
        },
      },
      { forcePolling: true, pollingInterval: 1200 },
    );
  },
);

function normalizeStage(input: string): AgentStageKey | null {
  const value = input.toLowerCase();
  if (value.includes("plan")) return "planner";
  if (value.includes("research") || value.includes("search") || value.includes("evidence")) return "research";
  if (value.includes("score")) return "scorer";
  if (value.includes("conflict") || value.includes("resolve") || value.includes("resolver") || value.includes("merge")) return "resolver";
  if (value.includes("write")) return "writer";
  if (value.includes("review")) return "reviewer";
  if (value.includes("publish") || value.includes("final") || value.includes("done")) return "publish";
  return null;
}

function setPresetStrategy(strategy: AnalysisStrategy) {
  form.analysisStrategy = strategy;
}

function setFocusAttributes(value: string[]) {
  form.focusAttributesText = value.join(", ");
}

function updateWeight(key: DimensionKey, value: number) {
  form.customWeights[key] = value;
}

function restoreDefaultWeights() {
  form.customWeights = { ...defaultWeights };
}

function stageClasses(stage: AgentStageKey, index: number) {
  return {
    active: index === stageIndex.value,
    done: index < stageIndex.value,
    pending: index > stageIndex.value,
    pulse: index === stageIndex.value && loading.value,
    [stage]: true,
  };
}

async function submitTask() {
  const normalizedProductGoal = form.productGoal.trim();
  const normalizedCompetitors = competitors.value;
  const normalizedFocusAttributes = focusAttributes.value;

  if (normalizedProductGoal.length < 8) {
    ElMessage.warning("产品目标至少需要 8 个字符");
    return;
  }

  if (normalizedCompetitors.length === 0) {
    ElMessage.warning("至少需要填写一个竞品名称");
    return;
  }

  loading.value = true;
  analysisRunning.value = true;
  taskSubscription?.unsubscribe();
  taskSubscription = null;
  currentTask.value = null;
  eventTrail.value = [];

  try {
    const payload: RichTaskCreateRequest = {
      product_goal: normalizedProductGoal,
      competitors: normalizedCompetitors,
      urls: [],
      competitor_urls: [],
      comments: form.comments.trim() || undefined,
      image_names: [],
      budget: {
        max_sources: 8,
        max_tokens: 12000,
        max_cost_usd: 1,
        timeout_seconds: 90,
      },
      analysis_profile: {
        ...analysisProfile.value,
        focus_attributes: normalizedFocusAttributes,
      } as RichTaskCreateRequest["analysis_profile"],
      reflow_controls: {
        enable_memory_recall: form.enableMemoryRecall,
        max_repair_rounds: form.maxRepairRounds,
        auto_publish_final: form.autoPublishFinal,
      },
    };

    const createdTask = await createTask(payload);
    currentTask.value = createdTask;
    ElMessage.success("任务已提交，正在跟踪分析过程");
  } catch (error) {
    analysisRunning.value = false;
    ElMessage.error(error instanceof Error ? error.message : "提交失败");
  } finally {
    loading.value = false;
  }
}

function resetForm() {
  form.productGoal = "为一款手持小风扇产品寻找差异化定位和竞争策略，分析市场机会点";
  form.competitorsText = "几素高速节能手持小风扇\n铁布衫手持风扇";
  form.comments = "用户反馈：几素风扇风力强劲但噪音略大，铁布衫风扇静音效果好但风力稍弱。消费者关注续航时间、便携性、噪音控制和价格。";
  form.analysisStrategy = "hybrid";
  form.focusAttributesText = "风力, 噪音, 续航, 价格";
  form.ourProductHints = "";
  form.enableMemoryRecall = true;
  form.maxRepairRounds = 2;
  form.autoPublishFinal = true;
  restoreDefaultWeights();
  eventTrail.value = [];
}

onUnmounted(() => {
  taskSubscription?.unsubscribe();
  taskSubscription = null;
});
</script>

<template>
  <div class="app-shell">
    <section class="hero">
      <div class="hero-copy-block">
        <p class="eyebrow">Evidence-First Agent</p>
        <h1>竞品情报决策工作台</h1>
        <p class="hero-copy">把分析策略、证据流转和复核闭环放在一屏里，先看得懂，再跑得稳。</p>
      </div>
      <div class="hero-meta">
        <span>流转节点 7</span>
        <span>输入类型 3</span>
        <span>决策产物 2</span>
      </div>
    </section>

    <section class="grid">
      <article class="card input-card">
        <div class="card-head">
          <h2>任务输入</h2>
          <button type="button" class="ghost-btn" @click="resetForm">重置</button>
        </div>

        <label class="field">
          <span>产品目标</span>
          <textarea id="product-goal" name="product_goal" v-model="form.productGoal" rows="3" />
        </label>

        <label class="field">
          <span>竞品列表</span>
          <textarea id="competitors" name="competitors" v-model="form.competitorsText" rows="3" />
        </label>

        <label class="field">
          <span>用户反馈 / 评论</span>
          <textarea id="comments" name="comments" v-model="form.comments" rows="4" />
        </label>

        <div class="strategy-panel">
          <div class="strategy-panel-head">
            <span>分析策略</span>
            <span class="strategy-badge">{{ analysisStrategyOptions.find((item) => item.value === form.analysisStrategy)?.label }}</span>
          </div>
          <div class="strategy-grid">
            <button
              v-for="option in analysisStrategyOptions"
              :key="option.value"
              type="button"
              class="strategy-card"
              :class="{ active: form.analysisStrategy === option.value }"
              @click="setPresetStrategy(option.value)"
            >
              <strong>{{ option.label }}</strong>
              <span>{{ option.desc }}</span>
            </button>
          </div>
        </div>

        <div class="field">
          <span>关注属性</span>
          <div class="chip-row">
            <button
              v-for="option in focusOptions"
              :key="option"
              type="button"
              class="chip"
              :class="{ active: focusAttributes.includes(option) }"
              @click="setFocusAttributes(focusAttributes.includes(option) ? focusAttributes.filter((item) => item !== option) : [...focusAttributes, option])"
            >
              {{ option }}
            </button>
          </div>
          <input id="focus-attributes" name="focus_attributes" v-model="form.focusAttributesText" type="text" placeholder="支持手动编辑，逗号分隔" />
        </div>

        <label class="field">
          <span>我方产品提示</span>
          <textarea id="product-hints" name="our_product_hints" v-model="form.ourProductHints" rows="3" placeholder="例如：我方成本可控、风道设计更好、外观更轻薄" />
        </label>

        <div class="toggle-row">
          <label><input id="enable-memory-recall" name="enable_memory_recall" v-model="form.enableMemoryRecall" type="checkbox" /> 启用记忆回流</label>
          <label><input id="auto-publish-final" name="auto_publish_final" v-model="form.autoPublishFinal" type="checkbox" /> 自动发布 final</label>
        </div>

        <label class="field inline">
          <span>最大修复轮次</span>
          <input id="max-repair-rounds" name="max_repair_rounds" v-model.number="form.maxRepairRounds" type="number" min="1" max="8" />
        </label>

        <div v-if="form.analysisStrategy === 'custom'" class="weights-panel">
          <div class="weights-head">
            <span>自定义权重</span>
            <button type="button" class="link-btn" @click="restoreDefaultWeights">恢复默认</button>
          </div>
          <div v-for="(label, key) in dimensionLabels" :key="key" class="weight-row">
            <div class="weight-row-head">
              <span>{{ label }}</span>
              <strong>{{ form.customWeights[key as DimensionKey] }}%</strong>
            </div>
              <input
              :id="`dimension-${key as DimensionKey}`"
              :name="`dimension_${key as DimensionKey}`"
              :value="form.customWeights[key as DimensionKey]"
              type="range"
              min="0"
              max="100"
              step="5"
              @input="updateWeight(key as DimensionKey, Number(($event.target as HTMLInputElement).value))"
            />
          </div>
        </div>

        <div class="action-row">
          <button type="button" class="primary-btn" :disabled="loading" @click="submitTask">
            {{ loading ? '提交中...' : '提交分析任务' }}
          </button>
        </div>
      </article>

      <article class="card status-card">
        <div class="card-head">
          <h2>Agent 运作流程</h2>
          <span class="status-pill" :class="`status-${activeStage}`">{{ activeStageMeta.label }}</span>
        </div>

        <div class="workflow-stage-board">
          <div class="workflow-rail">
            <div
              v-for="(stage, index) in stageRail"
              :key="stage.key"
              class="workflow-node"
              :class="stageClasses(stage.key, index)"
            >
              <div class="node-dot">
                <span />
              </div>
              <div class="node-text">
                <strong>{{ stage.label }}</strong>
                <p>{{ stage.note }}</p>
              </div>
            </div>
            <div class="workflow-track">
              <div class="workflow-track-fill" :style="{ width: `${stageProgress}%` }" />
            </div>
          </div>

          <div class="workflow-detail" :key="animationTick">
            <p class="workflow-title">{{ stageHint }}</p>
            <div class="workflow-metrics">
              <div>
                <span>当前版本</span>
                <strong>v{{ currentPackVersion ?? '-' }}</strong>
              </div>
              <div>
                <span>回流轮次</span>
                <strong>{{ currentIteration }}/{{ maxIterations }}</strong>
              </div>
              <div>
                <span>召回块</span>
                <strong>{{ recallCount }}</strong>
              </div>
              <div>
                <span>Reviewer</span>
                <strong>{{ reflowStatusLabel }}</strong>
              </div>
            </div>
            <div class="terminal-box">
              <span class="terminal-badge">{{ terminalBadge }}</span>
              <strong>{{ terminalReason }}</strong>
            </div>
            <div class="flow-preview">
              <div
                v-for="(item, index) in eventTrail.length
                  ? eventTrail
                  : stageRail.slice(0, Math.max(stageIndex + 1, 1)).map((stage) => ({ stage: stage.key, title: stage.label, detail: stage.note }))"
                :key="`${item.stage}-${index}-${animationTick}`"
                class="flow-step"
                :class="item.stage"
              >
                <span class="flow-step-badge">{{ index + 1 }}</span>
                <div>
                  <strong>{{ item.title }}</strong>
                  <p>{{ item.detail }}</p>
                </div>
              </div>
            </div>
          </div>
        </div>

        <template v-if="currentTask">
          <div class="mini-list">
            <p>决策历史：{{ reflowHistory.length }}</p>
            <p>记忆条目：{{ recentRecallItems.length }}</p>
            <p>Review 记录：{{ reviewHistory.length }}</p>
            <p>事件数：{{ taskEvents.length }}</p>
            <p v-if="currentTask.memory_state?.last_recall_summary">最近召回：{{ currentTask.memory_state.last_recall_summary }}</p>
            <p v-if="currentTask.memory_state?.retry_reason">终止/回流原因：{{ currentTask.memory_state.retry_reason }}</p>
          </div>
        </template>
        <template v-else>
          <p class="empty-state">暂无任务，提交后会显示任务结果。</p>
        </template>
      </article>
    </section>

    <section class="card summary-card">
      <h2>当前输入摘要</h2>
      <div class="summary-grid">
        <p>竞品数量：{{ competitors.length }}</p>
        <p>关注属性：{{ focusAttributes.join('、') || '未设置' }}</p>
        <p>回流策略：{{ form.enableMemoryRecall ? '开启' : '关闭' }}</p>
        <p>终止条件：{{ terminalReason }}</p>
      </div>
    </section>
  </div>
</template>

<style scoped>
:global(body) {
  margin: 0;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background:
    radial-gradient(circle at top left, rgba(59, 130, 246, 0.16), transparent 32%),
    linear-gradient(180deg, #f8fbff 0%, #eef4ff 100%);
  color: #0f172a;
}

.app-shell {
  min-height: 100vh;
  padding: 28px;
  display: grid;
  gap: 20px;
}

.hero {
  display: flex;
  justify-content: space-between;
  gap: 24px;
  padding: 24px;
  border-radius: 24px;
  background: rgba(15, 23, 42, 0.92);
  color: #fff;
}

.hero-copy-block h1,
.card h2 {
  margin: 0;
}

.eyebrow {
  margin: 0 0 8px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  font-size: 12px;
  opacity: 0.72;
}

.hero-copy {
  margin: 12px 0 0;
  max-width: 56ch;
  line-height: 1.7;
  opacity: 0.88;
}

.hero-meta {
  display: grid;
  gap: 10px;
  align-content: start;
  min-width: 180px;
}

.hero-meta span,
.status-pill,
.strategy-badge,
.chip,
.flow-step-badge {
  border-radius: 14px;
}

.hero-meta span {
  padding: 10px 12px;
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.1);
}

.grid {
  display: grid;
  grid-template-columns: 1.18fr 0.82fr;
  gap: 20px;
}

.card {
  padding: 20px;
  border-radius: 22px;
  background: rgba(255, 255, 255, 0.82);
  border: 1px solid rgba(148, 163, 184, 0.2);
  backdrop-filter: blur(18px);
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
  gap: 12px;
}

.field {
  display: grid;
  gap: 8px;
  margin-bottom: 14px;
  font-size: 14px;
}

.field > span,
.strategy-panel-head > span,
.weights-head > span,
.weight-row-head > span,
.workflow-metrics span,
.flow-step p,
.mini-list p,
.summary-grid p {
  color: #475569;
}

.field > span {
  font-weight: 600;
}

textarea,
input,
select {
  width: 100%;
  box-sizing: border-box;
  border-radius: 14px;
  border: 1px solid #cbd5e1;
  background: #fff;
  padding: 12px 14px;
  color: #0f172a;
  outline: none;
  transition: border-color 0.2s ease, transform 0.2s ease;
}

textarea:focus,
input:focus,
select:focus {
  border-color: #60a5fa;
  transform: translateY(-1px);
}

.strategy-panel,
.weights-panel,
.workflow-stage-board,
.mini-list,
.summary-card {
  border-radius: 18px;
  background: rgba(248, 250, 252, 0.92);
  border: 1px solid rgba(148, 163, 184, 0.14);
}

.strategy-panel,
.weights-panel {
  padding: 14px;
  margin-bottom: 14px;
}

.strategy-panel-head,
.weights-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 12px;
}

.strategy-badge {
  padding: 8px 10px;
  background: rgba(37, 99, 235, 0.08);
  color: #1d4ed8;
  font-size: 12px;
  font-weight: 700;
}

.strategy-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.strategy-card {
  padding: 12px;
  border: 1px solid #dbe4f0;
  border-radius: 16px;
  background: #fff;
  text-align: left;
  cursor: pointer;
  display: grid;
  gap: 6px;
}

.strategy-card strong {
  color: #0f172a;
}

.strategy-card span {
  color: #64748b;
  font-size: 13px;
  line-height: 1.5;
}

.strategy-card.active {
  border-color: #2563eb;
  background: rgba(37, 99, 235, 0.06);
}

.chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.chip {
  border: 1px solid #dbe4f0;
  background: #fff;
  color: #334155;
  padding: 8px 10px;
  cursor: pointer;
}

.chip.active {
  border-color: #2563eb;
  color: #1d4ed8;
  background: rgba(37, 99, 235, 0.08);
}

.toggle-row {
  display: flex;
  gap: 18px;
  flex-wrap: wrap;
  margin: 6px 0 16px;
  color: #334155;
}

.toggle-row label {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.inline {
  grid-template-columns: 1fr 140px;
  align-items: center;
}

.weight-row {
  display: grid;
  gap: 8px;
  margin-top: 10px;
}

.weight-row-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.weight-row input[type="range"] {
  padding: 0;
}

.action-row {
  display: flex;
  justify-content: flex-end;
  margin-top: 18px;
}

.primary-btn,
.ghost-btn,
.link-btn {
  border: 0;
  border-radius: 14px;
  padding: 12px 16px;
  font-weight: 700;
  cursor: pointer;
}

.primary-btn {
  background: linear-gradient(135deg, #2563eb, #7c3aed);
  color: #fff;
}

.primary-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.ghost-btn,
.link-btn {
  background: rgba(37, 99, 235, 0.08);
  color: #1d4ed8;
}

.workflow-stage-board {
  padding: 14px;
  display: grid;
  grid-template-columns: 1.1fr 0.9fr;
  gap: 16px;
  overflow: hidden;
}

.workflow-rail {
  position: relative;
  display: grid;
  gap: 12px;
  padding-bottom: 14px;
}

.workflow-track {
  position: absolute;
  left: 14px;
  right: 14px;
  bottom: 18px;
  height: 2px;
  background: rgba(148, 163, 184, 0.2);
  overflow: hidden;
}

.workflow-track-fill {
  height: 100%;
  background: #2563eb;
  transition: width 0.45s ease;
}

.workflow-node {
  display: grid;
  grid-template-columns: 28px 1fr;
  gap: 10px;
  align-items: center;
}

.node-dot {
  width: 18px;
  height: 18px;
  display: grid;
  place-items: center;
  border-radius: 999px;
  border: 1px solid #cbd5e1;
  background: #fff;
  margin-left: 5px;
}

.node-dot span {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: #cbd5e1;
}

.workflow-node.active .node-dot {
  border-color: #2563eb;
}

.workflow-node.active .node-dot span,
.workflow-node.done .node-dot span {
  background: #2563eb;
}

.workflow-node.done .node-dot {
  border-color: rgba(37, 99, 235, 0.5);
}

.workflow-node.pulse .node-dot span {
  animation: pulse 1s infinite ease-in-out;
}

.node-text strong {
  display: block;
  color: #0f172a;
}

.node-text p,
.workflow-title,
.empty-state {
  margin: 4px 0 0;
  color: #64748b;
}

.status-pill {
  padding: 8px 10px;
  color: #1d4ed8;
  background: rgba(37, 99, 235, 0.08);
}

.workflow-detail {
  display: grid;
  gap: 14px;
  align-content: start;
}

.workflow-title {
  line-height: 1.7;
}

.workflow-metrics {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.workflow-metrics > div {
  padding: 12px;
  border-radius: 16px;
  background: #fff;
  border: 1px solid #dbe4f0;
  display: grid;
  gap: 6px;
}

.workflow-metrics strong {
  font-size: 18px;
  color: #0f172a;
}

.flow-preview {
  display: grid;
  gap: 10px;
}

.flow-step {
  display: grid;
  grid-template-columns: 28px 1fr;
  gap: 10px;
  align-items: start;
  padding: 12px;
  border-radius: 16px;
  background: #fff;
  border: 1px solid #dbe4f0;
  animation: float-in 0.36s ease;
}

.flow-step-badge {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  background: rgba(37, 99, 235, 0.1);
  color: #1d4ed8;
  font-size: 12px;
  font-weight: 700;
}

.flow-step strong {
  color: #0f172a;
}

.flow-step p {
  margin: 4px 0 0;
  line-height: 1.5;
}

.mini-list,
.summary-card {
  margin-top: 14px;
  padding: 14px;
}

.mini-list {
  display: grid;
  gap: 10px;
}

.summary-card {
  display: grid;
  gap: 12px;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.summary-grid p,
.mini-list p {
  margin: 0;
}

@keyframes float-in {
  from {
    opacity: 0;
    transform: translateY(6px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes pulse {
  0%,
  100% {
    transform: scale(0.9);
    opacity: 0.55;
  }
  50% {
    transform: scale(1.2);
    opacity: 1;
  }
}

@media (max-width: 1040px) {
  .grid,
  .workflow-stage-board,
  .summary-grid,
  .strategy-grid {
    grid-template-columns: 1fr;
  }

  .hero {
    grid-template-columns: 1fr;
    display: grid;
  }
}
</style>