<script setup lang="ts">
import { ref, reactive, onMounted, computed } from 'vue';
import { useRouter } from 'vue-router';
import { ElMessage, ElButton, ElCard, ElTable, ElTableColumn, ElForm, ElFormItem, ElInput, ElSelect, ElOption, ElTag } from 'element-plus';
import { listBadCases, createBadCase, updateBadCaseStatus, getBadCaseSummary, type BadCase, type CreateBadCaseRequest, type BadCaseSummary } from '../services/api';

const router = useRouter();
const loading = ref(false);
const summaryLoading = ref(false);
const badCases = ref<BadCase[]>([]);
const summary = ref<BadCaseSummary>({ total: 0, by_status: {}, by_type: {}, by_severity: {} });

const filterForm = reactive({
  type: '',
  status: '',
  severity: '',
});

const createForm = reactive({
  type: '',
  severity: 'medium',
  description: '',
  task_id: '',
});

const typeOptions = [
  { value: 'recall_failure', label: '召回失败' },
  { value: 'hallucination', label: '幻觉' },
  { value: 'coverage_gap', label: '覆盖缺口' },
  { value: 'evidence_conflict', label: '证据冲突' },
  { value: 'low_quality', label: '低质量' },
  { value: 'user_complaint', label: '用户投诉' },
];

const severityOptions = [
  { value: 'critical', label: '严重' },
  { value: 'high', label: '高' },
  { value: 'medium', label: '中' },
  { value: 'low', label: '低' },
];

const statusOptions = [
  { value: 'pending', label: '待分析' },
  { value: 'analyzed', label: '已分析' },
  { value: 'fixed', label: '已修复' },
  { value: 'wont_fix', label: '暂不修复' },
];

const typeLabels: Record<string, string> = {
  recall_failure: '召回失败',
  hallucination: '幻觉',
  coverage_gap: '覆盖缺口',
  evidence_conflict: '证据冲突',
  low_quality: '低质量',
  user_complaint: '用户投诉',
};

const severityLabels: Record<string, string> = {
  critical: '严重',
  high: '高',
  medium: '中',
  low: '低',
};

const statusLabels: Record<string, string> = {
  pending: '待分析',
  analyzed: '已分析',
  fixed: '已修复',
  wont_fix: '暂不修复',
};

const severityColors: Record<string, 'danger' | 'warning' | 'info' | 'success'> = {
  critical: 'danger',
  high: 'warning',
  medium: 'info',
  low: 'success',
};

const statusColors: Record<string, 'warning' | 'info' | 'success'> = {
  pending: 'warning',
  analyzed: 'info',
  fixed: 'success',
  wont_fix: 'info',
};

const filteredBadCases = computed(() => {
  return badCases.value.filter(caseItem => {
    if (filterForm.type && caseItem.type !== filterForm.type) return false;
    if (filterForm.status && caseItem.status !== filterForm.status) return false;
    if (filterForm.severity && caseItem.severity !== filterForm.severity) return false;
    return true;
  });
});

onMounted(async () => {
  await loadBadCases();
  await loadSummary();
});

async function loadBadCases() {
  loading.value = true;
  try {
    badCases.value = await listBadCases(
      filterForm.type as any || undefined,
      filterForm.status as any || undefined,
      filterForm.severity as any || undefined
    );
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '获取异常样本列表失败');
  } finally {
    loading.value = false;
  }
}

async function loadSummary() {
  summaryLoading.value = true;
  try {
    summary.value = await getBadCaseSummary();
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '获取汇总统计失败');
  } finally {
    summaryLoading.value = false;
  }
}

