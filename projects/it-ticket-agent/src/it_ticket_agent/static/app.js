const STORAGE_SESSION_KEY = 'it-ticket-console-session-id';
const STORAGE_TICKET_KEY = 'it-ticket-console-ticket-id';

const chatMessages = document.getElementById('chatMessages');
const messageForm = document.getElementById('messageForm');
const messageInput = document.getElementById('messageInput');
const userIdInput = document.getElementById('userId');
const serviceNameInput = document.getElementById('serviceName');
const clusterNameInput = document.getElementById('clusterName');
const namespaceNameInput = document.getElementById('namespaceName');

const approvalModal = document.getElementById('approvalModal');
const approvalContent = document.getElementById('approvalContent');
const approverIdInput = document.getElementById('approverId');
const approvalCommentInput = document.getElementById('approvalComment');
const approveBtn = document.getElementById('approveBtn');
const rejectBtn = document.getElementById('rejectBtn');
const closeModalBtn = document.getElementById('closeModalBtn');
const modalBackdrop = document.querySelector('.modal-backdrop');

const clearChatBtn = document.getElementById('clearChatBtn');
const reloadSessionBtn = document.getElementById('reloadSessionBtn');
const refreshRecoveryBtn = document.getElementById('refreshRecoveryBtn');

const sessionSummary = document.getElementById('sessionSummary');
const interruptSummary = document.getElementById('interruptSummary');
const interruptBadge = document.getElementById('interruptBadge');
const openApprovalPanelBtn = document.getElementById('openApprovalPanelBtn');

const clarificationForm = document.getElementById('clarificationForm');
const clarificationAnswerInput = document.getElementById('clarificationAnswer');
const clarificationSubmitBtn = document.getElementById('clarificationSubmitBtn');

const feedbackForm = document.getElementById('feedbackForm');
const feedbackResolutionSelect = document.getElementById('feedbackResolution');
const feedbackRejectOption = document.getElementById('feedbackRejectOption');
const feedbackCapabilityHint = document.getElementById('feedbackCapabilityHint');
const feedbackRejectFields = document.getElementById('feedbackRejectFields');
const feedbackRootCauseInput = document.getElementById('feedbackRootCause');
const feedbackCommentInput = document.getElementById('feedbackComment');
const feedbackSubmitBtn = document.getElementById('feedbackSubmitBtn');

const messageModeDefaultBtn = document.getElementById('messageModeDefault');
const messageModeSupplementBtn = document.getElementById('messageModeSupplement');
const composerModeHint = document.getElementById('composerModeHint');

const executionRecoveryPanel = document.getElementById('executionRecoveryPanel');

let currentSessionId = null;
let currentTicketId = null;
let currentPendingInterrupt = null;
let pendingApproval = null;
let pendingApprovalDiagnosis = null;
let currentMessageMode = 'default';

function safeStorageGet(key) {
  try {
    return window.localStorage.getItem(key);
  } catch (_error) {
    return null;
  }
}

function safeStorageSet(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch (_error) {
    // ignore storage failures in private mode
  }
}

function safeStorageRemove(key) {
  try {
    window.localStorage.removeItem(key);
  } catch (_error) {
    // ignore storage failures in private mode
  }
}

function genTicketId() {
  return `INC-${Date.now()}-${Math.floor(Math.random() * 1000)}`;
}

function getOrCreateTicketId() {
  if (!currentTicketId) {
    currentTicketId = safeStorageGet(STORAGE_TICKET_KEY) || genTicketId();
  }
  return currentTicketId;
}

function persistConversationSession() {
  if (currentSessionId) {
    safeStorageSet(STORAGE_SESSION_KEY, currentSessionId);
  }
  if (currentTicketId) {
    safeStorageSet(STORAGE_TICKET_KEY, currentTicketId);
  }
}

function clearPersistedConversationSession() {
  safeStorageRemove(STORAGE_SESSION_KEY);
  safeStorageRemove(STORAGE_TICKET_KEY);
}

