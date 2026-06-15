<script setup lang="ts">
import { computed, reactive, ref, onMounted, onUnmounted } from "vue";
import { ElMessage } from "element-plus";

import { createTask, subscribeTaskEvents, createIntervention, uploadFile, type TaskRecord, type TaskEvent, type SubscribeResult, type InterventionRequest, type TaskMetrics } from "./services/api";

interface CompetitorUrl {
  id: string;
  competitorId: string;
  url: string;
}

interface UploadedFile {
  id: string;
  file: File;
  name: string;
  size: number;
  type: string;
  preview?: string;
  status: "pending" | "uploading" | "success" | "error";
  progress: number;
  message?: string;
}

const form = reactive({
  productGoal: "为一款手持小风扇产品寻找差异化定位和竞争策略，分析市场机会点",
  competitors: ["几素高速节能手持小风扇", "铁布衫手持风扇"],
  urls: [
    { id: "url-init-1", competitorId: "0", url: "https://detail.tmall.com/item.htm?ali_refid=a3_430582_1006%3A1681222856%3AH%3AZCZHusXvprMAzLYF1tEbs62VHd6rIm3E%3Ac88fa1890a046fb718aaec93c909d43a&ali_trackid=282_c88fa1890a046fb718aaec93c909d43a&id=774257883187&mi_id=0000H5ZUil8E84AwzSqyGJnZjBSJTyBhCcF3G6R5La2Es9Q&mm_sceneid=1_0_4426213599_0&priceTId=214783f117815399342816618e1130&skuId=5779113845960&spm=a21n57.1.hoverItem.1&utparam=%7B%22aplus_abtest%22%3A%2299e6944681a5ba96bf0d2ea5ea319e31%22%7D&xxc=ad_ztc" },
    { id: "url-init-2", competitorId: "1", url: "https://detail.tmall.com/item.htm?ali_refid=a3_430582_1006%3A1681222856%3AH%3AZCZHusXvprMAzLYF1tEbs62VHd6rIm3E%3A6b217c1f4241fa54b46b091deac238ca&ali_trackid=282_6b217c1f4241fa54b46b091deac238ca&id=774257883187&mi_id=0000H5ZUil8E84AwzSqyGJnZjBSJTyBhCcF3G6R5La2Es9Q&mm_sceneid=1_0_4426213599_0&priceTId=214783f117815399342816618e1130&skuId=5779113845960&spm=a21n57.1.hoverItem.1&utparam=%7B%22aplus_abtest%22%3A%2299e6944681a5ba96bf0d2ea5ea319e31%22%7D&xxc=ad_ztc" },
  ],
  comments: "用户反馈：几素风扇风力强劲但噪音略大，铁布衫风扇静音效果好但风力稍弱。消费者关注续航时间、便携性、噪音控制和价格。夏天是销售旺季，学生和上班族是主要购买人群。",
  files: [] as UploadedFile[],
});

const demoForm: typeof form = {
  productGoal: "为一款手持小风扇产品寻找差异化定位和竞争策略，分析市场机会点",
  competitors: ["几素高速节能手持小风扇", "铁布衫手持风扇"],
  urls: [
    { id: "url-1", competitorId: "0", url: "https://detail.tmall.com/item.htm?ali_refid=a3_430582_1006%3A1681222856%3AH%3AZCZHusXvprMAzLYF1tEbs62VHd6rIm3E%3Ac88fa1890a046fb718aaec93c909d43a&ali_trackid=282_c88fa1890a046fb718aaec93c909d43a&id=774257883187&mi_id=0000H5ZUil8E84AwzSqyGJnZjBSJTyBhCcF3G6R5La2Es9Q&mm_sceneid=1_0_4426213599_0&priceTId=214783f117815399342816618e1130&skuId=5779113845960&spm=a21n57.1.hoverItem.1&utparam=%7B%22aplus_abtest%22%3A%2299e6944681a5ba96bf0d2ea5ea319e31%22%7D&xxc=ad_ztc" },
    { id: "url-2", competitorId: "1", url: "https://detail.tmall.com/item.htm?ali_refid=a3_430582_1006%3A1681222856%3AH%3AZCZHusXvprMAzLYF1tEbs62VHd6rIm3E%3A6b217c1f4241fa54b46b091deac238ca&ali_trackid=282_6b217c1f4241fa54b46b091deac238ca&id=774257883187&mi_id=0000H5ZUil8E84AwzSqyGJnZjBSJTyBhCcF3G6R5La2Es9Q&mm_sceneid=1_0_4426213599_0&priceTId=214783f117815399342816618e1130&skuId=5779113845960&spm=a21n57.1.hoverItem.1&utparam=%7B%22aplus_abtest%22%3A%2299e6944681a5ba96bf0d2ea5ea319e31%22%7D&xxc=ad_ztc" },
  ],
  comments:
    "用户反馈：几素风扇风力强劲但噪音略大，铁布衫风扇静音效果好但风力稍弱。消费者关注续航时间、便携性、噪音控制和价格。夏天是销售旺季，学生和上班族是主要购买人群。",
  files: [],
};

const loading = ref(false);
const currentTask = ref<TaskRecord | null>(null);
const isVisible = ref(false);
let subscribeResult: SubscribeResult | null = null;

// 干预面板状态
const interventionPanelVisible = ref(false);
const interventionLoading = ref(false);
const interventionForm = reactive({
  target: "task" as "task" | "evidence" | "decision",
  targetId: "",
  action: "approve" as "approve" | "reject" | "revise" | "force_rerun",
  reason: "",
});
const interventionReasonError = computed(() => {
  const len = interventionForm.reason.trim().length;
  if (len === 0) return "";
  if (len < 4) return "原因至少需要 4 个字符";
  if (len > 500) return "原因不能超过 500 个字符";
  return "";
});
const canSubmitIntervention = computed(() => {
  const len = interventionForm.reason.trim().length;
  return len >= 4 && len <= 500 && interventionForm.targetId.trim().length > 0;
});

const stageOrder = [
  "created",
  "planner",
  "research",
  "evidence",
  "coverage_gate",
  "conflict_resolver",
  "writer",
  "reviewer",
];

const stageNames: Record<string, string> = {
  created: "任务创建",
  planner: "规划分析",
  research: "竞品调研",
  evidence: "证据收集",
  coverage_gate: "覆盖验证",
  conflict_resolver: "冲突解决",
  writer: "决策生成",
  reviewer: "质量审查",
};

const competitors = computed(() => form.competitors.filter(Boolean));

const urls = computed(() => {
  const result: string[] = [];
  form.urls.forEach((urlItem) => {
    if (urlItem.url.trim()) {
      result.push(urlItem.url.trim());
    }
  });
  return result;
});

const imageNames = computed(() => {
  return form.files
    .filter((f) => f.status === "success" && f.type.startsWith("image/"))
    .map((f) => f.name);
});

function addCompetitor() {
  form.competitors.push("");
}

function removeCompetitor(index: number) {
  if (form.competitors.length > 1) {
    form.competitors.splice(index, 1);
    form.urls = form.urls.filter((url) => url.competitorId !== String(index));
  }
}

