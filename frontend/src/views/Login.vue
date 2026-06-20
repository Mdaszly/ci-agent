<script setup lang="ts">
import { reactive, ref } from 'vue';
import { useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import { login, register, setTokens, type UserInfo } from '../services/api';

const router = useRouter();

const isRegisterMode = ref(false);
const loading = ref(false);

const form = reactive({
  username: '',
  password: '',
});

const errorMessage = ref('');

const testAccounts = [
  { username: 'admin', password: 'admin', label: '管理员账号' },
  { username: 'user', password: 'user', label: '普通用户' },
];

async function handleSubmit() {
  if (!form.username.trim() || !form.password.trim()) {
    errorMessage.value = '请输入用户名和密码';
    return;
  }

  loading.value = true;
  errorMessage.value = '';

  try {
    if (isRegisterMode.value) {
      await register(form.username.trim(), form.password);
      await login(form.username.trim(), form.password);
    } else {
      await login(form.username.trim(), form.password);
    }
    ElMessage.success(isRegisterMode.value ? '注册成功' : '登录成功');
    router.push('/');
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : '操作失败';
  } finally {
    loading.value = false;
  }
}

function toggleMode() {
  isRegisterMode.value = !isRegisterMode.value;
  errorMessage.value = '';
}

function fillTestAccount(username: string, password: string) {
  form.username = username;
  form.password = password;
  errorMessage.value = '';
}
</script>

<template>
  <div class="login-page">
    <div class="login-card">
      <div class="brand-block">
        <div class="brand-kicker">Evidence-First Agent</div>
        <div class="brand-title">竞品情报决策工作台</div>
      </div>

      <form class="login-form" @submit.prevent="handleSubmit">
        <label class="field">
          <span class="field-label">用户名</span>
          <input
            v-model="form.username"
            type="text"
            class="field-input"
            placeholder="输入用户名"
            autocomplete="username"
          />
        </label>

        <label class="field">
          <span class="field-label">密码</span>
          <input
            v-model="form.password"
            type="password"
            class="field-input"
            placeholder="输入密码"
            autocomplete="current-password"
          />
        </label>

        <div v-if="!isRegisterMode" class="test-accounts">
          <span class="test-label">测试账号：</span>
          <button
            v-for="account in testAccounts"
            :key="account.username"
            type="button"
            class="test-account-btn"
            @click="fillTestAccount(account.username, account.password)"
          >
            {{ account.label }}
          </button>
        </div>

        <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>

        <button type="submit" class="submit-btn" :disabled="loading">
          {{ loading ? '处理中…' : isRegisterMode ? '注册并登录' : '登录' }}
        </button>

        <button type="button" class="toggle-btn" @click="toggleMode">
          {{ isRegisterMode ? '已有账号？去登录' : '没有账号？去注册' }}
        </button>
      </form>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background:
    radial-gradient(circle at top left, rgba(37, 99, 235, 0.08), transparent 28%),
    linear-gradient(180deg, #f8fafc 0%, #eef4ff 100%);
}

.login-card {
  width: 100%;
  max-width: 380px;
  background: rgba(255, 255, 255, 0.92);
  backdrop-filter: blur(12px);
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 16px;
  padding: 40px 32px;
  box-shadow: 0 12px 40px rgba(15, 23, 42, 0.08);
}

.brand-block {
  text-align: center;
  margin-bottom: 32px;
}

.brand-kicker {
  color: #2563eb;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  margin-bottom: 6px;
}

.brand-title {
  font-size: 20px;
  font-weight: 700;
  color: #0f172a;
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field-label {
  font-size: 13px;
  font-weight: 600;
  color: #475569;
}

.field-input {
  padding: 10px 14px;
  border: 1px solid rgba(148, 163, 184, 0.4);
  border-radius: 8px;
  font-size: 14px;
  color: #0f172a;
  background: #fff;
  transition: border-color 0.18s ease, box-shadow 0.18s ease;
}

.field-input:focus {
  outline: none;
  border-color: #2563eb;
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.12);
}

.error-text {
  margin: 0;
  font-size: 13px;
  color: #dc2626;
}

.submit-btn {
  padding: 11px 16px;
  border: none;
  border-radius: 8px;
  background: #2563eb;
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.18s ease, transform 0.18s ease;
}

.submit-btn:hover:not(:disabled) {
  background: #1d4ed8;
  transform: translateY(-1px);
}

.submit-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.toggle-btn {
  padding: 8px;
  border: none;
  background: transparent;
  color: #2563eb;
  font-size: 13px;
  cursor: pointer;
  text-align: center;
}

.toggle-btn:hover {
  text-decoration: underline;
}

.test-accounts {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 8px 12px;
  background: #f8fafc;
  border-radius: 6px;
}

.test-label {
  font-size: 12px;
  color: #64748b;
}

.test-account-btn {
  padding: 4px 10px;
  border: 1px solid #cbd5e1;
  border-radius: 4px;
  background: #fff;
  color: #334155;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.18s ease;
}

.test-account-btn:hover {
  background: #2563eb;
  border-color: #2563eb;
  color: #fff;
}
</style>