function resetConversationSession() {
  currentSessionId = null;
  currentTicketId = null;
  currentPendingInterrupt = null;
  pendingApproval = null;
  pendingApprovalDiagnosis = null;
  clearPersistedConversationSession();
  closeApprovalModal();
  renderSessionSummary(null);
  renderInterruptPanel(null, null);
  renderExecutionRecovery(null);
  setComposerMode('default');
}

function addMessage(role, title, body) {
  const article = document.createElement('article');
  article.className = `message ${role}`;

  const meta = document.createElement('span');
  meta.className = 'message-meta';
  meta.textContent = title;

  const pre = document.createElement('pre');
  pre.textContent = body || '';

  article.appendChild(meta);
  article.appendChild(pre);
  chatMessages.appendChild(article);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function clearMessages() {
  chatMessages.innerHTML = '';
}

function toPrettyJson(value) {
  if (value == null || value === '') return '-';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch (_error) {
    return String(value);
  }
}

function parseJsonResponse(text) {
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch (_error) {
    return { message: text };
  }
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const text = await response.text();
  const data = parseJsonResponse(text);
  if (!response.ok) {
    throw new Error(data.detail || data.message || '请求失败');
  }
  return data;
}

function formatTimestamp(value) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString('zh-CN', { hour12: false });
}

function pillToneForStatus(status) {
  switch (String(status || '').toLowerCase()) {
    case 'completed':
    case 'answered':
      return 'success';
    case 'failed':
    case 'rejected':
    case 'cancelled':
    case 'expired':
      return 'danger';
    case 'awaiting_approval':
    case 'awaiting_clarification':
    case 'pending':
    case 'approval':
    case 'clarification':
    case 'feedback':
    case 'manual_intervention':
      return 'warning';
    default:
      return 'info';
  }
}

function setPanelEmpty(container, text) {
  container.innerHTML = '';
  container.textContent = text;
  container.classList.add('empty-state');
}

function appendDetailRow(container, label, value, { badge = false, tone = 'info', list = false } = {}) {
  if (value == null || value === '') return;

  container.classList.remove('empty-state');
  const row = document.createElement('div');
  row.className = 'detail-row';

  const strong = document.createElement('strong');
  strong.textContent = label;
  row.appendChild(strong);

  if (list && Array.isArray(value)) {
    const listElement = document.createElement('ul');
    value.forEach((item) => {
      if (!item) return;
      const li = document.createElement('li');
      li.textContent = String(item);
      listElement.appendChild(li);
    });
    row.appendChild(listElement);
  } else {
    const content = document.createElement('span');
    if (badge) {
      content.className = `status-pill ${tone}`;
    }
    content.textContent = String(value);
    row.appendChild(content);
  }

  container.appendChild(row);
}