function addUrl() {
  form.urls.push({
    id: `url-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
    competitorId: form.competitors.length > 0 ? "0" : "",
    url: "",
  });
}

function removeUrl(index: number) {
  form.urls.splice(index, 1);
}

const MAX_FILE_SIZE = 10 * 1024 * 1024;
const ALLOWED_IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp"];
const ALLOWED_DOC_TYPES = ["application/pdf", "application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "text/plain", "text/markdown"];

interface EvidenceAnalysis {
  type: "image" | "document" | "url" | "comment";
  source: string;
  dimensions: string[];
  insights: string[];
}

const fileInputRef = ref<HTMLInputElement | null>(null);
const isDragging = ref(false);
const dragCounter = ref(0);
const analysisResults = ref<EvidenceAnalysis[]>([]);
const isAnalyzing = ref(false);

function validateFile(file: File): { valid: boolean; message?: string } {
  if (file.size > MAX_FILE_SIZE) {
    return { valid: false, message: "文件大小不能超过 10MB" };
  }
  
  const isImage = ALLOWED_IMAGE_TYPES.includes(file.type);
  const isDoc = ALLOWED_DOC_TYPES.includes(file.type);
  
  if (!isImage && !isDoc) {
    return { valid: false, message: `不支持的文件类型: ${file.type}。支持: 图片(png,jpeg,webp,gif,bmp)、文档(pdf,doc,docx,txt,md)` };
  }
  
  return { valid: true };
}

function handleFileSelect(event: Event) {
  const target = event.target as HTMLInputElement;
  const files = target.files;
  if (!files || files.length === 0) return;

  processFiles(Array.from(files));
  target.value = "";
}

function processFiles(files: File[]) {
  for (const file of files) {
    const validation = validateFile(file);
    if (!validation.valid) {
      ElMessage.error(validation.message);
      continue;
    }

    const uploadedFile: UploadedFile = {
      id: `file-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
      file,
      name: file.name,
      size: file.size,
      type: file.type,
      status: "pending",
      progress: 0,
    };

    if (file.type.startsWith("image/")) {
      const reader = new FileReader();
      reader.onload = (e) => {
        uploadedFile.preview = e.target?.result as string;
      };
      reader.readAsDataURL(file);
    }

    form.files.push(uploadedFile);
  }
}

function triggerFileSelect() {
  fileInputRef.value?.click();
}

function handleDragEnter(event: DragEvent) {
  event.preventDefault();
  event.stopPropagation();
  dragCounter.value++;
  isDragging.value = true;
}

function handleDragLeave(event: DragEvent) {
  event.preventDefault();
  event.stopPropagation();
  dragCounter.value--;
  if (dragCounter.value === 0) {
    isDragging.value = false;
  }
}

function handleDragOver(event: DragEvent) {
  event.preventDefault();
  event.stopPropagation();
}

function handleDrop(event: DragEvent) {
  event.preventDefault();
  event.stopPropagation();
  isDragging.value = false;
  dragCounter.value = 0;

  const files = event.dataTransfer?.files;
  if (!files || files.length === 0) return;

  processFiles(Array.from(files));
}

function handleFileRemove(index: number, uploadFile: UploadedFile) {
  form.files.splice(index, 1);
  
  if (uploadFile.status === "success") {
    analysisResults.value = analysisResults.value.filter(
      (r) => r.source !== uploadFile.name
    );
  }
}

async function uploadFiles() {
  const pendingFiles = form.files.filter((f) => f.status === "pending");
  if (pendingFiles.length === 0) {
    ElMessage.info("没有待上传的文件");
    return;
  }

  isAnalyzing.value = true;

  for (const uploadedFile of pendingFiles) {
    uploadedFile.status = "uploading";
    uploadedFile.progress = 0;

    try {
      const result = await uploadFile(uploadedFile.file);
      uploadedFile.status = "success";
      uploadedFile.progress = 100;
      uploadedFile.message = result.message;
      
      await analyzeFile(uploadedFile);
      
      ElMessage.success(`${uploadedFile.name} 上传并分析成功`);
    } catch (error) {
      uploadedFile.status = "error";
      uploadedFile.progress = 0;
      uploadedFile.message = error instanceof Error ? error.message : "上传失败";
      ElMessage.error(`${uploadedFile.name} 上传失败: ${uploadedFile.message}`);
    }
  }

  isAnalyzing.value = false;
}

async function analyzeFile(uploadedFile: UploadedFile) {
  const analysis: EvidenceAnalysis = {
    type: uploadedFile.type.startsWith("image/") ? "image" : "document",
    source: uploadedFile.name,
    dimensions: [],
    insights: [],
  };

  await new Promise(resolve => setTimeout(resolve, 800));

  if (analysis.type === "image") {
    analysis.dimensions = ["视觉设计", "品牌识别", "用户界面布局", "色彩方案"];
    analysis.insights = [
      "图片展示了竞品的主页设计风格",
      "可以分析其视觉层次和用户体验",
      "识别竞品的品牌色彩和排版风格",
    ];
  } else {
    analysis.dimensions = ["内容分析", "定价策略", "功能描述", "市场定位"];
    analysis.insights = [
      "文档包含竞品的产品说明和功能列表",
      "可以提取定价信息和套餐对比",
      "分析竞品的市场定位和差异化策略",
    ];
  }

  const existingIndex = analysisResults.value.findIndex(
    (r) => r.source === uploadedFile.name
  );
  if (existingIndex !== -1) {
    analysisResults.value[existingIndex] = analysis;
  } else {
    analysisResults.value.push(analysis);
  }
}

function clearAnalysis() {
  analysisResults.value = [];
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / (1024 * 1024)).toFixed(2) + " MB";
}

async function submitTask() {
  loading.value = true;
  currentTask.value = null;

  if (subscribeResult) {
    subscribeResult.unsubscribe();
    subscribeResult = null;
  }

  try {
    const task = await createTask({
      product_goal: form.productGoal,
      competitors: competitors.value,
      urls: urls.value,
      comments: form.comments || undefined,
      image_names: imageNames.value,
      budget: {
        max_sources: 8,
        max_tokens: 12000,
        max_cost_usd: 1,
        timeout_seconds: 90,
      },
    });

    currentTask.value = task;

    if (!task.id) {
      throw new Error("任务创建成功但未返回任务 ID");
    }

    subscribeResult = subscribeTaskEvents(task.id, {
      onEvent: (event: TaskEvent) => {
        if (currentTask.value) {
          const events = currentTask.value.events ?? [];
          const isDuplicate = event.id ? 
            events.some((e) => e.id === event.id) : 
            false;
          if (!isDuplicate) {
            currentTask.value = {
              ...currentTask.value,
              events: [...events, event],
            };
          }
        }
      },
      onComplete: (completedTask: TaskRecord) => {
        currentTask.value = completedTask;
        loading.value = false;
        ElMessage.success("任务已完成，决策包已生成");
        if (subscribeResult) {
          subscribeResult.unsubscribe();
          subscribeResult = null;
        }
      },
      onError: (error: Error) => {
        loading.value = false;
        ElMessage.error(error.message || "任务执行失败");
        if (subscribeResult) {
          subscribeResult.unsubscribe();
          subscribeResult = null;
        }
      },
    });
  } catch (error) {
    loading.value = false;
    ElMessage.error(error instanceof Error ? error.message : "任务创建失败");
  }
}