async function handleCreate() {
  if (!createForm.type || !createForm.description) {
    ElMessage.warning('请填写类型和描述');
    return;
  }

  loading.value = true;
  try {
    const request: CreateBadCaseRequest = {
      type: createForm.type as any,
      description: createForm.description,
      severity: createForm.severity as any,
      task_id: createForm.task_id || undefined,
    };
    await createBadCase(request);
    ElMessage.success('创建成功');
    createForm.type = '';
    createForm.severity = 'medium';
    createForm.description = '';
    createForm.task_id = '';
    await loadBadCases();
    await loadSummary();
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '创建失败');
  } finally {
    loading.value = false;
  }
}

async function handleStatusChange(badCaseId: string, status: string) {
  try {
    await updateBadCaseStatus(badCaseId, status as any);
    ElMessage.success('状态更新成功');
    await loadBadCases();
    await loadSummary();
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '更新失败');
  }
}

function goBack() {
  router.push('/');
}

function formatDate(dateStr?: string) {
  if (!dateStr) return '-';
  const date = new Date(dateStr);
  return date.toLocaleString('zh-CN');
}

</script>

<template>
  <div class="badcase-page">
    <div class="page-header">
      <button class="back-btn" @click="goBack">← 返回</button>
        <h1>异常样本管理</h1>
    </div>

    <div class="summary-cards">
      <ElCard :loading="summaryLoading" class="summary-card">
        <div class="summary-icon total">📊</div>
        <div class="summary-info">
          <span class="summary-count">{{ summary.total }}</span>
          <span class="summary-label">异常样本总数</span>
        </div>
      </ElCard>

      <ElCard :loading="summaryLoading" class="summary-card">
        <div class="summary-icon pending">⏳</div>
        <div class="summary-info">
          <span class="summary-count">{{ summary.by_status.pending || 0 }}</span>
          <span class="summary-label">待分析</span>
        </div>
      </ElCard>

      <ElCard :loading="summaryLoading" class="summary-card">
        <div class="summary-icon critical">⚠️</div>
        <div class="summary-info">
          <span class="summary-count">{{ summary.by_severity.critical || 0 }}</span>
          <span class="summary-label">严重</span>
        </div>
      </ElCard>

      <ElCard :loading="summaryLoading" class="summary-card">
        <div class="summary-icon fixed">✅</div>
        <div class="summary-info">
          <span class="summary-count">{{ summary.by_status.fixed || 0 }}</span>
          <span class="summary-label">已修复</span>
        </div>
      </ElCard>
    </div>

    <div class="page-content">
      <div class="left-panel">
        <ElCard title="创建异常样本">
          <ElForm :model="createForm" label-width="92px">
            <ElFormItem label="样本类型">
              <ElSelect v-model="createForm.type" placeholder="请选择">
                <ElOption v-for="opt in typeOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
              </ElSelect>
            </ElFormItem>

            <ElFormItem label="严重程度">
              <ElSelect v-model="createForm.severity">
                <ElOption v-for="opt in severityOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
              </ElSelect>
            </ElFormItem>

            <ElFormItem label="归属分析任务ID（可选）">
              <ElInput v-model="createForm.task_id" placeholder="请输入归属分析任务ID" />
            </ElFormItem>

            <ElFormItem label="问题描述">
              <textarea v-model="createForm.description" rows="3" placeholder="请描述异常现象..." class="textarea" />
            </ElFormItem>

            <ElFormItem>
              <ElButton type="primary" :loading="loading" @click="handleCreate">创建</ElButton>
            </ElFormItem>
          </ElForm>
        </ElCard>

          <ElCard title="筛选条件">
          <ElForm :model="filterForm" label-width="92px">
            <ElFormItem label="样本类型">
              <ElSelect v-model="filterForm.type" placeholder="全部">
                <ElOption label="全部" value="" />
                <ElOption v-for="opt in typeOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
              </ElSelect>
            </ElFormItem>

            <ElFormItem label="处理状态">
              <ElSelect v-model="filterForm.status" placeholder="全部">
                <ElOption label="全部" value="" />
                <ElOption v-for="opt in statusOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
              </ElSelect>
            </ElFormItem>

            <ElFormItem label="严重程度">
              <ElSelect v-model="filterForm.severity" placeholder="全部">
                <ElOption label="全部" value="" />
                <ElOption v-for="opt in severityOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
              </ElSelect>
            </ElFormItem>

            <ElFormItem>
              <ElButton @click="loadBadCases">应用筛选</ElButton>
            </ElFormItem>
          </ElForm>
        </ElCard>
      </div>

      <div class="right-panel">
          <ElCard title="异常样本列表" :loading="loading">
          <ElTable :data="filteredBadCases" border>
            <ElTableColumn prop="id" label="异常样本ID" width="150" />
            <ElTableColumn prop="task_id" label="归属分析任务ID" width="180" />
            <ElTableColumn prop="type" label="样本类型" width="120">
              <template #default="scope">
              {{ typeLabels[scope.row.type] || scope.row.type }}
              </template>
            </ElTableColumn>
            <ElTableColumn prop="severity" label="严重程度" width="100">
              <template #default="scope">
                <ElTag :type="severityColors[scope.row.severity]">
                  {{ severityLabels[scope.row.severity] }}
                </ElTag>
              </template>
            </ElTableColumn>
            <ElTableColumn prop="status" label="处理状态" width="110">
              <template #default="scope">
                <ElTag :type="statusColors[scope.row.status]">
                  {{ statusLabels[scope.row.status] }}
                </ElTag>
              </template>
            </ElTableColumn>
            <ElTableColumn prop="description" label="问题描述" />
            <ElTableColumn prop="created_at" label="创建时间" width="150">
              <template #default="scope">
                {{ formatDate(scope.row.created_at) }}
              </template>
            </ElTableColumn>
            <ElTableColumn label="操作" width="150">
              <template #default="scope">
                <ElSelect
                  :value="scope.row.status"
                  @change="(val) => handleStatusChange(scope.row.id, val)"
                  class="status-select"
                >
                  <ElOption v-for="opt in statusOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
                </ElSelect>
              </template>
            </ElTableColumn>
          </ElTable>
        </ElCard>
      </div>
    </div>
  </div>