function formatDiagnosis(diagnosis) {
  if (!diagnosis) return '';
  const lines = [];

  if (diagnosis.routing) {
    lines.push(`路由 Agent：${diagnosis.routing.agent_name || '-'}`);
    if (diagnosis.routing.route_source) lines.push(`路由路径：${diagnosis.routing.route_source}`);
    if (diagnosis.routing.reason) lines.push(`路由原因：${diagnosis.routing.reason}`);
  }
  if (diagnosis.execution_path) lines.push(`Agent 执行路径：${diagnosis.execution_path}`);
  if (diagnosis.summary) lines.push(`摘要：${diagnosis.summary}`);
  if (diagnosis.conclusion) lines.push(`结论：${diagnosis.conclusion}`);
  if (typeof diagnosis.confidence === 'number') lines.push(`置信度：${diagnosis.confidence}`);

  if (Array.isArray(diagnosis.findings) && diagnosis.findings.length > 0) {
    lines.push('关键发现：');
    diagnosis.findings.forEach((finding) => {
      lines.push(`- ${finding.title}: ${finding.detail}`);
    });
  }

  if (Array.isArray(diagnosis.tool_results) && diagnosis.tool_results.length > 0) {
    lines.push('工具执行：');
    diagnosis.tool_results.forEach((toolResult) => {
      lines.push(`- ${toolResult.tool_name} [${toolResult.status}]`);
      if (toolResult.summary) lines.push(`  · ${toolResult.summary}`);
      if (Array.isArray(toolResult.evidence)) {
        toolResult.evidence.slice(0, 2).forEach((item) => lines.push(`  · ${item}`));
      }
    });
  }

  if (Array.isArray(diagnosis.sources) && diagnosis.sources.length > 0) {
    lines.push('证据来源：');
    diagnosis.sources.forEach((source) => {
      lines.push(`- ${source.agent}: ${source.conclusion}`);
      if (Array.isArray(source.evidence)) {
        source.evidence.forEach((evidence) => lines.push(`  · ${evidence}`));
      }
    });
  }

  if (Array.isArray(diagnosis.recommended_actions) && diagnosis.recommended_actions.length > 0) {
    lines.push('建议动作：');
    diagnosis.recommended_actions.forEach((action) => {
      lines.push(`- ${action.action} [${action.risk}]`);
      if (action.reason) lines.push(`  · ${action.reason}`);
    });
  }

  if (diagnosis.approval) {
    lines.push('审批状态：');
    lines.push(`- ${diagnosis.approval.action}: ${diagnosis.approval.status}`);
  }

  if (diagnosis.execution) {
    lines.push('执行结果：');
    Object.entries(diagnosis.execution).forEach(([key, value]) => {
      lines.push(`- ${key}: ${Array.isArray(value) ? value.join(', ') : typeof value === 'object' && value ? JSON.stringify(value) : value}`);
    });
  }

  if (diagnosis.execution_limit) {
    lines.push('执行控制：');
    if (diagnosis.execution_limit.recovery_action) {
      lines.push(`- recovery_action: ${diagnosis.execution_limit.recovery_action}`);
    }
    if (Array.isArray(diagnosis.execution_limit.recovery_hints) && diagnosis.execution_limit.recovery_hints.length > 0) {
      diagnosis.execution_limit.recovery_hints.forEach((hint) => lines.push(`  · ${hint}`));
    }
  }

  if (diagnosis.feedback) {
    lines.push('人工反馈：');
    lines.push(`- human_verified: ${Boolean(diagnosis.feedback.human_verified)}`);
    if (diagnosis.feedback.actual_root_cause_hypothesis) {
      lines.push(`- actual_root_cause: ${diagnosis.feedback.actual_root_cause_hypothesis}`);
    }
  }

  return lines.join('\n');
}

function buildAssistantBody(message, diagnosis) {
  return `${message || ''}\n\n${formatDiagnosis(diagnosis)}`.trim();
}

function formatApprovalRequest(approvalRequest) {
  if (!approvalRequest) return '';
  const lines = [
    `审批单：${approvalRequest.approval_id}`,
    `动作：${approvalRequest.action}`,
    `风险：${approvalRequest.risk}`,
    `原因：${approvalRequest.reason}`,
  ];
  if (approvalRequest.params) {
    lines.push(`参数：${JSON.stringify(approvalRequest.params, null, 2)}`);
  }
  return lines.join('\n');
}

function appendApprovalRow(label, value, badge = false) {
  const row = document.createElement('div');
  row.className = 'approval-row';

  const strong = document.createElement('strong');
  strong.textContent = label;

  const content = document.createElement('span');
  if (badge) {
    content.className = 'system-badge';
  }
  content.textContent = value;

  row.appendChild(strong);
  row.appendChild(content);
  approvalContent.appendChild(row);
}

function buildApprovalContext(approvalRequest, pendingInterrupt, diagnosis) {
  if (!approvalRequest) return null;
  return {
    ...approvalRequest,
    interrupt_id: approvalRequest.interrupt_id || pendingInterrupt?.interrupt_id || null,
    diagnosis: diagnosis || null,
  };
}

function resolveLatestApprovalContext(turns = []) {
  for (let index = turns.length - 1; index >= 0; index -= 1) {
    const payload = turns[index]?.structured_payload || {};
    if (payload.approval_request) {
      return {
        approvalRequest: payload.approval_request,
        diagnosis: payload.diagnosis || null,
      };
    }
  }
  return { approvalRequest: null, diagnosis: null };
}