function loadDemoData() {
  form.productGoal = demoForm.productGoal;
  form.competitors = [...demoForm.competitors];
  form.urls = [...demoForm.urls];
  form.comments = demoForm.comments;
  form.files = [];
  ElMessage.success("已加载演示用例");
}

onMounted(() => {
  setTimeout(() => {
    isVisible.value = true;
  }, 100);
});

onUnmounted(() => {
  if (subscribeResult) {
    subscribeResult.unsubscribe();
    subscribeResult = null;
  }
});

function getQualityTagType(score: number): string {
  if (score >= 0.7) return "success";
  if (score >= 0.5) return "warning";
  return "danger";
}

function getCredibilityTagType(score: number): string {
  if (score >= 0.6) return "success";
  if (score >= 0.4) return "warning";
  return "danger";
}

function normalizeScore(value: unknown): number | null {
  const score = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(score)) return null;
  return Math.min(1, Math.max(0, score));
}

function formatPercent(value: unknown): string {
  const score = normalizeScore(value);
  if (score === null) return "--";
  return `${Math.round(score * 100)}%`;
}

function getFailedMessage(task: TaskRecord): string {
  const failedEvents = (task.events ?? []).filter(e => e.status === "failed");
  if (failedEvents.length > 0) {
    const lastFailed = failedEvents[failedEvents.length - 1];
    return `${stageNames[lastFailed.stage] || lastFailed.stage}: ${lastFailed.message}`;
  }
  return "未知错误";
}

// 干预面板相关函数
function toggleInterventionPanel() {
  interventionPanelVisible.value = !interventionPanelVisible.value;
}

function resetInterventionForm() {
  interventionForm.target = "task";
  interventionForm.targetId = "";
  interventionForm.action = "approve";
  interventionForm.reason = "";
}

async function submitIntervention() {
  if (!currentTask.value?.id) {
    ElMessage.error("请先创建任务");
    return;
  }

  if (!canSubmitIntervention.value) {
    ElMessage.error("请填写完整的干预信息");
    return;
  }

  interventionLoading.value = true;

  try {
    const payload: InterventionRequest = {
      target: interventionForm.target,
      target_id: interventionForm.targetId.trim(),
      action: interventionForm.action,
      reason: interventionForm.reason.trim(),
    };

    await createIntervention(currentTask.value.id, payload);
    ElMessage.success("干预已提交");
    resetInterventionForm();
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "干预提交失败");
  } finally {
    interventionLoading.value = false;
  }
}

// 获取可选的目标 ID 列表
const targetOptions = computed(() => {
  if (!currentTask.value) return [];

  switch (interventionForm.target) {
    case "task":
      return [{ label: `任务 ${currentTask.value.id}`, value: currentTask.value.id }];
    case "evidence":
      return (currentTask.value.evidence ?? []).map(e => ({
        label: `证据 ${e.id} - ${e.dimension}: ${e.claim.slice(0, 30)}...`,
        value: e.id || "",
      }));
    case "decision":
      return (currentTask.value.decision_pack?.positioning ?? []).map(d => ({
        label: `决策 ${d.title}`,
        value: d.title,
      }));
    default:
      return [];
  }
});

// 干预审计时间线
interface InterventionEvent {
  id: string;
  timestamp: string;
  target: string;
  targetId: string;
  action: string;
  reason: string;
  stage?: string;
  previousStatus?: string;
}

const actionNames: Record<string, string> = {
  approve: "批准",
  reject: "拒绝",
  revise: "修订",
  force_rerun: "强制重跑",
};

const targetNames: Record<string, string> = {
  task: "任务",
  evidence: "证据",
  decision: "决策",
};

// 过滤并解析 human_intervention 事件
const interventionEvents = computed<InterventionEvent[]>(() => {
  if (!currentTask.value?.events) return [];

  return currentTask.value.events
    .filter((event) => event.stage === "human_intervention")
    .map((event) => {
      let metadata: Record<string, unknown> = {};
      try {
        metadata = JSON.parse(event.message || "{}");
      } catch {
        metadata = {};
      }

      return {
        id: event.id || `${event.created_at}-${Math.random()}`,
        timestamp: event.created_at || "",
        target: (metadata.target as string) || "task",
        targetId: (metadata.target_id as string) || "",
        action: (metadata.action as string) || "approve",
        reason: (metadata.reason as string) || "",
        stage: metadata.stage as string | undefined,
        previousStatus: metadata.previous_status as string | undefined,
      };
    });
});

// 格式化时间戳
function formatTimestamp(timestamp: string): string {
  if (!timestamp) return "--";
  try {
    const date = new Date(timestamp);
    return date.toLocaleString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return timestamp;
  }
}

// 运行指标计算属性
const taskMetrics = computed<TaskMetrics | null>(() => {
  if (!currentTask.value?.metrics) return null;
  return currentTask.value.metrics as TaskMetrics;
});

const citationPrecision = computed<number | null>(() => {
  if (!currentTask.value) return null;
  // 优先使用 review 中的 citation_precision
  if (currentTask.value.review?.citation_precision !== undefined) {
    return currentTask.value.review.citation_precision;
  }
  // 否则根据 evidence_ids 计算
  const evidenceIds = new Set(currentTask.value.evidence?.map(e => e.id) ?? []);
  if (evidenceIds.size === 0) return null;

  let totalCitations = 0;
  let validCitations = 0;

  // 统计 decision_pack 中的引用
  const actions = [
    ...(currentTask.value.decision_pack?.positioning ?? []),
    ...(currentTask.value.decision_pack?.pricing_insights ?? []),
    ...(currentTask.value.decision_pack?.battlecard ?? []),
  ];

  for (const action of actions) {
    for (const evidenceId of action.evidence_ids ?? []) {
      totalCitations++;
      if (evidenceIds.has(evidenceId)) {
        validCitations++;
      }
    }
  }

  // 统计 claims 中的引用
  for (const claim of currentTask.value.claims ?? []) {
    for (const evidenceId of claim.evidence_ids ?? []) {
      totalCitations++;
      if (evidenceIds.has(evidenceId)) {
        validCitations++;
      }
    }
  }

  if (totalCitations === 0) return null;
  return validCitations / totalCitations;
});

const reviewerScore = computed<number | null>(() => {
  if (!currentTask.value?.review?.score) return null;
  return currentTask.value.review.score;
});
</script>