</template>

<style scoped>
.badcase-page {
  max-width: 1400px;
  margin: 0 auto;
  padding: 20px;
}

.page-header {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 20px;
}

.back-btn {
  padding: 8px 16px;
  background: #f1f5f9;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
}

.back-btn:hover {
  background: #e2e8f0;
}

.page-header h1 {
  margin: 0;
  font-size: 24px;
}

.summary-cards {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 20px;
}

.summary-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px;
}

.summary-icon {
  font-size: 28px;
  width: 48px;
  height: 48px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 12px;
}

.summary-icon.total {
  background: rgba(37, 99, 235, 0.1);
}

.summary-icon.pending {
  background: rgba(245, 158, 11, 0.1);
}

.summary-icon.critical {
  background: rgba(239, 68, 68, 0.1);
}

.summary-icon.fixed {
  background: rgba(34, 197, 94, 0.1);
}

.summary-info {
  display: flex;
  flex-direction: column;
}

.summary-count {
  font-size: 24px;
  font-weight: 700;
  color: #0f172a;
}

.summary-label {
  font-size: 13px;
  color: #64748b;
}

.page-content {
  display: grid;
  grid-template-columns: 380px 1fr;
  gap: 20px;
}

.left-panel {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.right-panel {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.status-select {
  width: 120px;
  font-size: 12px;
}

@media (max-width: 1024px) {
  .summary-cards {
    grid-template-columns: repeat(2, 1fr);
  }

  .page-content {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 640px) {
  .summary-cards {
    grid-template-columns: 1fr;
  }
}

.textarea {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid #d9d9d9;
  border-radius: 8px;
  font-size: 14px;
  box-sizing: border-box;
  resize: vertical;
}

.textarea:focus {
  outline: none;
  border-color: #409eff;
}
</style>