function openApprovalModal(approvalRequest, diagnosis) {
  if (!approvalRequest) return;
  pendingApproval = approvalRequest;
  pendingApprovalDiagnosis = diagnosis || null;
  approvalContent.innerHTML = '';
  appendApprovalRow('Ticket', approvalRequest.ticket_id);
  appendApprovalRow('动作', approvalRequest.action);
  appendApprovalRow('风险', approvalRequest.risk, true);
  appendApprovalRow('原因', approvalRequest.reason);
  appendApprovalRow('参数', JSON.stringify(approvalRequest.params || {}, null, 2));
  if (diagnosis && (diagnosis.summary || diagnosis.conclusion)) {
    appendApprovalRow('诊断', diagnosis.summary || diagnosis.conclusion);
  }
  approvalModal.classList.remove('hidden');
  approvalModal.setAttribute('aria-hidden', 'false');
}

function closeApprovalModal() {
  approvalModal.classList.add('hidden');
  approvalModal.setAttribute('aria-hidden', 'true');
}

function renderSessionSummary(session) {
  if (!session) {
    setPanelEmpty(sessionSummary, '当前无活跃会话');
    return;
  }

  currentSessionId = session.session_id;
  currentTicketId = session.ticket_id;
  persistConversationSession();

  if (session.user_id) userIdInput.value = session.user_id;
  if (session.incident_state?.service) serviceNameInput.value = session.incident_state.service;
  if (session.incident_state?.cluster) clusterNameInput.value = session.incident_state.cluster;
  if (session.incident_state?.namespace) namespaceNameInput.value = session.incident_state.namespace;

  sessionSummary.innerHTML = '';
  appendDetailRow(sessionSummary, 'Ticket', session.ticket_id);
  appendDetailRow(sessionSummary, 'Session', session.session_id);
  appendDetailRow(sessionSummary, '状态', session.status, { badge: true, tone: pillToneForStatus(session.status) });
  appendDetailRow(sessionSummary, '阶段', session.current_stage, { badge: true, tone: pillToneForStatus(session.current_stage) });
  appendDetailRow(sessionSummary, '当前 Agent', session.current_agent || '-');
  appendDetailRow(sessionSummary, '最近活跃', formatTimestamp(session.last_active_at));
  appendDetailRow(sessionSummary, 'Checkpoint', session.last_checkpoint_id || '-');
}

function resetInterruptForms() {
  clarificationForm.classList.add('hidden');
  feedbackForm.classList.add('hidden');
  openApprovalPanelBtn.classList.add('hidden');
  interruptBadge.classList.add('hidden');
  clarificationAnswerInput.value = '';
  feedbackResolutionSelect.value = 'accept';
  feedbackRootCauseInput.value = '';
  feedbackCommentInput.value = '';
  feedbackRejectFields.classList.add('hidden');
  feedbackCapabilityHint.textContent = '';
  feedbackRejectOption.disabled = false;
}

function feedbackSupportsRejectReopen(interrupt) {
  return Boolean(
    interrupt?.metadata?.can_reject_reopen
    || interrupt?.metadata?.action_name
    || interrupt?.metadata?.approval_present
  );
}

function updateFeedbackResolutionUI() {
  const rejectMode = feedbackResolutionSelect.value === 'reject_reopen';
  feedbackRejectFields.classList.toggle('hidden', !rejectMode);
}