<template>
  <div class="app-container">
    <main class="app-shell">
      <nav 
        class="topbar" 
        aria-label="主导航"
        :class="{ 'animate-fade-in-up': isVisible }"
      >
        <div class="brand-section">
          <div class="brand-icon">EF</div>
          <div class="brand-text">
            <p class="eyebrow">Evidence-First Agent</p>
            <h1>竞品情报决策工作台</h1>
          </div>
        </div>
        <span class="version-tag">MVP v1.0.0</span>
      </nav>

      <section 
        class="hero-panel" 
        aria-labelledby="hero-title"
        :class="{ 'animate-fade-in-up delay-1': isVisible }"
      >
        <div class="hero-content">
          <p class="eyebrow">竞品情报</p>
          <h2 id="hero-title">竞品情报决策工作台</h2>
          <p>
            将公开竞品信号转化为结构化证据、经过验证的洞察和可操作的产品决策。
          </p>
          <div class="features">
            <span class="feature-badge">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10"/>
                <path d="m9 12 2 2 4-4"/>
              </svg>
              证据优先
            </span>
            <span class="feature-badge">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12 20h9"/>
                <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>
              </svg>
              决策级品质
            </span>
            <span class="feature-badge">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="12" cy="12" r="10"/>
                <path d="M12 6v6l4 2"/>
              </svg>
              数据源无关
            </span>
          </div>
        </div>
        <div class="metric-grid" aria-label="首版能力指标">
          <div class="metric-card">
            <strong>7</strong>
            <span>工作流节点</span>
          </div>
          <div class="metric-card">
            <strong>3</strong>
            <span>输入类型</span>
          </div>
          <div class="metric-card">
            <strong>2</strong>
            <span>决策产物</span>
          </div>
        </div>
      </section>

      <div class="workspace-grid">
        <section 
          class="panel" 
          aria-labelledby="task-form-title"
          :class="{ 'animate-fade-in-up delay-2': isVisible }"
        >
          <h2 id="task-form-title">创建分析任务</h2>
          <el-form label-position="top" :label-width="0">
            <el-form-item label="产品目标 *">
              <el-input 
                v-model="form.productGoal" 
                type="textarea" 
                :rows="3" 
                maxlength="800" 
                show-word-limit
                placeholder="输入您的产品目标..."
              />
            </el-form-item>
            <el-form-item label="竞品名称 *">
              <div class="competitor-list">
                <div 
                  v-for="(competitor, index) in form.competitors" 
                  :key="index" 
                  class="competitor-item"
                >
                  <el-input 
                    v-model="form.competitors[index]" 
                    :placeholder="`输入竞品 ${index + 1} 的名称`"
                    class="competitor-input"
                  />
                  <button 
                    v-if="form.competitors.length > 1"
                    type="button"
                    class="remove-btn"
                    @click="removeCompetitor(index)"
                    title="移除竞品"
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                      <line x1="18" y1="6" x2="6" y2="18"/>
                      <line x1="6" y1="6" x2="18" y2="18"/>
                    </svg>
                  </button>
                </div>
                <button 
                  type="button" 
                  class="add-btn"
                  @click="addCompetitor"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <line x1="12" y1="5" x2="12" y2="19"/>
                    <line x1="5" y1="12" x2="19" y2="12"/>
                  </svg>
                  添加竞品
                </button>
              </div>
            </el-form-item>
            <el-form-item label="来源网址">
              <div class="url-list">
                <div 
                  v-for="(urlItem, index) in form.urls" 
                  :key="urlItem.id" 
                  class="url-item"
                >
                  <div class="url-competitor-select">
                    <el-select 
                      v-model="urlItem.competitorId" 
                      placeholder="选择关联的竞品"
                      class="competitor-select"
                    >
                      <el-option 
                        v-for="(competitor, idx) in form.competitors" 
                        :key="idx" 
                        :label="competitor || `竞品 ${idx + 1}`" 
                        :value="String(idx)"
                      />
                    </el-select>
                  </div>
                  <el-input 
                    v-model="urlItem.url" 
                    type="url"
                    placeholder="https://example.com"
                    class="url-input"
                  />
                  <button 
                    v-if="form.urls.length > 1"
                    type="button"
                    class="remove-btn"
                    @click="removeUrl(index)"
                    title="移除网址"
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                      <line x1="18" y1="6" x2="6" y2="18"/>
                      <line x1="6" y1="6" x2="18" y2="18"/>
                    </svg>
                  </button>
                </div>
                <button 
                  type="button" 
                  class="add-btn"
                  @click="addUrl"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <line x1="12" y1="5" x2="12" y2="19"/>
                    <line x1="5" y1="12" x2="19" y2="12"/>
                  </svg>
                  添加网址
                </button>
              </div>
              <p class="form-hint">点击"+"按钮添加网址，并选择该网址关联的竞品</p>
            </el-form-item>
            <el-form-item label="上传证据">
              <div class="upload-area">
                <input
                  ref="fileInputRef"
                  type="file"
                  multiple
                  accept="image/*,.pdf,.doc,.docx,.txt,.md"
                  class="file-input"
                  @change="handleFileSelect"
                />
                <div 
                  class="upload-drop-zone"
                  :class="{ 'is-dragging': isDragging }"
                  @click="triggerFileSelect"
                  @dragenter="handleDragEnter"
                  @dragleave="handleDragLeave"
                  @dragover="handleDragOver"
                  @drop="handleDrop"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="upload-icon">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                    <polyline points="17 8 12 3 7 8"/>
                    <line x1="12" y1="3" x2="12" y2="15"/>
                  </svg>
                  <p class="upload-text">点击或拖拽文件到此处上传</p>
                  <p class="upload-hint">支持图片(png,jpeg,webp,gif,bmp)、文档(pdf,doc,docx,txt,md)，单文件不超过 10MB</p>
                </div>

                <div v-if="form.files.length > 0" class="uploaded-files">
                  <div 
                    v-for="(uploadedFile, index) in form.files" 
                    :key="uploadedFile.id" 
                    class="uploaded-file-item"
                  >
                    <div v-if="uploadedFile.preview" class="file-preview">
                      <img :src="uploadedFile.preview" :alt="uploadedFile.name" />
                    </div>
                    <div v-else class="file-icon">
                      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                        <polyline points="14 2 14 8 20 8"/>
                        <line x1="16" y1="13" x2="8" y2="13"/>
                        <line x1="16" y1="17" x2="8" y2="17"/>
                        <polyline points="10 9 9 9 8 9"/>
                      </svg>
                    </div>
                    <div class="file-info">
                      <p class="file-name">{{ uploadedFile.name }}</p>
                      <p class="file-size">{{ formatFileSize(uploadedFile.size) }}</p>
                    </div>
                    <div class="file-status">
                      <template v-if="uploadedFile.status === 'pending'">
                        <span class="status-pending">待上传</span>
                      </template>
                      <template v-else-if="uploadedFile.status === 'uploading'">
                        <div class="progress-bar">
                          <div class="progress-fill" :style="{ width: uploadedFile.progress + '%' }"></div>
                        </div>
                        <span class="status-uploading">{{ uploadedFile.progress }}%</span>
                      </template>
                      <template v-else-if="uploadedFile.status === 'success'">
                        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="status-icon success">
                          <polyline points="20 6 9 17 4 12"/>
                        </svg>
                        <span class="status-success">已上传</span>
                      </template>
                      <template v-else-if="uploadedFile.status === 'error'">
                        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="status-icon error">
                          <circle cx="12" cy="12" r="10"/>
                          <line x1="15" y1="9" x2="9" y2="15"/>
                          <line x1="9" y1="9" x2="15" y2="15"/>
                        </svg>
                        <span class="status-error">{{ uploadedFile.message }}</span>
                      </template>
                    </div>
                    <button 
                      type="button" 
                      class="remove-btn"
                      @click="handleFileRemove(index, uploadedFile)"
                      title="移除文件"
                    >
                      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <line x1="18" y1="6" x2="6" y2="18"/>
                        <line x1="6" y1="6" x2="18" y2="18"/>
                      </svg>
                    </button>
                  </div>
                </div>

                <div v-if="form.files.some(f => f.status === 'pending')" class="upload-actions">
                  <button type="button" class="upload-btn" :disabled="isAnalyzing" @click="uploadFiles">
                    <svg v-if="!isAnalyzing" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                      <polyline points="17 8 12 3 7 8"/>
                      <line x1="12" y1="3" x2="12" y2="15"/>
                    </svg>
                    <svg v-else xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="animate-spin">
                      <circle cx="12" cy="12" r="10" stroke-dasharray="100" stroke-dashoffset="25"/>
                    </svg>
                    {{ isAnalyzing ? '正在上传分析...' : '上传并分析' }}
                  </button>
                </div>

                <div v-if="analysisResults.length > 0" class="analysis-results">
                  <h4 class="analysis-title">分析结果</h4>
                  <button type="button" class="clear-analysis-btn" @click="clearAnalysis">
                    清除分析
                  </button>
                  <div class="analysis-grid">
                    <div 
                      v-for="(analysis, index) in analysisResults" 
                      :key="index" 
                      class="analysis-card"
                    >
                      <div class="analysis-header">
                        <span class="analysis-type" :class="analysis.type">
                          {{ analysis.type === 'image' ? '🖼️ 图片分析' : '📄 文档分析' }}
                        </span>
                        <span class="analysis-source">{{ analysis.source }}</span>
                      </div>
                      <div class="analysis-dimensions">
                        <p class="dimensions-label">分析维度:</p>
                        <div class="dimensions-tags">
                          <span 
                            v-for="(dim, idx) in analysis.dimensions" 
                            :key="idx" 
                            class="dimension-tag"
                          >
                            {{ dim }}
                          </span>
                        </div>
                      </div>
                      <div class="analysis-insights">
                        <p class="insights-label">洞察发现:</p>
                        <ul class="insights-list">
                          <li v-for="(insight, idx) in analysis.insights" :key="idx">
                            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="insight-icon">
                              <circle cx="12" cy="12" r="10"/>
                              <polyline points="12 6 12 12 16 14"/>
                            </svg>
                            {{ insight }}
                          </li>
                        </ul>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </el-form-item>
            <el-form-item label="上传备注">
              <el-input 
                v-model="form.comments" 
                type="textarea" 
                :rows="4" 
                maxlength="10000" 
                show-word-limit
                placeholder="用户反馈、市场研究笔记..."
              />
            </el-form-item>
            <div class="form-footer">
              <p>我们仅使用公开来源数据。无需登录。</p>
              <div class="form-actions">
                <el-button class="cursor-pointer" @click="loadDemoData">加载演示用例</el-button>
                <el-button type="primary" :loading="loading" class="cursor-pointer" @click="submitTask">
                  运行分析
                </el-button>
              </div>
            </div>
          </el-form>
        </section>

        <section
          class="panel"
          aria-labelledby="progress-title"
          :class="{ 'animate-fade-in-up delay-3': isVisible }"
        >
          <h2 id="progress-title">工作流进度</h2>
          <el-empty v-if="!currentTask" description="运行分析任务以查看进度" />
          <el-timeline v-else>
            <el-timeline-item
              v-for="(stage, index) in stageOrder"
              :key="stage"
              :type="(currentTask.events ?? []).some((event) => event.stage === stage) ? 'primary' : 'info'"
              :class="{ 'animate-fade-in-up': currentTask }"
              :style="{ animationDelay: `${index * 0.08}s` }"
            >
              <strong>{{ stageNames[stage] || stage }}</strong>
              <p>{{ (currentTask.events ?? []).find((event) => event.stage === stage)?.message ?? "等待执行" }}</p>
            </el-timeline-item>
          </el-timeline>
        </section>

        <!-- 运行指标卡片 -->
        <section
          v-if="currentTask && taskMetrics"
          class="panel metrics-panel"
          aria-labelledby="metrics-title"
        >
          <h2 id="metrics-title">运行指标</h2>
          <div class="metrics-grid">
            <div class="stat-card">
              <div class="stat-icon">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <circle cx="12" cy="12" r="10"/>
                  <polyline points="12 6 12 12 16 14"/>
                </svg>
              </div>
              <div class="stat-content">
                <span class="stat-value">{{ taskMetrics.total_duration_ms }}<span class="stat-unit">ms</span></span>
                <span class="stat-label">总时延</span>
              </div>
            </div>
            <div class="stat-card">
              <div class="stat-icon">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"/>
                  <polyline points="14 2 14 8 20 8"/>
                  <line x1="16" y1="13" x2="8" y2="13"/>
                  <line x1="16" y1="17" x2="8" y2="17"/>
                  <polyline points="10 9 9 9 8 9"/>
                </svg>
              </div>
              <div class="stat-content">
                <span class="stat-value">{{ taskMetrics.evidence_count }}</span>
                <span class="stat-label">证据数</span>
              </div>
            </div>
            <div class="stat-card">
              <div class="stat-icon">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
                  <line x1="12" y1="9" x2="12" y2="13"/>
                  <line x1="12" y1="17" x2="12.01" y2="17"/>
                </svg>
              </div>
              <div class="stat-content">
                <span class="stat-value">{{ taskMetrics.conflict_count }}</span>
                <span class="stat-label">冲突数</span>
              </div>
            </div>
            <div v-if="reviewerScore !== null" class="stat-card">
              <div class="stat-icon">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>
                </svg>
              </div>
              <div class="stat-content">
                <span class="stat-value">{{ formatPercent(reviewerScore) }}</span>
                <span class="stat-label">Reviewer 分数</span>
              </div>
            </div>
            <div v-if="citationPrecision !== null" class="stat-card">
              <div class="stat-icon">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10"/>
                  <path d="m9 12 2 2 4-4"/>
                </svg>
              </div>
              <div class="stat-content">
                <span class="stat-value">{{ formatPercent(citationPrecision) }}</span>
                <span class="stat-label">Citation Precision</span>
              </div>
            </div>
          </div>
        </section>

        <!-- 干预面板 -->
        <section
          v-if="currentTask"
          class="panel intervention-panel"
          aria-labelledby="intervention-title"
        >
          <div class="panel-header" @click="toggleInterventionPanel">
            <h2 id="intervention-title">人工干预</h2>
            <span class="toggle-icon" :class="{ expanded: interventionPanelVisible }">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="6 9 12 15 18 9"></polyline>
              </svg>
            </span>
          </div>

          <el-collapse-transition>
            <div v-show="interventionPanelVisible" class="intervention-content">
              <el-form label-position="top" :label-width="0">
                <el-form-item label="干预目标类型">
                  <el-radio-group v-model="interventionForm.target">
                    <el-radio-button value="task">任务</el-radio-button>
                    <el-radio-button value="evidence">证据</el-radio-button>
                    <el-radio-button value="decision">决策</el-radio-button>
                  </el-radio-group>
                </el-form-item>

                <el-form-item label="目标 ID">
                  <el-select
                    v-model="interventionForm.targetId"
                    placeholder="请选择目标"
                    style="width: 100%"
                    :disabled="targetOptions.length === 0"
                  >
                    <el-option
                      v-for="option in targetOptions"
                      :key="option.value"
                      :label="option.label"
                      :value="option.value"
                    />
                  </el-select>
                </el-form-item>

                <el-form-item label="操作类型">
                  <el-radio-group v-model="interventionForm.action">
                    <el-radio-button value="approve">批准</el-radio-button>
                    <el-radio-button value="reject">拒绝</el-radio-button>
                    <el-radio-button value="revise">修订</el-radio-button>
                    <el-radio-button value="force_rerun">强制重跑</el-radio-button>
                  </el-radio-group>
                </el-form-item>

                <el-form-item label="原因说明">
                  <el-input
                    v-model="interventionForm.reason"
                    type="textarea"
                    :rows="3"
                    maxlength="500"
                    show-word-limit
                    placeholder="请输入干预原因（4-500 字符）"
                  />
                  <div v-if="interventionReasonError" class="error-text">
                    {{ interventionReasonError }}
                  </div>
                </el-form-item>

                <div class="form-footer">
                  <el-button @click="resetInterventionForm">重置</el-button>
                  <el-button
                    type="primary"
                    :loading="interventionLoading"
                    :disabled="!canSubmitIntervention"
                    @click="submitIntervention"
                  >
                    提交干预
                  </el-button>
                </div>
              </el-form>
            </div>
          </el-collapse-transition>
        </section>

        <!-- 干预审计时间线 -->
        <section
          v-if="currentTask && interventionEvents.length > 0"
          class="panel audit-timeline-panel"
          aria-labelledby="audit-timeline-title"
        >
          <h2 id="audit-timeline-title">干预审计时间线</h2>
          <el-timeline class="intervention-timeline">
            <el-timeline-item
              v-for="(event, index) in interventionEvents"
              :key="event.id"
              :type="event.action === 'approve' ? 'success' : event.action === 'reject' ? 'danger' : event.action === 'force_rerun' ? 'warning' : 'primary'"
              :timestamp="formatTimestamp(event.timestamp)"
              placement="top"
              :class="{ 'animate-fade-in-up': true }"
              :style="{ animationDelay: `${index * 0.1}s` }"
            >
              <div class="timeline-card">
                <div class="timeline-header">
                  <span class="action-badge" :class="`action-${event.action}`">
                    {{ actionNames[event.action] || event.action }}
                  </span>
                  <span class="target-info">
                    {{ targetNames[event.target] || event.target }}
                    <template v-if="event.targetId">: {{ event.targetId.slice(0, 20) }}{{ event.targetId.length > 20 ? '...' : '' }}</template>
                  </span>
                </div>
                <div v-if="event.reason" class="timeline-reason">
                  <strong>原因：</strong>{{ event.reason }}
                </div>
                <div v-if="event.stage" class="timeline-meta">
                  <span class="meta-item">
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="meta-icon">
                      <polyline points="23 4 23 10 17 10"></polyline>
                      <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path>
                    </svg>
                    重跑阶段：{{ stageNames[event.stage] || event.stage }}
                  </span>
                  <span v-if="event.previousStatus" class="meta-item">
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="meta-icon">
                      <circle cx="12" cy="12" r="10"></circle>
                      <polyline points="12 6 12 12 16 14"></polyline>
                    </svg>
                    之前状态：{{ event.previousStatus }}
                  </span>
                </div>
              </div>
            </el-timeline-item>
          </el-timeline>
        </section>
      </div>

      <section 
        v-if="currentTask" 
        class="workspace-grid"
        :class="{ 'animate-fade-in-up delay-4': currentTask }"
      >
        <article class="panel" aria-labelledby="coverage-title">
          <h2 id="coverage-title">证据覆盖</h2>
          <div v-if="currentTask.coverage" class="coverage-box">
            <div class="progress-section">
              <el-progress :percentage="Math.round(currentTask.coverage.score * 100)" :width="200" />
              <span :class="['result-tag', currentTask.coverage.passed ? 'passed' : 'failed']">
                {{ currentTask.coverage.passed ? '已验证' : '部分覆盖' }}
              </span>
            </div>
            <p>
              结果：
              <strong :class="currentTask.coverage.passed ? 'text-green-600' : 'text-amber-600'">
                {{ currentTask.coverage.passed ? "覆盖达标" : "存在缺口" }}
              </strong>
            </p>
            <p v-if="currentTask.budget_usage">
              预算估算：{{ currentTask.budget_usage.estimated_sources }} 个来源，
              {{ currentTask.budget_usage.estimated_tokens }} tokens，
              ${{ currentTask.budget_usage.estimated_cost_usd }}
            </p>
            <p>已覆盖维度：{{ currentTask.coverage.covered_dimensions.join(", ") || "无" }}</p>
            <el-collapse v-if="currentTask.coverage.gap_queries && currentTask.coverage.gap_queries.length > 0">
              <el-collapse-item title="缺口查询详情">
                <ul class="gap-list">
                  <li v-for="query in currentTask.coverage.gap_queries" :key="query">{{ query }}</li>
                </ul>
              </el-collapse-item>
            </el-collapse>
            <el-collapse v-if="currentTask.conflicts && currentTask.conflicts.length > 0">
              <el-collapse-item :title="`冲突裁决（${currentTask.conflicts.length}）`">
                <ul class="gap-list">
                  <li v-for="conflict in currentTask.conflicts" :key="conflict.id">
                    <strong>{{ conflict.resolution }}</strong>
                    <div>{{ conflict.rationale }}</div>
                  </li>
                </ul>
              </el-collapse-item>
            </el-collapse>
          </div>
          <el-alert 
            v-if="currentTask.status === 'failed'" 
            type="error" 
            :closable="false"
            class="failed-alert"
          >
            <template #title>
              <strong>任务失败</strong>
            </template>
            <p>{{ getFailedMessage(currentTask) }}</p>
          </el-alert>
          <el-table :data="currentTask.evidence ?? []" class="evidence-table" stripe>
            <el-table-column prop="id" label="证据ID" width="120" />
            <el-table-column prop="competitor" label="来源" width="100" />
            <el-table-column prop="dimension" label="信号类型" width="110" />
            <el-table-column prop="claim" label="证据结论" />
            <el-table-column label="置信度" width="90">
              <template #default="{ row }">
                <div class="confidence-bar">
                  <div 
                    class="confidence-fill" 
                    :style="{ width: `${Math.round((row.confidence || 0) * 100)}%` }"
                    :class="{
                      'confidence-high': (row.confidence || 0) >= 0.7,
                      'confidence-medium': (row.confidence || 0) >= 0.4 && (row.confidence || 0) < 0.7,
                      'confidence-low': (row.confidence || 0) < 0.4
                    }"
                  />
                </div>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="80">
              <template #default="{ row }">
                <el-tag 
                  :type="row.status === 'verified' ? 'success' : row.status === 'partial' ? 'warning' : 'danger'" 
                  size="small"
                >
                  {{ row.status === 'verified' ? '已验证' : row.status === 'partial' ? '部分' : '未验证' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column width="60">
              <template #default>
                <button class="view-btn">查看</button>
              </template>
            </el-table-column>
          </el-table>
        </article>

        <article class="panel" aria-labelledby="decision-title">
          <h2 id="decision-title">决策包</h2>
          <template v-if="currentTask.decision_pack">
            <div class="decision-pack-container">
              <div class="summary">{{ currentTask.decision_pack.summary }}</div>
              
              <div class="section">
                <h3 class="section-title">
                  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/>
                    <polyline points="22 4 12 14.01 9 11.01"/>
                  </svg>
                  关键建议
                </h3>
                <div class="recommendations-list">
                  <div 
                    v-for="(action, index) in currentTask.decision_pack.positioning" 
                    :key="action.title" 
                    class="recommendation-item"
                    :style="{ animationDelay: `${index * 0.1}s` }"
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                      <polyline points="20 6 9 17 4 12"/>
                    </svg>
                    <p>{{ action.recommendation }}</p>
                  </div>
                </div>
              </div>

              <div v-if="currentTask.decision_pack.pricing_insights && currentTask.decision_pack.pricing_insights.length > 0" class="section">
                <h3 class="section-title">
                  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <line x1="12" y1="1" x2="12" y2="23"/>
                    <path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>
                  </svg>
                  定价洞察
                </h3>
                <div class="decision-cards-list">
                  <div 
                    v-for="(insight, index) in currentTask.decision_pack.pricing_insights" 
                    :key="insight.title" 
                    class="decision-card"
                    :style="{ animationDelay: `${index * 0.1}s` }"
                  >
                    <div class="card-header">
                      <span class="card-title">{{ insight.title }}</span>
                      <el-tag :type="insight.priority === 'P0' ? 'danger' : insight.priority === 'P1' ? 'warning' : 'info'" size="small">
                        {{ insight.priority }}
                      </el-tag>
                    </div>
                    <div class="card-body">
                      <p class="card-recommendation">{{ insight.recommendation }}</p>
                      <p class="card-rationale">{{ insight.rationale }}</p>
                    </div>
                    <div class="card-footer">
                      <span class="evidence-label">证据引用：</span>
                      <div class="evidence-ids">
                        <el-tag 
                          v-for="evidenceId in insight.evidence_ids" 
                          :key="evidenceId" 
                          size="small" 
                          type="info"
                          class="evidence-tag"
                        >
                          {{ evidenceId }}
                        </el-tag>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <div v-if="currentTask.decision_pack.battlecard && currentTask.decision_pack.battlecard.length > 0" class="section">
                <h3 class="section-title">
                  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
                    <line x1="3" y1="9" x2="21" y2="9"/>
                    <line x1="9" y1="21" x2="9" y2="9"/>
                  </svg>
                  竞争卡片
                </h3>
                <div class="decision-cards-list">
                  <div 
                    v-for="(card, index) in currentTask.decision_pack.battlecard" 
                    :key="card.title" 
                    class="decision-card"
                    :style="{ animationDelay: `${index * 0.1}s` }"
                  >
                    <div class="card-header">
                      <span class="card-title">{{ card.title }}</span>
                      <el-tag :type="card.priority === 'P0' ? 'danger' : card.priority === 'P1' ? 'warning' : 'info'" size="small">
                        {{ card.priority }}
                      </el-tag>
                    </div>
                    <div class="card-body">
                      <p class="card-recommendation">{{ card.recommendation }}</p>
                      <p class="card-rationale">{{ card.rationale }}</p>
                    </div>
                    <div class="card-footer">
                      <span class="evidence-label">证据引用：</span>
                      <div class="evidence-ids">
                        <el-tag 
                          v-for="evidenceId in card.evidence_ids" 
                          :key="evidenceId" 
                          size="small" 
                          type="info"
                          class="evidence-tag"
                        >
                          {{ evidenceId }}
                        </el-tag>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <div class="section">
                <h3 class="section-title">
                  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M12 9v4"/>
                    <path d="M12 17h.01"/>
                    <path d="M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10z"/>
                  </svg>
                  风险提示
                </h3>
                <div class="risks-list">
                  <div 
                    v-for="(risk, index) in ['模板市场竞争激烈', '循环验证难以造假', 'SMB 细分市场对价格敏感', '需持续监控竞品动态']" 
                    :key="index" 
                    class="risk-item"
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                      <path d="M12 9v4"/>
                      <path d="M12 17h.01"/>
                      <path d="M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10z"/>
                    </svg>
                    <p>{{ risk }}</p>
                  </div>
                </div>
              </div>

              <div class="section">
                <h3 class="section-title">
                  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"/>
                    <polyline points="14 2 14 8 20 8"/>
                    <line x1="16" y1="13" x2="8" y2="13"/>
                    <line x1="16" y1="17" x2="8" y2="17"/>
                    <polyline points="10 9 9 9 8 9"/>
                  </svg>
                  决策输出
                </h3>
                <div class="outputs-list">
                  <span class="output-tag">
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                      <path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"/>
                      <polyline points="14 2 14 8 20 8"/>
                    </svg>
                    证据ID决策包
                  </span>
                  <span class="output-tag">
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                      <path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"/>
                      <polyline points="14 2 14 8 20 8"/>
                    </svg>
                    产品定位简报
                  </span>
                  <span class="output-tag">
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                      <path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"/>
                      <polyline points="14 2 14 8 20 8"/>
                    </svg>
                    功能机会地图
                  </span>
                  <span class="output-tag">
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                      <path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"/>
                      <polyline points="14 2 14 8 20 8"/>
                    </svg>
                    风险与缓解摘要
                  </span>
                </div>
              </div>

              <div class="decision-pack-footer">
                <div class="meta-info">
                  <span class="meta-item">
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                      <circle cx="12" cy="12" r="10"/>
                      <polyline points="12 6 12 12 16 14"/>
                    </svg>
                    生成时间：{{ formatTimestamp(currentTask.created_at || "") }}
                  </span>
                  <span class="meta-item">
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10"/>
                    </svg>
                    声明来源：公开数据
                  </span>
                </div>
                <el-button type="primary" size="small">导出决策包</el-button>
              </div>
            </div>

            <div v-if="currentTask.review" class="review-box">
              <strong>审核评分：{{ Math.round(currentTask.review.score * 100) }}%</strong>
              <p>幻觉风险：{{ currentTask.review.hallucination_risk }}</p>
            </div>
          </template>
        </article>
      </section>
    </main>
  </div>
</template>

<style scoped>
.app-container {
  min-height: 100vh;
  position: relative;
}

.text-green-600 {
  color: var(--success-600);
}

.text-amber-600 {
  color: var(--warning-600);
}

.confidence-bar {
  width: 100%;
  height: 6px;
  background: var(--secondary-100);
  border-radius: 3px;
  overflow: hidden;
}

.confidence-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.3s ease;
}

.confidence-high {
  background: linear-gradient(90deg, var(--success-500), var(--success-400));
}

.confidence-medium {
  background: linear-gradient(90deg, var(--warning-500), var(--warning-400));
}

.confidence-low {
  background: linear-gradient(90deg, var(--danger-500), var(--danger-400));
}

.view-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 50px;
  height: 28px;
  padding: 0;
  border: 1px solid var(--secondary-200);
  border-radius: 6px;
  background: white;
  color: var(--secondary-600);
  font-size: 0.75rem;
  font-weight: 500;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.view-btn:hover {
  border-color: var(--primary-400);
  color: var(--primary-600);
}

.gap-list {
  margin: 0;
  padding-left: 1.25rem;
  list-style-type: disc;
}

.gap-list li {
  margin-bottom: 0.5rem;
  color: var(--warning-600);
  font-size: 0.875rem;
  line-height: 1.6;
}

.failed-alert {
  margin-bottom: 1rem;
}

.form-actions {
  display: flex;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.intervention-panel {
  margin-top: 1rem;
}

.intervention-panel .panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  cursor: pointer;
  user-select: none;
}

.intervention-panel .panel-header:hover {
  opacity: 0.8;
}

.intervention-panel .toggle-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  transition: transform 0.3s ease;
}