function renderInterruptPanel(interrupt, approvalContext) {
  currentPendingInterrupt = interrupt || null;
  pendingApproval = approvalContext || null;
  pendingApprovalDiagnosis = approvalContext?.diagnosis || null;

  interruptSummary.innerHTML = '';
  resetInterruptForms();

  if (!interrupt) {
    setPanelEmpty(interruptSummary, '当前无待处理人工节点');
    return;
  }

  interruptBadge.textContent = interrupt.type;
  interruptBadge.className = `status-pill ${pillToneForStatus(interrupt.type)}`;
  interruptBadge.classList.remove('hidden');

  appendDetailRow(interruptSummary, '类型', interrupt.type, { badge: true, tone: pillToneForStatus(interrupt.type) });
  appendDetailRow(interruptSummary, '状态', interrupt.status, { badge: true, tone: pillToneForStatus(interrupt.status) });
  appendDetailRow(interruptSummary, '原因', interrupt.reason || '-');
  appendDetailRow(interruptSummary, '问题', interrupt.question || '-');
  appendDetailRow(interruptSummary, '创建时间', formatTimestamp(interrupt.created_at));

  if (interrupt.type === 'approval' && approvalContext) {
    appendDetailRow(interruptSummary, '审批动作', approvalContext.action || '-');
    appendDetailRow(interruptSummary, '风险', approvalContext.risk || '-', { badge: true, tone: pillToneForStatus(approvalContext.risk) });
    appendDetailRow(interruptSummary, '审批说明', approvalContext.reason || '-');
    appendDetailRow(interruptSummary, '参数', toPrettyJson(approvalContext.params || {}));
    openApprovalPanelBtn.classList.remove('hidden');
  }

  if (interrupt.type === 'clarification') {
    clarificationForm.classList.remove('hidden');
  }

  if (interrupt.type === 'feedback') {
    const canRejectReopen = feedbackSupportsRejectReopen(interrupt);
    const actionName = interrupt?.metadata?.action_name || '-';
    appendDetailRow(interruptSummary, '当前建议动作', actionName);
    appendDetailRow(interruptSummary, '可拒绝重开', canRejectReopen ? '是' : '否', {
      badge: true,
      tone: canRejectReopen ? 'warning' : 'info',
    });
    feedbackRejectOption.disabled = !canRejectReopen;
    feedbackCapabilityHint.textContent = canRejectReopen
      ? `当前建议动作：${actionName}。如果你不接受当前结论，可以拒绝并重新分析。`
      : '当前诊断没有给出建议动作或审批结果，只支持接受当前结论。';
    feedbackResolutionSelect.value = 'accept';
    updateFeedbackResolutionUI();
    feedbackForm.classList.remove('hidden');
  }
}

function renderExecutionRecovery(recovery) {
  executionRecoveryPanel.innerHTML = '';

  if (!currentSessionId) {
    setPanelEmpty(executionRecoveryPanel, '当前无执行恢复需求');
    return;
  }

  if (!recovery || recovery.recovery_action === 'none') {
    setPanelEmpty(executionRecoveryPanel, '当前无执行恢复需求');
    return;
  }

  appendDetailRow(executionRecoveryPanel, '恢复动作', recovery.recovery_action, {
    badge: true,
    tone: pillToneForStatus(recovery.recovery_action),
  });
  appendDetailRow(executionRecoveryPanel, '原因', recovery.reason || '-');
  appendDetailRow(executionRecoveryPanel, 'failed_step_id', recovery.failed_step_id || '-');
  appendDetailRow(executionRecoveryPanel, 'resume_from_step_id', recovery.resume_from_step_id || '-');
  appendDetailRow(executionRecoveryPanel, 'last_completed_step_id', recovery.last_completed_step_id || '-');

  if (recovery.execution_plan) {
    const failedStep = (recovery.execution_plan.steps || []).find((step) => step.step_id === recovery.failed_step_id);
    appendDetailRow(executionRecoveryPanel, '计划状态', recovery.execution_plan.status, {
      badge: true,
      tone: pillToneForStatus(recovery.execution_plan.status),
    });
    appendDetailRow(executionRecoveryPanel, '计划摘要', recovery.execution_plan.summary || '-');
    if (failedStep) {
      appendDetailRow(executionRecoveryPanel, '失败动作', failedStep.action || '-');
      appendDetailRow(executionRecoveryPanel, '失败摘要', failedStep.result_summary || '-');
      if (Array.isArray(failedStep.evidence) && failedStep.evidence.length > 0) {
        appendDetailRow(executionRecoveryPanel, '失败证据', failedStep.evidence.slice(0, 3), { list: true });
      }
    }
  }

  if (recovery.latest_checkpoint) {
    appendDetailRow(executionRecoveryPanel, '最新 checkpoint', recovery.latest_checkpoint.stage || '-');
    appendDetailRow(executionRecoveryPanel, 'checkpoint next_action', recovery.latest_checkpoint.next_action || '-');
  }

  if (Array.isArray(recovery.recovery_hints) && recovery.recovery_hints.length > 0) {
    appendDetailRow(executionRecoveryPanel, '人工接手提示', recovery.recovery_hints, { list: true });
  }

  appendDetailRow(
    executionRecoveryPanel,
    '当前策略',
    '当前阶段不支持自动恢复，请人工确认外部资源状态、幂等性和副作用后再继续处理。'
  );
}

async function refreshExecutionRecovery() {
  if (!currentSessionId) {
    renderExecutionRecovery(null);
    return;
  }

  try {
    const recovery = await fetchJson(`/api/v1/sessions/${currentSessionId}/execution-recovery`);
    renderExecutionRecovery(recovery);
  } catch (error) {
    renderExecutionRecovery(null);
    if (!String(error.message || '').includes('not found')) {
      addMessage('system', '恢复面板', `刷新执行恢复信息失败：${error.message}`);
    }
  }
}

function renderTurn(turn, session) {
  const payload = turn.structured_payload || {};
  if (turn.role === 'assistant') {
    addMessage('agent', '罗伯特🤖', buildAssistantBody(turn.content, payload.diagnosis));
    if (payload.approval_request) {
      addMessage(
        'approval',
        '审批请求',
        `检测到高风险动作，需要人工审批。\n\n${formatApprovalRequest(payload.approval_request)}`
      );
    }
    return;
  }

  if (turn.role === 'user') {
    addMessage('user', session?.user_id || '用户', turn.content);
    return;
  }

  addMessage('system', turn.role || '系统', turn.content);
}

async function hydrateConversation(detail, { announce = false } = {}) {
  clearMessages();
  detail.turns.forEach((turn) => renderTurn(turn, detail.session));
  renderSessionSummary(detail.session);

  const latestApproval = resolveLatestApprovalContext(detail.turns || []);
  const approvalContext = buildApprovalContext(
    latestApproval.approvalRequest,
    detail.pending_interrupt,
    latestApproval.diagnosis
  );
  renderInterruptPanel(detail.pending_interrupt, approvalContext);
  await refreshExecutionRecovery();

  if (announce) {
    addMessage(
      'system',
      '系统',
      `已恢复最近会话：${detail.session.ticket_id}\n状态：${detail.session.status}\n阶段：${detail.session.current_stage}`
    );
  }
}

async function loadConversation(sessionId, { announce = false } = {}) {
  const detail = await fetchJson(`/api/v1/conversations/${sessionId}`);
  await hydrateConversation(detail, { announce });
}

function buildFeedbackPayload() {
  const resolution = feedbackResolutionSelect.value;
  const canRejectReopen = feedbackSupportsRejectReopen(currentPendingInterrupt);
  if (resolution === 'reject_reopen') {
    if (!canRejectReopen) {
      throw new Error('当前结果不支持拒绝并重新分析');
    }
    const actualRootCause = feedbackRootCauseInput.value.trim();
    const comment = feedbackCommentInput.value.trim();
    if (!actualRootCause && !comment) {
      throw new Error('拒绝并重新分析时，至少填写新的根因判断或补充说明');
    }
    return {
      human_verified: false,
      actual_root_cause_hypothesis: actualRootCause,
      comment,
    };
  }
  return {
    human_verified: true,
    actual_root_cause_hypothesis: '',
    comment: feedbackCommentInput.value.trim() || '',
  };
}

function setComposerMode(mode) {
  currentMessageMode = mode === 'supplement' ? 'supplement' : 'default';
  messageModeDefaultBtn.classList.toggle('active', currentMessageMode === 'default');
  messageModeSupplementBtn.classList.toggle('active', currentMessageMode === 'supplement');
  composerModeHint.textContent = currentMessageMode === 'supplement'
    ? '补充模式：把这条消息作为当前诊断的增量线索，不新开问题。'
    : '默认模式：把这条消息作为当前会话的正常追问或继续诊断。';
}