.intervention-panel .toggle-icon svg {
  width: 16px;
  height: 16px;
  color: var(--secondary-500);
}

.intervention-panel .toggle-icon.expanded {
  transform: rotate(180deg);
}

.intervention-content {
  padding-top: 1rem;
}

.error-text {
  color: var(--danger-500);
  font-size: 0.75rem;
  margin-top: 0.25rem;
}

/* 干预审计时间线样式 */
.audit-timeline-panel {
  margin-top: 1rem;
}

.audit-timeline-panel h2 {
  margin-bottom: 1rem;
}

.intervention-timeline {
  padding-left: 0;
}

.timeline-card {
  background: var(--secondary-50, #f8fafc);
  border: 1px solid var(--secondary-200, #e2e8f0);
  border-radius: 8px;
  padding: 1rem;
  margin-bottom: 0.5rem;
}

.timeline-header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 0.5rem;
  flex-wrap: wrap;
}

.action-badge {
  display: inline-flex;
  align-items: center;
  padding: 0.25rem 0.75rem;
  border-radius: 9999px;
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.action-badge.action-approve {
  background: var(--success-100, #dcfce7);
  color: var(--success-700, #15803d);
}

.action-badge.action-reject {
  background: var(--danger-100, #fee2e2);
  color: var(--danger-700, #b91c1c);
}

.action-badge.action-revise {
  background: var(--primary-100, #dbeafe);
  color: var(--primary-700, #1d4ed8);
}

.action-badge.action-force_rerun {
  background: var(--warning-100, #fef3c7);
  color: var(--warning-700, #b45309);
}

.target-info {
  font-size: 0.875rem;
  color: var(--secondary-600, #475569);
}

.timeline-reason {
  font-size: 0.875rem;
  color: var(--secondary-700, #334155);
  line-height: 1.6;
  margin-bottom: 0.5rem;
}

.timeline-reason strong {
  color: var(--secondary-900, #0f172a);
}

.timeline-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
  margin-top: 0.5rem;
  padding-top: 0.5rem;
  border-top: 1px dashed var(--secondary-200, #e2e8f0);
}

.timeline-meta .meta-item {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.75rem;
  color: var(--secondary-500, #64748b);
}

.timeline-meta .meta-icon {
  width: 14px;
  height: 14px;
}

/* 定价洞察和竞争卡片样式 */
.decision-cards-list {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.decision-card {
  background: var(--secondary-50, #f8fafc);
  border: 1px solid var(--secondary-200, #e2e8f0);
  border-radius: 12px;
  padding: 1.25rem;
  transition: all var(--transition-fast);
  animation: fade-in-up 0.5s ease forwards;
}

.decision-card:hover {
  border-color: var(--primary-300, #93c5fd);
  box-shadow: 0 2px 8px rgba(59, 130, 246, 0.1);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.75rem;
}

.card-title {
  font-size: 1rem;
  font-weight: 600;
  color: var(--secondary-900, #0f172a);
}

.card-body {
  margin-bottom: 0.75rem;
}

.card-recommendation {
  font-size: 0.875rem;
  color: var(--secondary-700, #334155);
  line-height: 1.6;
  margin-bottom: 0.5rem;
}

.card-rationale {
  font-size: 0.75rem;
  color: var(--secondary-500, #64748b);
  line-height: 1.5;
}

.card-footer {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding-top: 0.75rem;
  border-top: 1px dashed var(--secondary-200, #e2e8f0);
}

.evidence-label {
  font-size: 0.75rem;
  color: var(--secondary-600, #475569);
  font-weight: 500;
}

.evidence-ids {
  display: flex;
  flex-wrap: wrap;
  gap: 0.375rem;
}

.evidence-tag {
  font-size: 0.7rem;
  padding: 0.125rem 0.5rem;
}

/* 运行指标卡片样式 */
.metrics-panel {
  margin-top: 1rem;
}

.metrics-panel h2 {
  margin-bottom: 1rem;
}

.metrics-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 1rem;
}

.stat-card {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 1rem;
  background: var(--secondary-50, #f8fafc);
  border: 1px solid var(--secondary-200, #e2e8f0);
  border-radius: 12px;
  transition: all var(--transition-fast);
}

.stat-card:hover {
  border-color: var(--primary-300, #93c5fd);
  box-shadow: 0 2px 8px rgba(59, 130, 246, 0.1);
}

.stat-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  background: var(--primary-100, #dbeafe);
  border-radius: 10px;
  flex-shrink: 0;
}

.stat-icon svg {
  width: 20px;
  height: 20px;
  color: var(--primary-600, #2563eb);
}

.stat-content {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.stat-value {
  font-size: 1.25rem;
  font-weight: 700;
  color: var(--secondary-900, #0f172a);
  line-height: 1.2;
}

.stat-unit {
  font-size: 0.75rem;
  font-weight: 500;
  color: var(--secondary-500, #64748b);
  margin-left: 0.125rem;
}

.stat-label {
  font-size: 0.75rem;
  color: var(--secondary-500, #64748b);
}

@keyframes fade-in-up {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>