async function handleMutationResponse(data, { autoOpenApproval = true } = {}) {
  renderSessionSummary(data.session);

  const approvalContext = buildApprovalContext(data.approval_request, data.pending_interrupt, data.diagnosis);
  if (data.message || data.diagnosis) {
    addMessage('agent', '罗伯特🤖', buildAssistantBody(data.message, data.diagnosis));
  }

  if (approvalContext) {
    addMessage(
      'approval',
      '审批请求',
      `检测到高风险动作，需要人工审批。\n\n${formatApprovalRequest(approvalContext)}`
    );
  }

  if (data.pending_interrupt?.type === 'clarification') {
    addMessage('system', '待补充信息', data.pending_interrupt.question || data.message);
  }

  if (data.pending_interrupt?.type === 'feedback') {
    addMessage(
      'system',
      '待人工确认',
      `${data.pending_interrupt.question || '请确认诊断结果'}\n\n${data.pending_interrupt.reason || ''}`.trim()
    );
  }

  renderInterruptPanel(data.pending_interrupt, approvalContext);
  await refreshExecutionRecovery();

  if (autoOpenApproval && approvalContext && data.pending_interrupt?.type === 'approval') {
    openApprovalModal(approvalContext, data.diagnosis);
  }
}

async function submitClarification(event) {
  event.preventDefault();
  if (!currentSessionId || !currentPendingInterrupt) return;

  const answer = clarificationAnswerInput.value.trim();
  if (!answer) {
    alert('请先填写澄清信息');
    return;
  }

  clarificationSubmitBtn.disabled = true;
  try {
    addMessage('user', userIdInput.value.trim() || '用户', answer);
    const data = await fetchJson(`/api/v1/conversations/${currentSessionId}/resume`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        interrupt_id: currentPendingInterrupt.interrupt_id,
        answer_payload: { text: answer },
      }),
    });
    clarificationAnswerInput.value = '';
    await handleMutationResponse(data);
  } catch (error) {
    addMessage('system', '系统错误', error.message);
  } finally {
    clarificationSubmitBtn.disabled = false;
  }
}

async function submitFeedback(event) {
  event.preventDefault();
  if (!currentSessionId || !currentPendingInterrupt) return;

  feedbackSubmitBtn.disabled = true;
  try {
    const payload = buildFeedbackPayload();
    addMessage(
      'user',
      userIdInput.value.trim() || '人工审核',
      payload.human_verified
        ? '人工反馈：接受当前建议'
        : `人工反馈：拒绝并重新分析${payload.actual_root_cause_hypothesis ? `\n新的根因判断：${payload.actual_root_cause_hypothesis}` : ''}${payload.comment ? `\n补充说明：${payload.comment}` : ''}`
    );
    const data = await fetchJson(`/api/v1/conversations/${currentSessionId}/resume`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        interrupt_id: currentPendingInterrupt.interrupt_id,
        answer_payload: payload,
      }),
    });
    await handleMutationResponse(data, { autoOpenApproval: false });
  } catch (error) {
    alert(error.message);
  } finally {
    feedbackSubmitBtn.disabled = false;
  }
}

async function submitDecision(approved) {
  if (!pendingApproval || !currentSessionId) return;

  const approverId = approverIdInput.value.trim();
  if (!approverId) {
    alert('请填写审批人');
    return;
  }

  approveBtn.disabled = true;
  rejectBtn.disabled = true;

  try {
    const comment = approvalCommentInput.value.trim() || null;
    const data = await fetchJson(`/api/v1/conversations/${currentSessionId}/resume`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        interrupt_id: pendingApproval.interrupt_id || currentPendingInterrupt?.interrupt_id || null,
        approval_id: pendingApproval.approval_id,
        approved,
        approver_id: approverId,
        comment,
      }),
    });

    addMessage(
      'approval',
      '审批结果',
      `${approved ? '已批准' : '已拒绝'}：${pendingApproval.action}\n审批人：${approverId}\n备注：${comment || '无'}`
    );
    approvalCommentInput.value = '';
    closeApprovalModal();
    pendingApproval = null;
    await handleMutationResponse(data, { autoOpenApproval: false });
  } catch (error) {
    alert(error.message);
  } finally {
    approveBtn.disabled = false;
    rejectBtn.disabled = false;
  }
}

async function handleUserMessage(event) {
  event.preventDefault();
  const message = messageInput.value.trim();
  if (!message) return;

  const basePayload = {
    user_id: userIdInput.value.trim() || 'zhangsan',
    message,
    service: serviceNameInput.value.trim() || null,
    cluster: clusterNameInput.value.trim() || 'prod-shanghai-1',
    namespace: namespaceNameInput.value.trim() || 'default',
  };

  const userMessageTitle = currentSessionId && currentMessageMode === 'supplement'
    ? `${basePayload.user_id}（补充）`
    : basePayload.user_id;
  addMessage('user', userMessageTitle, message);
  messageInput.value = '';

  try {
    const data = await fetchJson(
      currentSessionId ? `/api/v1/conversations/${currentSessionId}/messages` : '/api/v1/conversations',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(
          currentSessionId
            ? { message, message_mode: currentMessageMode }
            : { ...basePayload, ticket_id: getOrCreateTicketId() }
        ),
      }
    );
    await handleMutationResponse(data);
    setComposerMode('default');
  } catch (error) {
    addMessage('system', '系统错误', error.message);
  }
}

async function restoreConversationFromStorage() {
  const storedSessionId = safeStorageGet(STORAGE_SESSION_KEY);
  const storedTicketId = safeStorageGet(STORAGE_TICKET_KEY);
  if (!storedSessionId) return false;

  currentSessionId = storedSessionId;
  currentTicketId = storedTicketId;

  try {
    await loadConversation(storedSessionId, { announce: true });
    return true;
  } catch (error) {
    resetConversationSession();
    addMessage('system', '系统', `恢复最近会话失败：${error.message}`);
    return false;
  }
}

function renderInitialGreeting() {
  addMessage(
    'agent',
    '罗伯特🤖',
    '你好，我是 IT 工单机器人。请输入你的问题；如果涉及高风险操作，我会弹出审批卡片，并在右侧同步展示澄清、人工确认和执行恢复信息。'
  );
}

messageForm.addEventListener('submit', handleUserMessage);
clarificationForm.addEventListener('submit', submitClarification);
feedbackForm.addEventListener('submit', submitFeedback);
feedbackResolutionSelect.addEventListener('change', updateFeedbackResolutionUI);
approveBtn.addEventListener('click', () => submitDecision(true));
rejectBtn.addEventListener('click', () => submitDecision(false));
closeModalBtn.addEventListener('click', closeApprovalModal);
modalBackdrop.addEventListener('click', closeApprovalModal);
messageModeDefaultBtn.addEventListener('click', () => setComposerMode('default'));
messageModeSupplementBtn.addEventListener('click', () => setComposerMode('supplement'));

openApprovalPanelBtn.addEventListener('click', () => {
  if (pendingApproval) {
    openApprovalModal(pendingApproval, pendingApprovalDiagnosis);
  }
});

reloadSessionBtn.addEventListener('click', async () => {
  const targetSessionId = currentSessionId || safeStorageGet(STORAGE_SESSION_KEY);
  if (!targetSessionId) {
    alert('当前没有可恢复的会话');
    return;
  }
  try {
    await loadConversation(targetSessionId, { announce: true });
  } catch (error) {
    addMessage('system', '系统错误', error.message);
  }
});

refreshRecoveryBtn.addEventListener('click', () => {
  refreshExecutionRecovery();
});

clearChatBtn.addEventListener('click', () => {
  clearMessages();
  resetConversationSession();
  renderInitialGreeting();
});

document.querySelectorAll('.quick-fill').forEach((button) => {
  button.addEventListener('click', () => {
    messageInput.value = button.dataset.message || '';
    messageInput.focus();
  });
});

async function boot() {
  renderSessionSummary(null);
  renderInterruptPanel(null, null);
  renderExecutionRecovery(null);
  setComposerMode('default');

  const restored = await restoreConversationFromStorage();
  if (!restored) {
    renderInitialGreeting();
  }
}

boot();
