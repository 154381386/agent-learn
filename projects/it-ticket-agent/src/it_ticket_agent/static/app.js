const STORAGE_SESSION_KEY = 'it-ticket-console-session-id';
const STORAGE_TICKET_KEY = 'it-ticket-console-ticket-id';

const pageNav = document.getElementById('pageNav');
const chatMessages = document.getElementById('chatMessages');
const agentActivityPanel = document.getElementById('agentActivityPanel');
const agentActivityText = document.getElementById('agentActivityText');
const agentActivityLog = document.getElementById('agentActivityLog');
const messageForm = document.getElementById('messageForm');
const messageInput = document.getElementById('messageInput');
const userIdInput = document.getElementById('userId');
const serviceNameInput = document.getElementById('serviceName');
const clusterNameInput = document.getElementById('clusterName');
const namespaceNameInput = document.getElementById('namespaceName');
const environmentNameInput = document.getElementById('environmentName');
const mockWorldSelect = document.getElementById('mockWorldSelect');
const mockWorldSummary = document.getElementById('mockWorldSummary');
const sendBtn = document.getElementById('sendBtn');

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
const recentSessions = document.getElementById('recentSessions');
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

const workspaceTabs = document.getElementById('workspaceTabs');
const refreshWorkspaceBtn = document.getElementById('refreshWorkspaceBtn');
const sessionMemoryPanel = document.getElementById('sessionMemoryPanel');
const agentEventsPanel = document.getElementById('agentEventsPanel');
const contextSnapshotPanel = document.getElementById('contextSnapshotPanel');
const diagnosisTimeline = document.getElementById('diagnosisTimeline');
const playbookStatusFilter = document.getElementById('playbookStatusFilter');
const refreshPlaybooksBtn = document.getElementById('refreshPlaybooksBtn');
const playbookList = document.getElementById('playbookList');
const playbookDetail = document.getElementById('playbookDetail');
const playbookReviewBtn = document.getElementById('playbookReviewBtn');
const playbookRejectBtn = document.getElementById('playbookRejectBtn');
const caseStatusFilter = document.getElementById('caseStatusFilter');
const refreshCasesBtn = document.getElementById('refreshCasesBtn');
const caseList = document.getElementById('caseList');
const caseDetail = document.getElementById('caseDetail');
const caseVerifyBtn = document.getElementById('caseVerifyBtn');
const caseExtractPlaybookBtn = document.getElementById('caseExtractPlaybookBtn');
const caseRejectBtn = document.getElementById('caseRejectBtn');
const badCaseStatusFilter = document.getElementById('badCaseStatusFilter');
const refreshBadCasesBtn = document.getElementById('refreshBadCasesBtn');
const badCaseList = document.getElementById('badCaseList');
const badCaseDetail = document.getElementById('badCaseDetail');
const badCaseExportBtn = document.getElementById('badCaseExportBtn');
const badCaseIgnoreBtn = document.getElementById('badCaseIgnoreBtn');

let currentSessionId = null;
let currentTicketId = null;
let currentPendingInterrupt = null;
let pendingApproval = null;
let pendingApprovalDiagnosis = null;
let currentMessageMode = 'default';
let currentPageView = 'chat';
let activityTimer = null;
let activityPolling = false;
let activityLastEventId = null;
let activityHideTimer = null;
let activityRenderedEventIds = new Set();
let currentSessionDetail = null;
let latestSystemEvents = [];
let latestRuntimeSnapshot = null;
let selectedPlaybook = null;
let selectedCase = null;
let selectedBadCase = null;
let mockWorlds = [];
let selectedMockWorld = null;

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


function setPageView(view, { updateHash = true } = {}) {
  currentPageView = view === 'workspace' ? 'workspace' : 'chat';
  document.querySelectorAll('[data-page-view-panel]').forEach((panel) => {
    panel.classList.toggle('active', panel.dataset.pageViewPanel === currentPageView);
  });
  document.querySelectorAll('[data-page-view]').forEach((button) => {
    button.classList.toggle('active', button.dataset.pageView === currentPageView);
  });
  if (updateHash) {
    window.location.hash = currentPageView === 'workspace' ? 'workspace' : 'chat';
  }
  if (currentPageView === 'workspace') {
    refreshWorkspaceData();
  } else {
    messageInput.focus();
  }
}

function resolveInitialPageView() {
  return window.location.hash.replace('#', '') === 'workspace' ? 'workspace' : 'chat';
}

function setAgentActivity(text) {
  if (!agentActivityPanel || !agentActivityText) return;
  agentActivityText.textContent = text;
  agentActivityPanel.classList.remove('hidden');
}

function formatAgentActivityFromEvent(event) {
  const eventType = String(event?.event_type || '');
  const payload = event?.payload || {};
  const metadata = event?.metadata || {};
  const toolName = payload.tool_name || payload.tool || payload.action || metadata.tool_name || metadata.action;
  if (eventType === 'tool.started' && toolName) return `正在调用 ${toolName}`;
  if (eventType === 'tool.completed' && toolName) return `已完成 ${toolName}，继续分析`;
  if (eventType === 'tool.cached' && toolName) return `复用 ${toolName} 结果，继续分析`;
  if (eventType === 'tool.failed' && toolName) return `${toolName} 调用失败，正在调整`;
  if (toolName) return `正在处理 ${toolName}`;
  if (eventType === 'knowledge.retrieved') return `正在检索知识库：${payload.hit_count || 0} 个命中`;
  if (eventType === 'context.collected') {
    const domains = Array.isArray(payload.matched_tool_domains) ? payload.matched_tool_domains.join(', ') : '';
    return domains ? `正在收集上下文，准备 ${domains} 工具` : '正在收集诊断上下文';
  }
  if (eventType === 'message.received') return '正在解析工单信息';
  if (eventType.includes('approval')) return '正在处理审批节点';
  if (eventType.includes('execution')) return '正在执行已批准动作';
  if (eventType === 'conversation.closed') return '诊断已完成，正在整理结果';
  return `正在处理：${eventType || '诊断任务'}`;
}

function shouldShowActivityEvent(event) {
  const eventType = String(event?.event_type || '');
  return [
    'knowledge.retrieved',
    'context.collected',
    'tool.started',
    'tool.completed',
    'tool.cached',
    'tool.failed',
    'conversation.closed',
  ].includes(eventType);
}

function resetAgentActivityLog() {
  activityRenderedEventIds = new Set();
  if (!agentActivityLog) return;
  agentActivityLog.innerHTML = '';
  agentActivityLog.classList.add('hidden');
}

function appendActivityLogItem(text, eventType = '') {
  if (!agentActivityLog || !text) return;
  const item = document.createElement('div');
  item.className = 'activity-log-item';
  if (eventType) item.dataset.eventType = eventType;
  item.textContent = text;
  agentActivityLog.appendChild(item);
  agentActivityLog.classList.remove('hidden');
  while (agentActivityLog.children.length > 10) {
    agentActivityLog.removeChild(agentActivityLog.firstElementChild);
  }
}

function renderAgentActivityEvents(events) {
  if (!Array.isArray(events)) return;
  events.forEach((event) => {
    if (!shouldShowActivityEvent(event)) return;
    const eventId = event.event_id || `${event.event_type}-${event.created_at || ''}-${JSON.stringify(event.payload || {})}`;
    if (activityRenderedEventIds.has(eventId)) return;
    activityRenderedEventIds.add(eventId);
    appendActivityLogItem(formatAgentActivityFromEvent(event), event.event_type);
  });
}

async function pollAgentActivity() {
  if (!currentSessionId || activityPolling) {
    if (!currentSessionId) setAgentActivity('正在执行诊断工具...');
    return;
  }
  activityPolling = true;
  try {
    const events = await fetchJson(`/api/v1/sessions/${currentSessionId}/events?limit=12`);
    renderAgentActivityEvents(events);
    if (Array.isArray(events) && events.length > 0) {
      const latest = events[events.length - 1];
      if (latest.event_id !== activityLastEventId) {
        activityLastEventId = latest.event_id;
        setAgentActivity(formatAgentActivityFromEvent(latest));
      }
    }
  } catch (_error) {
    setAgentActivity('正在等待诊断结果...');
  } finally {
    activityPolling = false;
  }
}

function startAgentActivity(text = '正在诊断...') {
  if (activityHideTimer) {
    window.clearTimeout(activityHideTimer);
    activityHideTimer = null;
  }
  activityLastEventId = null;
  setAgentActivity(text);
  if (activityTimer) window.clearInterval(activityTimer);
  activityTimer = window.setInterval(() => {
    pollAgentActivity();
  }, 1200);
}

function stopAgentActivity(text = '诊断完成') {
  if (activityTimer) {
    window.clearInterval(activityTimer);
    activityTimer = null;
  }
  setAgentActivity(text);
}

function startNewConversation() {
  if (activityTimer) {
    window.clearInterval(activityTimer);
    activityTimer = null;
  }
  if (activityHideTimer) {
    window.clearTimeout(activityHideTimer);
    activityHideTimer = null;
  }
  agentActivityPanel?.classList.add('hidden');
  resetAgentActivityLog();
  clearMessages();
  resetConversationSession();
  setPageView('chat');
  renderInitialGreeting();
  loadRecentSessions();
}

function resetConversationSession() {
  currentSessionId = null;
  currentTicketId = null;
  currentPendingInterrupt = null;
  pendingApproval = null;
  pendingApprovalDiagnosis = null;
  currentSessionDetail = null;
  latestSystemEvents = [];
  latestRuntimeSnapshot = null;
  resetAgentActivityLog();
  clearPersistedConversationSession();
  closeApprovalModal();
  renderSessionSummary(null);
  renderInterruptPanel(null, null);
  renderExecutionRecovery(null);
  renderSessionInspector(null);
  renderDiagnosisTimeline(null);
  setComposerMode('default');
}

function appendMessageArticle(article, title, bodyNode) {
  const meta = document.createElement('span');
  meta.className = 'message-meta';
  meta.textContent = title;

  article.appendChild(meta);
  article.appendChild(bodyNode);
  chatMessages.appendChild(article);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function createPlainMessageBody(body) {
  const pre = document.createElement('pre');
  pre.textContent = body || '';
  return pre;
}

function addMessage(role, title, body) {
  const article = document.createElement('article');
  article.className = `message ${role}`;
  appendMessageArticle(article, title, createPlainMessageBody(body));
}

function createReportField(label, value, { wide = false, tone = '' } = {}) {
  const item = document.createElement('div');
  item.className = `report-field${wide ? ' wide' : ''}${tone ? ` ${tone}` : ''}`;
  const key = document.createElement('span');
  key.textContent = label;
  const content = document.createElement('strong');
  content.textContent = value == null || value === '' ? '-' : String(value);
  item.appendChild(key);
  item.appendChild(content);
  return item;
}

function createReportList(items, emptyText) {
  const list = document.createElement('ol');
  list.className = 'report-list';
  const values = Array.isArray(items) ? items.filter(Boolean) : [];
  if (values.length === 0) {
    const empty = document.createElement('li');
    empty.className = 'muted-list-item';
    empty.textContent = emptyText;
    list.appendChild(empty);
    return list;
  }
  values.slice(0, 8).forEach((item) => {
    const li = document.createElement('li');
    li.textContent = String(item);
    list.appendChild(li);
  });
  return list;
}

function createReportSection(title, bodyNode, { tone = '' } = {}) {
  const section = document.createElement('section');
  section.className = `report-section${tone ? ` ${tone}` : ''}`;
  const heading = document.createElement('h4');
  heading.textContent = title;
  section.appendChild(heading);
  section.appendChild(bodyNode);
  return section;
}

function buildDiagnosisReportCard(message, diagnosis) {
  const userReport = diagnosis?.user_report || {};
  const rootCause = userReport.root_cause || diagnosis?.conclusion || message || '当前还没有明确根因判断';
  const evidence = userReport.evidence || diagnosis?.evidence || [];
  const ruledOut = userReport.ruled_out || diagnosis?.ruled_out || [];
  const recommendedActions = userReport.recommended_actions || diagnosis?.recommended_actions || [];
  const approvalExplanation = userReport.approval_explanation || diagnosis?.approval_explanation || '';
  const confidence = userReport.confidence ?? diagnosis?.confidence;
  const stopReason = userReport.stop_reason || diagnosis?.stop_reason || '';
  const toolCallsUsed = diagnosis?.tool_calls_used ?? diagnosis?.react_runtime?.tool_calls_used;

  const card = document.createElement('div');
  card.className = 'diagnosis-report-card';

  const hero = document.createElement('div');
  hero.className = 'report-hero';
  const heroText = document.createElement('div');
  const eyebrow = document.createElement('span');
  eyebrow.className = 'eyebrow';
  eyebrow.textContent = '诊断报告';
  const title = document.createElement('h3');
  title.textContent = rootCause;
  heroText.appendChild(eyebrow);
  heroText.appendChild(title);
  const workspaceBtn = document.createElement('button');
  workspaceBtn.className = 'ghost-btn small-btn';
  workspaceBtn.type = 'button';
  workspaceBtn.textContent = '查看工作台';
  workspaceBtn.addEventListener('click', () => setPageView('workspace'));
  hero.appendChild(heroText);
  hero.appendChild(workspaceBtn);
  card.appendChild(hero);

  const fields = document.createElement('div');
  fields.className = 'report-field-grid';
  fields.appendChild(createReportField('根因判断', rootCause, { wide: true, tone: 'root-cause' }));
  fields.appendChild(createReportField('置信度', confidence == null || confidence === '' ? '-' : Number(confidence).toFixed(2)));
  fields.appendChild(createReportField('工具调用', toolCallsUsed == null ? '-' : `${toolCallsUsed} 次`));
  fields.appendChild(createReportField('停止原因', stopReason || '-'));
  fields.appendChild(createReportField('审批状态', approvalExplanation ? '未触发执行审批' : '无审批信息', { tone: 'approval-state' }));
  card.appendChild(fields);

  card.appendChild(createReportSection('关键证据', createReportList(evidence, '当前还没有收集到足够强的异常证据。'), { tone: 'evidence' }));

  if (Array.isArray(ruledOut) && ruledOut.length > 0) {
    card.appendChild(createReportSection('已排除 / 优先级降低', createReportList(ruledOut, '暂无排除项。'), { tone: 'ruled-out' }));
  }

  card.appendChild(createReportSection('建议下一步', createReportList(recommendedActions, '暂无明确建议动作。'), { tone: 'actions' }));

  if (approvalExplanation) {
    const approval = document.createElement('p');
    approval.className = 'approval-explanation';
    approval.textContent = approvalExplanation;
    card.appendChild(createReportSection('为什么没有弹出执行审批', approval, { tone: 'approval' }));
  }

  return card;
}

function diagnosisHasMockWorld(diagnosis) {
  const sharedContext = diagnosis?.incident_state?.shared_context || {};
  const mockResponses = sharedContext.mock_tool_responses || {};
  return Boolean(mockResponses && Object.keys(mockResponses).length > 0);
}

function shouldRenderAssistantAsPlainChat(diagnosis) {
  return Boolean(selectedMockWorld || diagnosisHasMockWorld(diagnosis));
}

function addAssistantMessage(message, diagnosis) {
  if (diagnosis?.display_mode === 'user_report' && !shouldRenderAssistantAsPlainChat(diagnosis)) {
    const article = document.createElement('article');
    article.className = 'message agent report-message';
    appendMessageArticle(article, '罗伯特🤖', buildDiagnosisReportCard(message, diagnosis));
    return;
  }
  addMessage('agent', '罗伯特🤖', buildAssistantBody(message, diagnosis));
}

function buildTicketSummaryCard(payload, { compact = false } = {}) {
  const card = document.createElement('div');
  card.className = `ticket-summary-card${compact ? ' compact' : ''}`;
  const heading = document.createElement('div');
  heading.className = 'ticket-summary-heading';
  const eyebrow = document.createElement('span');
  eyebrow.className = 'eyebrow';
  eyebrow.textContent = compact ? '补充信息' : '诊断请求';
  const title = document.createElement('h3');
  title.textContent = payload.message || '-';
  heading.appendChild(eyebrow);
  heading.appendChild(title);
  card.appendChild(heading);

  const grid = document.createElement('div');
  grid.className = 'ticket-summary-grid';
  grid.appendChild(createReportField('用户', payload.user_id || '-'));
  grid.appendChild(createReportField('服务', payload.service || '-'));
  grid.appendChild(createReportField('环境', payload.environment || '-'));
  grid.appendChild(createReportField('集群', payload.cluster || '-'));
  grid.appendChild(createReportField('命名空间', payload.namespace || '-'));
  grid.appendChild(createReportField('问题描述', payload.message || '-', { wide: true }));
  card.appendChild(grid);
  return card;
}

function addTicketMessage(title, payload, options = {}) {
  const article = document.createElement('article');
  article.className = 'message user ticket-message';
  appendMessageArticle(article, title, buildTicketSummaryCard(payload, options));
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

function renderMockWorldSummary() {
  if (!mockWorldSummary) return;
  mockWorldSummary.innerHTML = '';
  if (!selectedMockWorld) {
    mockWorldSummary.classList.add('hidden');
    return;
  }
  mockWorldSummary.classList.remove('hidden');
  const title = document.createElement('strong');
  title.textContent = `当前世界：${selectedMockWorld.label}`;
  const description = document.createElement('p');
  description.textContent = selectedMockWorld.description || '这个世界会固定返回一组 mock 工具结果。';
  const meta = document.createElement('span');
  const toolNames = (selectedMockWorld.tool_names || []).slice(0, 6).join(', ');
  const difficulty = selectedMockWorld.difficulty ? `难度：${selectedMockWorld.difficulty} · ` : '';
  meta.textContent = `${difficulty}${selectedMockWorld.tool_count || 0} 个工具 mock${toolNames ? `：${toolNames}` : ''}`;
  const focus = document.createElement('span');
  const focusItems = (selectedMockWorld.evaluation_focus || []).slice(0, 4).join(' / ');
  focus.textContent = focusItems ? `评估重点：${focusItems}` : '';
  const promptHint = document.createElement('span');
  const promptTemplate = (selectedMockWorld.user_prompt_templates || [])[0];
  promptHint.textContent = promptTemplate ? `示例问题：${promptTemplate}` : '';
  mockWorldSummary.appendChild(title);
  mockWorldSummary.appendChild(description);
  mockWorldSummary.appendChild(meta);
  if (focus.textContent) mockWorldSummary.appendChild(focus);
  if (promptHint.textContent) mockWorldSummary.appendChild(promptHint);
}

function selectMockWorld(worldId, { applyDefaults = true } = {}) {
  selectedMockWorld = mockWorlds.find((world) => world.world_id === worldId) || null;
  if (mockWorldSelect && mockWorldSelect.value !== (selectedMockWorld?.world_id || '')) {
    mockWorldSelect.value = selectedMockWorld?.world_id || '';
  }
  if (selectedMockWorld && applyDefaults) {
    serviceNameInput.value = selectedMockWorld.service || serviceNameInput.value;
    if (environmentNameInput && !environmentNameInput.value) environmentNameInput.value = 'prod';
    const promptTemplate = (selectedMockWorld.user_prompt_templates || [])[0];
    if (promptTemplate && messageInput && !messageInput.value.trim()) messageInput.value = promptTemplate;
  }
  messageForm.classList.toggle('mock-world-mode', Boolean(selectedMockWorld));
  renderMockWorldSummary();
}

async function loadMockWorlds() {
  if (!mockWorldSelect) return;
  try {
    mockWorlds = await fetchJson('/api/v1/mock-worlds');
  } catch (error) {
    mockWorlds = [];
    mockWorldSummary.classList.remove('hidden');
    mockWorldSummary.textContent = `加载 Mock 世界失败：${error.message}`;
    return;
  }
  const currentValue = mockWorldSelect.value;
  mockWorldSelect.innerHTML = '';
  const defaultOption = document.createElement('option');
  defaultOption.value = '';
  defaultOption.textContent = '真实/默认工具返回';
  mockWorldSelect.appendChild(defaultOption);
  mockWorlds.forEach((world) => {
    const option = document.createElement('option');
    option.value = world.world_id;
    option.textContent = `${world.label}（${world.tool_count} tools）`;
    mockWorldSelect.appendChild(option);
  });
  selectMockWorld(currentValue, { applyDefaults: false });
}

function selectedMockWorldPayload() {
  if (!selectedMockWorld) return {};
  return {
    mock_tool_responses: selectedMockWorld.mock_tool_responses || {},
  };
}

function stableJson(value) {
  if (value == null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map((item) => stableJson(item)).join(',')}]`;
  return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(',')}}`;
}

function mockResponsesFromDetail(detail) {
  const sessionResponses = detail?.session?.incident_state?.shared_context?.mock_tool_responses;
  if (sessionResponses && Object.keys(sessionResponses).length > 0) return sessionResponses;
  const diagnosis = latestDiagnosisFromDetail(detail);
  const diagnosisResponses = diagnosis?.incident_state?.shared_context?.mock_tool_responses;
  return diagnosisResponses && Object.keys(diagnosisResponses).length > 0 ? diagnosisResponses : {};
}

function syncMockWorldFromDetail(detail) {
  const responses = mockResponsesFromDetail(detail);
  if (!responses || Object.keys(responses).length === 0) {
    selectMockWorld('', { applyDefaults: false });
    return;
  }
  const responseKey = stableJson(responses);
  const matched = mockWorlds.find((world) => stableJson(world.mock_tool_responses || {}) === responseKey);
  selectedMockWorld = matched || {
    world_id: 'custom-session-world',
    label: '自定义 Mock 世界',
    service: detail?.session?.incident_state?.service || '',
    description: '当前会话使用一组自定义 mock_tool_responses。',
    tool_count: Object.keys(responses).length,
    tool_names: Object.keys(responses).sort(),
    mock_tool_responses: responses,
  };
  if (mockWorldSelect) mockWorldSelect.value = matched?.world_id || '';
  messageForm.classList.toggle('mock-world-mode', true);
  renderMockWorldSummary();
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


function clearPanel(container) {
  container.innerHTML = '';
  container.classList.remove('empty-state');
}

function isEmptyValue(value) {
  if (value == null || value === '') return true;
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === 'object') return Object.keys(value).length === 0;
  return false;
}

function truncateText(value, maxLength = 110) {
  const text = String(value ?? '');
  if (text.length <= maxLength) return text;
  return `${text.slice(0, maxLength - 1)}…`;
}

const FIELD_LABELS = {
  action: '动作',
  action_pattern: '动作模式',
  alert_count: '告警数',
  case_id: 'Case ID',
  candidate_id: 'Candidate ID',
  cluster: '集群',
  confidence: '置信度',
  context_quality: '上下文质量',
  created_at: '创建时间',
  current_agent: '当前 Agent',
  current_stage: '当前阶段',
  dependency_status: '依赖状态',
  diagnostic_goal: '诊断目标',
  event_type: '事件类型',
  failure_mode: '故障模式',
  health_status: '健康状态',
  human_verified: '人工确认',
  namespace: '命名空间',
  p99_latency_ms: 'P99 延迟',
  purpose: '目的',
  reason: '原因',
  recall_reason: '召回原因',
  recall_score: '召回分',
  root_cause: '根因',
  root_cause_taxonomy: '根因分类',
  service: '服务',
  service_type: '服务类型',
  session_id: 'Session ID',
  severity: '严重度',
  signal_pattern: '信号模式',
  source: '来源',
  status: '状态',
  summary: '摘要',
  ticket_id: 'Ticket',
  timeout_ratio: '超时比例',
  title: '标题',
  tool_name: '工具',
  updated_at: '更新时间',
};

function humanizeKey(key) {
  const text = String(key || '');
  return FIELD_LABELS[text] || text.replace(/_/g, ' ');
}

function formatReadableScalar(value) {
  if (value == null || value === '') return '-';
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(4)));
  return String(value);
}

function formatObjectSummary(value, maxLength = 120) {
  if (value == null || value === '') return '-';
  if (Array.isArray(value)) {
    const items = value.map((item) => formatObjectSummary(item, 60)).filter(Boolean);
    return truncateText(items.join('；') || '-', maxLength);
  }
  if (typeof value !== 'object') return truncateText(formatReadableScalar(value), maxLength);
  const preferredKeys = ['summary', 'title', 'tool_name', 'action', 'status', 'reason', 'root_cause', 'service', 'failure_mode'];
  const parts = [];
  preferredKeys.forEach((key) => {
    if (!isEmptyValue(value[key])) parts.push(`${humanizeKey(key)}：${formatReadableScalar(value[key])}`);
  });
  if (parts.length === 0) {
    Object.entries(value).slice(0, 4).forEach(([key, item]) => {
      if (!isEmptyValue(item)) parts.push(`${humanizeKey(key)}：${formatReadableScalar(typeof item === 'object' ? formatObjectSummary(item, 48) : item)}`);
    });
  }
  return truncateText(parts.join('；') || '-', maxLength);
}

function createMiniKeyValue(value, { maxEntries = 8 } = {}) {
  const dl = document.createElement('dl');
  dl.className = 'mini-kv';
  Object.entries(value || {}).slice(0, maxEntries).forEach(([key, item]) => {
    if (isEmptyValue(item)) return;
    const dt = document.createElement('dt');
    dt.textContent = humanizeKey(key);
    const dd = document.createElement('dd');
    dd.textContent = typeof item === 'object' ? formatObjectSummary(item) : formatReadableScalar(item);
    dl.appendChild(dt);
    dl.appendChild(dd);
  });
  return dl;
}

function createReadableValue(value, { badge = false, tone = 'info', list = false } = {}) {
  if (badge) return createStatusPill(value, tone);
  if ((list || Array.isArray(value)) && Array.isArray(value)) {
    const listElement = document.createElement('ul');
    listElement.className = 'readable-list';
    value.forEach((item) => {
      if (isEmptyValue(item)) return;
      const li = document.createElement('li');
      li.textContent = typeof item === 'object' ? formatObjectSummary(item, 180) : formatReadableScalar(item);
      listElement.appendChild(li);
    });
    if (listElement.children.length > 0) return listElement;
  }
  if (value && typeof value === 'object') return createMiniKeyValue(value);
  const content = document.createElement('span');
  if (tone) content.className = tone === 'plain' ? '' : `readable-value ${tone}`;
  content.textContent = formatReadableScalar(value);
  return content;
}

function appendFormSection(container, title, rows, { columns = false } = {}) {
  const visibleRows = (rows || []).filter((row) => row && !isEmptyValue(row.value));
  if (visibleRows.length === 0) return;
  container.classList.remove('empty-state');
  const section = document.createElement('section');
  section.className = 'form-section';
  if (title) {
    const heading = document.createElement('h4');
    heading.textContent = title;
    section.appendChild(heading);
  }
  const grid = document.createElement('div');
  grid.className = columns ? 'form-field-grid' : 'form-field-stack';
  visibleRows.forEach((row) => {
    const field = document.createElement('div');
    field.className = `form-field${row.wide ? ' wide' : ''}`;
    const label = document.createElement('span');
    label.textContent = row.label;
    field.appendChild(label);
    field.appendChild(createReadableValue(row.value, row));
    grid.appendChild(field);
  });
  section.appendChild(grid);
  container.appendChild(section);
}

function appendCardList(container, title, items, buildRows, { emptyText = '' } = {}) {
  if (!Array.isArray(items) || items.length === 0) {
    if (emptyText) appendFormSection(container, title, [{ label: '状态', value: emptyText }]);
    return;
  }
  container.classList.remove('empty-state');
  const section = document.createElement('section');
  section.className = 'form-section';
  const heading = document.createElement('h4');
  heading.textContent = title;
  section.appendChild(heading);
  const list = document.createElement('div');
  list.className = 'readable-card-list';
  items.forEach((item, index) => {
    const card = document.createElement('article');
    card.className = 'readable-card';
    appendFormSection(card, '', buildRows(item, index), { columns: true });
    list.appendChild(card);
  });
  section.appendChild(list);
  container.appendChild(section);
}

function appendRawJsonDetails(container, title, value) {
  if (isEmptyValue(value)) return;
  container.classList.remove('empty-state');
  const details = document.createElement('details');
  details.className = 'raw-json-details';
  const summary = document.createElement('summary');
  summary.textContent = title;
  const pre = document.createElement('pre');
  pre.className = 'json-block';
  pre.textContent = toPrettyJson(value);
  details.appendChild(summary);
  details.appendChild(pre);
  container.appendChild(details);
}

function appendJsonBlock(container, title, value) {
  if (isEmptyValue(value)) return;
  container.classList.remove('empty-state');
  const block = document.createElement('div');
  block.className = 'json-card';

  const heading = document.createElement('strong');
  heading.textContent = title;
  const pre = document.createElement('pre');
  pre.className = 'json-block';
  pre.textContent = toPrettyJson(value);

  block.appendChild(heading);
  block.appendChild(pre);
  container.appendChild(block);
}

function createStatusPill(status, toneOverride = '') {
  const pill = document.createElement('span');
  pill.className = `status-pill ${toneOverride || pillToneForStatus(status)}`;
  pill.textContent = String(status || '-');
  return pill;
}

function appendTimelineItem(container, title, subtitle, body, tone = 'info') {
  container.classList.remove('empty-state');
  const item = document.createElement('article');
  item.className = `timeline-item ${tone}`;

  const header = document.createElement('div');
  header.className = 'timeline-item-header';
  const heading = document.createElement('strong');
  heading.textContent = title || '-';
  const meta = document.createElement('span');
  meta.textContent = subtitle || '';
  header.appendChild(heading);
  header.appendChild(meta);
  item.appendChild(header);

  if (body) {
    const bodyNode = document.createElement('div');
    bodyNode.className = 'timeline-body';
    bodyNode.appendChild(createReadableValue(body, { tone: 'plain' }));
    item.appendChild(bodyNode);
  }

  container.appendChild(item);
}

function renderDataRows(container, rows, columns, onSelect, selectedId, getId) {
  clearPanel(container);
  if (!Array.isArray(rows) || rows.length === 0) {
    setPanelEmpty(container, '暂无数据');
    return;
  }

  rows.forEach((row) => {
    const rowId = getId(row);
    const button = document.createElement('button');
    button.className = 'data-row';
    if (rowId && rowId === selectedId) button.classList.add('active');
    button.type = 'button';

    columns.forEach((column) => {
      const line = document.createElement('span');
      line.className = 'data-cell';
      const label = document.createElement('strong');
      label.textContent = column.label;
      line.appendChild(label);
      const value = typeof column.value === 'function' ? column.value(row) : row[column.value];
      if (column.badge) {
        line.appendChild(createStatusPill(value));
      } else {
        const content = document.createElement('em');
        content.textContent = truncateText(value || '-', column.maxLength || 90);
        line.appendChild(content);
      }
      button.appendChild(line);
    });

    button.addEventListener('click', () => onSelect(row));
    container.appendChild(button);
  });
}

function latestDiagnosisFromDetail(detail) {
  const turns = detail?.turns || [];
  for (let index = turns.length - 1; index >= 0; index -= 1) {
    const diagnosis = turns[index]?.structured_payload?.diagnosis;
    if (diagnosis && Object.keys(diagnosis).length > 0) return diagnosis;
  }
  return detail?.latest_diagnosis || null;
}

function resolveContextSnapshot(detail = currentSessionDetail) {
  const session = detail?.session || detail;
  const incidentState = session?.incident_state || {};
  const diagnosis = latestDiagnosisFromDetail(detail);
  return (
    diagnosis?.context_snapshot
    || incidentState.context_snapshot
    || incidentState.metadata?.context_snapshot
    || {}
  );
}

function buildQuery(params) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') query.set(key, String(value));
  });
  const text = query.toString();
  return text ? `?${text}` : '';
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
      if (Array.isArray(toolResult.evidence) && toolResult.evidence.length > 0) {
        toolResult.evidence.slice(0, 2).forEach((item) => lines.push(`  · ${item}`));
      } else if (toolResult.payload) {
        lines.push(`  · ${JSON.stringify(toolResult.payload).slice(0, 240)}`);
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
  if (diagnosis?.display_mode === 'user_report') {
    return message || diagnosis.user_report?.message || '';
  }
  return `${message || ''}

${formatDiagnosis(diagnosis)}`.trim();
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

function renderRecentSessions(sessions) {
  if (!recentSessions) return;
  recentSessions.innerHTML = '';
  recentSessions.classList.remove('empty-state');
  if (!Array.isArray(sessions) || sessions.length === 0) {
    setPanelEmpty(recentSessions, '暂无最近会话');
    return;
  }

  sessions.forEach((session) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'session-list-item';
    if (session.session_id === currentSessionId) button.classList.add('active');

    const main = document.createElement('div');
    main.className = 'session-list-main';
    const ticket = document.createElement('strong');
    ticket.textContent = session.ticket_id || session.session_id;
    const status = document.createElement('span');
    status.className = `status-pill ${pillToneForStatus(session.status)}`;
    status.textContent = session.status || '-';
    main.appendChild(ticket);
    main.appendChild(status);

    const meta = document.createElement('div');
    meta.className = 'session-list-meta';
    meta.textContent = `${session.current_stage || '-'} · ${formatTimestamp(session.last_active_at)}`;

    button.appendChild(main);
    button.appendChild(meta);
    button.addEventListener('click', async () => {
      if (session.session_id === currentSessionId) {
        setPageView('chat');
        return;
      }
      try {
        await loadConversation(session.session_id, { announce: true });
        setPageView('chat');
      } catch (error) {
        addMessage('system', '系统错误', error.message);
      }
    });
    recentSessions.appendChild(button);
  });
}

async function loadRecentSessions() {
  if (!recentSessions) return;
  try {
    const userId = userIdInput.value.trim();
    const sessions = await fetchJson(`/api/v1/sessions${buildQuery({ user_id: userId || undefined, limit: 8 })}`);
    renderRecentSessions(sessions);
  } catch (_error) {
    setPanelEmpty(recentSessions, '最近会话加载失败');
  }
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
    addAssistantMessage(turn.content, payload.diagnosis);
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
  currentSessionDetail = detail;
  syncMockWorldFromDetail(detail);
  clearMessages();
  detail.turns.forEach((turn) => renderTurn(turn, detail.session));
  renderSessionSummary(detail.session);
  await loadRecentSessions();

  const latestApproval = resolveLatestApprovalContext(detail.turns || []);
  const approvalContext = buildApprovalContext(
    latestApproval.approvalRequest,
    detail.pending_interrupt,
    latestApproval.diagnosis
  );
  renderInterruptPanel(detail.pending_interrupt, approvalContext);
  renderSessionInspector(detail.session);
  renderDiagnosisTimeline(detail);
  await refreshSessionWorkbench();
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


function setWorkspaceTab(tab) {
  document.querySelectorAll('[data-workspace-tab]').forEach((button) => {
    button.classList.toggle('active', button.dataset.workspaceTab === tab);
  });
  document.querySelectorAll('[data-workspace-panel]').forEach((panel) => {
    panel.classList.toggle('active', panel.dataset.workspacePanel === tab);
  });
}

function renderSessionInspector(session, events = latestSystemEvents, runtime = latestRuntimeSnapshot) {
  clearPanel(sessionMemoryPanel);
  clearPanel(agentEventsPanel);
  clearPanel(contextSnapshotPanel);

  if (!session) {
    setPanelEmpty(sessionMemoryPanel, '当前无会话记忆');
    setPanelEmpty(agentEventsPanel, '当前无事件');
    setPanelEmpty(contextSnapshotPanel, '当前无上下文快照');
    return;
  }

  const memory = session.session_memory || {};
  const workingMemory = memory.working_memory || runtime?.process_memory_summary?.working_memory || {};
  appendFormSection(sessionMemoryPanel, '会话状态', [
    { label: '状态', value: session.status, badge: true },
    { label: '阶段', value: session.current_stage || '-' },
    { label: '当前 Agent', value: session.current_agent || '-' },
    { label: 'Ticket', value: session.ticket_id || '-' },
    { label: '更新时间', value: formatTimestamp(session.last_active_at) },
  ], { columns: true });
  appendFormSection(sessionMemoryPanel, 'Working Memory', [
    { label: '原始问题', value: workingMemory.original_user_message || memory.original_user_message || '-' , wide: true },
    { label: '当前目标', value: workingMemory.current_goal || workingMemory.goal || memory.current_goal || '-' , wide: true },
    { label: '已知事实', value: workingMemory.known_facts || workingMemory.facts || [], list: true, wide: true },
    { label: '待补充信息', value: workingMemory.missing_slots || workingMemory.open_questions || [], list: true, wide: true },
    { label: '下一步', value: workingMemory.next_action || memory.next_action || '-' , wide: true },
  ]);
  const memoryRemainder = { ...memory };
  delete memoryRemainder.working_memory;
  delete memoryRemainder.original_user_message;
  delete memoryRemainder.current_goal;
  delete memoryRemainder.next_action;
  appendFormSection(sessionMemoryPanel, '补充记忆', Object.entries(memoryRemainder).map(([key, value]) => ({
    label: humanizeKey(key),
    value,
    wide: true,
  })));
  if (sessionMemoryPanel.children.length === 0) {
    setPanelEmpty(sessionMemoryPanel, '当前无会话记忆');
  }

  if (Array.isArray(events) && events.length > 0) {
    events.slice(0, 16).forEach((event) => {
      appendTimelineItem(
        agentEventsPanel,
        event.event_type,
        formatTimestamp(event.created_at),
        event.payload?.summary || event.metadata?.summary || event.payload || {},
        pillToneForStatus(event.event_type)
      );
    });
  } else {
    setPanelEmpty(agentEventsPanel, '当前无事件');
  }

  const contextSnapshot = resolveContextSnapshot(currentSessionDetail || { session });
  const incidentState = session.incident_state || {};
  const ragContext = contextSnapshot.rag_context || incidentState.rag_context || {};
  const ragHits = ragContext.hits || ragContext.context || [];
  const similarCases = contextSnapshot.similar_cases || [];
  const playbooks = contextSnapshot.diagnosis_playbooks || [];
  const retrievalExpansion = contextSnapshot.retrieval_expansion || {};
  appendFormSection(contextSnapshotPanel, '召回概览', [
    { label: 'Context Quality', value: contextSnapshot.context_quality ?? '-' },
    { label: 'RAG 命中', value: ragHits.length || 0 },
    { label: '相似案例', value: similarCases.length || 0 },
    { label: 'Playbook', value: playbooks.length || 0 },
    { label: '工具域', value: contextSnapshot.matched_tool_domains || [], list: true, wide: true },
    { label: 'Case 召回状态', value: contextSnapshot.case_recall?.reason || contextSnapshot.case_recall?.state || '-' },
    { label: 'Playbook 召回状态', value: contextSnapshot.playbook_recall?.reason || contextSnapshot.playbook_recall?.state || '-' },
  ], { columns: true });
  appendCardList(contextSnapshotPanel, '相似案例', similarCases.slice(0, 5), (item) => [
    { label: 'Case ID', value: item.case_id || '-' },
    { label: '故障模式', value: item.failure_mode || '-' },
    { label: '根因分类', value: item.root_cause_taxonomy || '-' },
    { label: '召回分', value: item.recall_score ?? '-' },
    { label: '根因', value: item.root_cause || item.summary || '-', wide: true },
  ]);
  appendCardList(contextSnapshotPanel, 'Playbook 召回', playbooks.slice(0, 5), (item) => [
    { label: 'Playbook', value: item.title || item.playbook_id || '-' },
    { label: '召回分', value: item.recall_score ?? '-' },
    { label: '原因', value: item.recall_reason || '-', wide: true },
  ]);
  appendCardList(contextSnapshotPanel, 'RAG 命中', ragHits.slice(0, 5), (item) => [
    { label: '标题', value: item.title || item.path || '-' },
    { label: '分数', value: item.score ?? '-' },
    { label: '片段', value: item.snippet || item.content || '-', wide: true },
  ]);
  appendCardList(contextSnapshotPanel, '检索扩展', retrievalExpansion.subqueries || [], (item) => [
    { label: '查询', value: item.query || item.text || '-', wide: true },
    { label: '目的', value: item.intent || item.reason || '-' },
  ]);
  appendRawJsonDetails(contextSnapshotPanel, '查看完整上下文原始数据', contextSnapshot);
  if (contextSnapshotPanel.children.length === 0) {
    setPanelEmpty(contextSnapshotPanel, '当前无上下文快照');
  }
}

function collectDiagnosisTimeline(detail, events = latestSystemEvents) {
  if (!detail?.session) return [];
  const items = [];
  const session = detail.session;
  const incidentState = session.incident_state || {};
  const contextSnapshot = resolveContextSnapshot(detail);
  const diagnosis = latestDiagnosisFromDetail(detail) || {};

  items.push({
    title: '会话状态',
    subtitle: formatTimestamp(session.last_active_at),
    body: `status=${session.status}\nstage=${session.current_stage}\nagent=${session.current_agent || '-'}`,
    tone: pillToneForStatus(session.status),
  });

  const routing = diagnosis.routing || incidentState.routing;
  if (routing && Object.keys(routing).length > 0) {
    items.push({
      title: '路由决策',
      subtitle: routing.agent_name || routing.target_agent || '',
      body: routing.reason || routing.route_source || routing,
      tone: 'info',
    });
  }

  const ragContext = contextSnapshot.rag_context || incidentState.rag_context;
  if (ragContext) {
    const ragHits = ragContext.hits || ragContext.context || [];
    items.push({
      title: 'RAG 召回',
      subtitle: `${ragHits.length || 0} hits`,
      body: {
        query: ragContext.query,
        query_type: ragContext.query_type,
        citations: ragContext.citations || [],
      },
      tone: ragHits.length ? 'success' : 'warning',
    });
  }

  if (contextSnapshot.playbook_recall || (contextSnapshot.diagnosis_playbooks || []).length > 0) {
    items.push({
      title: 'Playbook 召回',
      subtitle: `${(contextSnapshot.diagnosis_playbooks || []).length} playbooks`,
      body: {
        recall: contextSnapshot.playbook_recall || {},
        playbooks: (contextSnapshot.diagnosis_playbooks || []).map((item) => ({
          playbook_id: item.playbook_id,
          title: item.title,
          recall_score: item.recall_score,
          recall_reason: item.recall_reason,
        })),
      },
      tone: (contextSnapshot.diagnosis_playbooks || []).length ? 'success' : 'warning',
    });
  }

  if (contextSnapshot.case_recall || (contextSnapshot.similar_cases || []).length > 0) {
    items.push({
      title: '历史案例召回',
      subtitle: `${(contextSnapshot.similar_cases || []).length} cases`,
      body: {
        recall: contextSnapshot.case_recall || {},
        cases: (contextSnapshot.similar_cases || []).map((item) => ({
          case_id: item.case_id,
          failure_mode: item.failure_mode,
          root_cause_taxonomy: item.root_cause_taxonomy,
          recall_score: item.recall_score,
        })),
      },
      tone: (contextSnapshot.similar_cases || []).length ? 'success' : 'warning',
    });
  }

  const toolResults = diagnosis.tool_results || incidentState.execution_results || [];
  toolResults.forEach((toolResult) => {
    items.push({
      title: `工具调用：${toolResult.tool_name || toolResult.action || '-'}`,
      subtitle: toolResult.status || '',
      body: toolResult.summary || toolResult.result_summary || (Array.isArray(toolResult.evidence) && toolResult.evidence.length > 0 ? toolResult.evidence : (toolResult.payload || toolResult)),
      tone: pillToneForStatus(toolResult.status),
    });
  });

  (incidentState.approval_proposals || []).forEach((approval) => {
    items.push({
      title: `审批申请：${approval.action || approval.title || '-'}`,
      subtitle: approval.risk || '',
      body: approval.reason || approval,
      tone: 'warning',
    });
  });

  (incidentState.approved_actions || []).forEach((approval) => {
    items.push({
      title: `审批结果：${approval.action || '-'}`,
      subtitle: approval.status || '',
      body: approval.comment || approval,
      tone: pillToneForStatus(approval.status),
    });
  });

  if (detail.pending_interrupt) {
    items.push({
      title: `待人工节点：${detail.pending_interrupt.type}`,
      subtitle: detail.pending_interrupt.status || '',
      body: `${detail.pending_interrupt.reason || ''}\n${detail.pending_interrupt.question || ''}`.trim(),
      tone: 'warning',
    });
  }

  (events || []).slice(0, 12).forEach((event) => {
    items.push({
      title: `系统事件：${event.event_type}`,
      subtitle: formatTimestamp(event.created_at),
      body: event.payload?.summary || event.payload || {},
      tone: pillToneForStatus(event.event_type),
    });
  });

  return items;
}

function renderDiagnosisTimeline(detail = currentSessionDetail) {
  clearPanel(diagnosisTimeline);
  const items = collectDiagnosisTimeline(detail, latestSystemEvents);
  if (items.length === 0) {
    setPanelEmpty(diagnosisTimeline, '当前无诊断时间线');
    return;
  }
  items.forEach((item) => appendTimelineItem(diagnosisTimeline, item.title, item.subtitle, item.body, item.tone));
}

async function refreshSessionWorkbench() {
  if (!currentSessionId || !currentSessionDetail?.session) {
    renderSessionInspector(null);
    renderDiagnosisTimeline(null);
    return;
  }

  const [eventsResult, runtimeResult] = await Promise.allSettled([
    fetchJson(`/api/v1/sessions/${currentSessionId}/events?limit=80`),
    fetchJson(`/api/v1/sessions/${currentSessionId}/runtime`),
  ]);

  latestSystemEvents = eventsResult.status === 'fulfilled' ? eventsResult.value : [];
  latestRuntimeSnapshot = runtimeResult.status === 'fulfilled' ? runtimeResult.value : null;
  renderAgentActivityEvents(latestSystemEvents);
  renderSessionInspector(currentSessionDetail.session, latestSystemEvents, latestRuntimeSnapshot);
  renderDiagnosisTimeline(currentSessionDetail);
}

function mergeMutationIntoCurrentDetail(data) {
  const turns = [...(currentSessionDetail?.turns || [])];
  if (data.assistant_turn && !turns.some((turn) => turn.turn_id === data.assistant_turn.turn_id)) {
    turns.push(data.assistant_turn);
  }
  currentSessionDetail = {
    session: data.session,
    turns,
    pending_interrupt: data.pending_interrupt || null,
    latest_diagnosis: data.diagnosis || currentSessionDetail?.latest_diagnosis || null,
  };
}

function renderPlaybookList(playbooks) {
  renderDataRows(
    playbookList,
    playbooks,
    [
      { label: '标题', value: (item) => item.title || item.playbook_id },
      { label: '状态', value: 'status', badge: true },
      { label: '服务类型', value: (item) => item.service_type || '-' },
      { label: '来源案例', value: (item) => (item.source_case_ids || []).join(', ') || '-' },
    ],
    selectPlaybook,
    selectedPlaybook?.playbook_id,
    (item) => item.playbook_id
  );
}

function selectPlaybook(playbook) {
  selectedPlaybook = playbook;
  renderPlaybookDetail(playbook);
  loadPlaybooks({ preserveSelection: true });
}

function renderPlaybookDetail(playbook) {
  clearPanel(playbookDetail);
  if (!playbook) {
    setPanelEmpty(playbookDetail, '请选择 Playbook');
    return;
  }
  appendFormSection(playbookDetail, '基本信息', [
    { label: '标题', value: playbook.title || playbook.playbook_id, wide: true },
    { label: '状态', value: playbook.status, badge: true },
    { label: '启用态', value: playbook.human_verified && playbook.status === 'verified' ? '已启用' : '未启用', badge: true },
    { label: '服务类型', value: playbook.service_type || '-' },
    { label: '成功案例数', value: playbook.success_count ?? 0 },
    { label: '失败案例数', value: playbook.failure_count ?? 0 },
    { label: '最后评测', value: playbook.last_eval_passed == null ? '-' : (playbook.last_eval_passed ? '通过' : '未通过') },
  ], { columns: true });
  appendFormSection(playbookDetail, '适用范围', [
    { label: '故障模式', value: playbook.failure_modes || [], list: true, wide: true },
    { label: '环境', value: playbook.environments || [], list: true, wide: true },
    { label: '触发条件', value: playbook.trigger_conditions || [], list: true, wide: true },
    { label: '信号模式', value: playbook.signal_patterns || [], list: true, wide: true },
    { label: '不适用条件', value: playbook.negative_conditions || [], list: true, wide: true },
    { label: '必填实体', value: playbook.required_entities || [], list: true, wide: true },
  ]);
  appendFormSection(playbookDetail, '诊断策略', [
    { label: '诊断目标', value: playbook.diagnostic_goal || '-', wide: true },
    { label: '证据要求', value: playbook.evidence_requirements || [], list: true, wide: true },
    { label: 'Guardrails', value: playbook.guardrails || [], list: true, wide: true },
    { label: '常见误判', value: playbook.common_false_positives || [], list: true, wide: true },
  ]);
  appendCardList(playbookDetail, '诊断步骤', playbook.diagnostic_steps || [], (step, index) => [
    { label: '步骤', value: `#${index + 1}` },
    { label: '工具', value: step.tool_name || step.action || '-' },
    { label: '目的', value: step.purpose || step.reason || '-', wide: true },
    { label: '参数', value: step.params || {}, wide: true },
  ]);
  appendFormSection(playbookDetail, '审核信息', [
    { label: '来源 Case', value: playbook.source_case_ids || [], list: true, wide: true },
    { label: '审核人', value: playbook.reviewed_by || '-' },
    { label: '审核时间', value: formatTimestamp(playbook.reviewed_at) },
    { label: '审核备注', value: playbook.review_note || '-', wide: true },
  ], { columns: true });
}

async function loadPlaybooks({ preserveSelection = false } = {}) {
  try {
    const playbooks = await fetchJson(`/api/v1/playbooks${buildQuery({ status: playbookStatusFilter.value, limit: 50 })}`);
    if (!preserveSelection) selectedPlaybook = playbooks[0] || null;
    if (preserveSelection && selectedPlaybook) {
      selectedPlaybook = playbooks.find((item) => item.playbook_id === selectedPlaybook.playbook_id) || selectedPlaybook;
    }
    renderPlaybookList(playbooks);
    renderPlaybookDetail(selectedPlaybook);
  } catch (error) {
    setPanelEmpty(playbookList, `加载 Playbook 失败：${error.message}`);
  }
}

async function reviewSelectedPlaybook(humanVerified) {
  if (!selectedPlaybook) {
    alert('请先选择 Playbook');
    return;
  }
  const reviewNote = window.prompt('审核备注（可选）', '') || '';
  const updated = await fetchJson(`/api/v1/playbooks/${selectedPlaybook.playbook_id}/review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      human_verified: humanVerified,
      status: humanVerified ? 'verified' : 'rejected',
      reviewed_by: userIdInput.value.trim() || 'frontend-user',
      review_note: reviewNote,
    }),
  });
  selectedPlaybook = updated;
  await loadPlaybooks({ preserveSelection: true });
}

function renderCaseList(cases) {
  renderDataRows(
    caseList,
    cases,
    [
      { label: 'Ticket', value: (item) => item.ticket_id || item.case_id },
      { label: '状态', value: 'case_status', badge: true },
      { label: '服务', value: (item) => item.service || '-' },
      { label: '根因', value: (item) => item.root_cause || item.final_conclusion || '-' },
    ],
    selectCase,
    selectedCase?.case_id,
    (item) => item.case_id
  );
}

function selectCase(caseRecord) {
  selectedCase = caseRecord;
  renderCaseDetail(caseRecord);
  loadCases({ preserveSelection: true });
}

function renderCaseDetail(caseRecord) {
  clearPanel(caseDetail);
  if (!caseRecord) {
    setPanelEmpty(caseDetail, '请选择案例');
    return;
  }
  appendFormSection(caseDetail, '案例状态', [
    { label: 'Case ID', value: caseRecord.case_id, wide: true },
    { label: 'Ticket', value: caseRecord.ticket_id || '-' },
    { label: '状态', value: caseRecord.case_status, badge: true },
    { label: '人工确认', value: caseRecord.human_verified ? '已确认' : '未确认', badge: true },
    { label: '服务', value: caseRecord.service || '-' },
    { label: '集群', value: caseRecord.cluster || '-' },
    { label: '命名空间', value: caseRecord.namespace || '-' },
  ], { columns: true });
  appendFormSection(caseDetail, '诊断结论', [
    { label: '症状', value: caseRecord.symptom || '-', wide: true },
    { label: '故障模式', value: caseRecord.failure_mode || '-' },
    { label: '根因分类', value: caseRecord.root_cause_taxonomy || '-' },
    { label: '信号模式', value: caseRecord.signal_pattern || '-' },
    { label: '根因', value: caseRecord.root_cause || '-', wide: true },
    { label: '最终结论', value: caseRecord.final_conclusion || '-', wide: true },
    { label: '关键证据', value: caseRecord.key_evidence || [], list: true, wide: true },
  ]);
  appendFormSection(caseDetail, '动作和验证', [
    { label: '最终动作', value: caseRecord.final_action || '-' },
    { label: '是否需审批', value: caseRecord.approval_required ? '是' : '否' },
    { label: '验证结果', value: caseRecord.verification_passed == null ? '-' : (caseRecord.verification_passed ? '通过' : '未通过') },
    { label: '动作模式', value: caseRecord.action_pattern || '-' },
  ], { columns: true });
  appendFormSection(caseDetail, '人工审核', [
    { label: '人工修正根因', value: caseRecord.actual_root_cause_hypothesis || '-', wide: true },
    { label: '选中假设', value: caseRecord.selected_hypothesis_id || '-' },
    { label: '假设准确度', value: caseRecord.hypothesis_accuracy || {}, wide: true },
    { label: '排序特征', value: caseRecord.selected_ranker_features || {}, wide: true },
    { label: '审核人', value: caseRecord.reviewed_by || '-' },
    { label: '审核时间', value: formatTimestamp(caseRecord.reviewed_at) },
    { label: '审核备注', value: caseRecord.review_note || '-', wide: true },
  ]);
}

async function loadCases({ preserveSelection = false } = {}) {
  try {
    const cases = await fetchJson(`/api/v1/cases${buildQuery({ case_status: caseStatusFilter.value, limit: 50 })}`);
    if (!preserveSelection) selectedCase = cases[0] || null;
    if (preserveSelection && selectedCase) {
      selectedCase = cases.find((item) => item.case_id === selectedCase.case_id) || selectedCase;
    }
    renderCaseList(cases);
    renderCaseDetail(selectedCase);
  } catch (error) {
    setPanelEmpty(caseList, `加载案例失败：${error.message}`);
  }
}

async function reviewCase(humanVerified) {
  if (!selectedCase) {
    alert('请先选择案例');
    return;
  }
  const reviewNote = window.prompt('审核备注（可选）', '') || '';
  const result = await fetchJson(`/api/v1/cases/${selectedCase.case_id}/review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      human_verified: humanVerified,
      reviewed_by: userIdInput.value.trim() || 'frontend-user',
      review_note: reviewNote,
    }),
  });
  selectedCase = result.incident_case || result;
  if (result.playbook_candidate) selectedPlaybook = result.playbook_candidate;
  await Promise.allSettled([
    loadCases({ preserveSelection: true }),
    loadPlaybooks({ preserveSelection: true }),
  ]);
  const extraction = result.playbook_extraction || {};
  if (humanVerified) {
    if (result.playbook_candidate) {
      alert(`案例已确认，并生成 Playbook 候选：${result.playbook_candidate.title || result.playbook_candidate.playbook_id}`);
      setWorkspaceTab('playbooks');
    } else if (extraction.reason) {
      alert(`案例已确认。${extraction.reason}`);
    }
  }
}

async function extractPlaybookFromSelectedCase() {
  if (!selectedCase) {
    alert('请先选择案例');
    return;
  }
  if (!selectedCase.human_verified || selectedCase.case_status !== 'verified') {
    alert('请先确认案例进入历史案例库，再抽取 Playbook 候选。');
    return;
  }
  const result = await fetchJson(`/api/v1/cases/${selectedCase.case_id}/extract-playbook`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      allow_single_case: true,
      min_cases: 1,
      reviewed_by: userIdInput.value.trim() || 'frontend-user',
    }),
  });
  selectedCase = result.incident_case || selectedCase;
  if (result.playbook_candidate) selectedPlaybook = result.playbook_candidate;
  await Promise.allSettled([
    loadCases({ preserveSelection: true }),
    loadPlaybooks({ preserveSelection: true }),
  ]);
  alert(result.reason || (result.extracted ? '已生成 Playbook 候选' : '未生成 Playbook 候选'));
  if (result.playbook_candidate) setWorkspaceTab('playbooks');
}

function buildBadCaseEvalSkeleton(candidate) {
  return {
    id: candidate.candidate_id,
    source: candidate.source,
    reason_codes: candidate.reason_codes || [],
    severity: candidate.severity,
    setup: {
      request_payload: candidate.request_payload || {},
    },
    expected: {
      should_not_repeat_reason_codes: candidate.reason_codes || [],
      human_feedback: candidate.human_feedback || {},
    },
    context_snapshot: candidate.context_snapshot || {},
    retrieval_expansion: candidate.retrieval_expansion || {},
    conversation_turns: candidate.conversation_turns || [],
    system_events: candidate.system_events || [],
  };
}

function renderBadCaseList(candidates) {
  renderDataRows(
    badCaseList,
    candidates,
    [
      { label: 'Ticket', value: (item) => item.ticket_id || item.candidate_id },
      { label: '状态', value: 'export_status', badge: true },
      { label: '严重度', value: 'severity', badge: true },
      { label: '归因', value: (item) => (item.reason_codes || []).join(', ') || '-' },
    ],
    selectBadCase,
    selectedBadCase?.candidate_id,
    (item) => item.candidate_id
  );
}

function selectBadCase(candidate) {
  selectedBadCase = candidate;
  renderBadCaseDetail(candidate);
  loadBadCaseCandidates({ preserveSelection: true });
}

function renderBadCaseDetail(candidate) {
  clearPanel(badCaseDetail);
  if (!candidate) {
    setPanelEmpty(badCaseDetail, '请选择候选样本');
    return;
  }
  appendFormSection(badCaseDetail, '候选样本', [
    { label: 'Candidate ID', value: candidate.candidate_id, wide: true },
    { label: 'Ticket', value: candidate.ticket_id || '-' },
    { label: '来源', value: candidate.source || '-' },
    { label: '导出状态', value: candidate.export_status, badge: true },
    { label: '严重度', value: candidate.severity, badge: true },
    { label: '创建时间', value: formatTimestamp(candidate.created_at) },
    { label: '归因码', value: candidate.reason_codes || [], list: true, wide: true },
  ], { columns: true });
  appendFormSection(badCaseDetail, '导出信息', [
    { label: '导出文件', value: candidate.export_metadata?.output_path || '-' , wide: true },
    { label: '目标数据集', value: candidate.export_metadata?.target_dataset || '-' },
    { label: '合并 Case', value: candidate.export_metadata?.merged_case_id || '-' },
    { label: '导出格式', value: candidate.export_metadata?.export_format || '-' },
  ], { columns: true });
  appendFormSection(badCaseDetail, '人工反馈', [
    { label: '是否确认', value: candidate.human_feedback?.human_verified == null ? '-' : (candidate.human_feedback.human_verified ? '确认' : '否定') },
    { label: '真实根因', value: candidate.human_feedback?.actual_root_cause_hypothesis || '-' , wide: true },
    { label: '备注', value: candidate.human_feedback?.comment || '-' , wide: true },
  ]);
  appendCardList(badCaseDetail, '工具观察', candidate.observations || [], (item) => [
    { label: '工具', value: item.tool_name || item.action || '-' },
    { label: '状态', value: item.status || '-' },
    { label: '摘要', value: item.summary || item.result_summary || '-', wide: true },
    { label: '证据', value: item.evidence || [], list: true, wide: true },
  ]);
  appendFormSection(badCaseDetail, '请求与响应摘要', [
    { label: '用户问题', value: candidate.request_payload?.message || candidate.request_payload?.user_message || '-', wide: true },
    { label: '服务', value: candidate.request_payload?.service || candidate.incident_state_snapshot?.service || '-' },
    { label: '原回答', value: candidate.response_payload?.message || candidate.response_payload?.diagnosis?.summary || candidate.response_payload?.diagnosis?.conclusion || '-', wide: true },
    { label: 'Case 召回状态', value: candidate.context_snapshot?.case_recall?.reason || candidate.context_snapshot?.case_recall?.state || '-' },
    { label: 'Playbook 召回状态', value: candidate.context_snapshot?.playbook_recall?.reason || candidate.context_snapshot?.playbook_recall?.state || '-' },
  ], { columns: true });
  appendRawJsonDetails(badCaseDetail, '查看 eval skeleton 原始数据', buildBadCaseEvalSkeleton(candidate));
  appendRawJsonDetails(badCaseDetail, '查看完整候选原始数据', candidate);
}

async function loadBadCaseCandidates({ preserveSelection = false } = {}) {
  try {
    const candidates = await fetchJson(`/api/v1/bad-case-candidates${buildQuery({ export_status: badCaseStatusFilter.value, limit: 50 })}`);
    if (!preserveSelection) selectedBadCase = candidates[0] || null;
    if (preserveSelection && selectedBadCase) {
      selectedBadCase = candidates.find((item) => item.candidate_id === selectedBadCase.candidate_id) || selectedBadCase;
    }
    renderBadCaseList(candidates);
    renderBadCaseDetail(selectedBadCase);
  } catch (error) {
    setPanelEmpty(badCaseList, `加载 bad case 候选失败：${error.message}`);
  }
}

async function exportBadCaseEvalSkeleton() {
  if (!selectedBadCase) {
    alert('请先选择 bad case 候选');
    return;
  }
  const result = await fetchJson(`/api/v1/bad-case-candidates/${selectedBadCase.candidate_id}/export-eval-skeleton`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mark_exported: true }),
  });
  selectedBadCase = result.candidate || selectedBadCase;
  await loadBadCaseCandidates({ preserveSelection: true });
  alert(`已导出 eval skeleton：${result.output_path}`);
}

async function updateBadCaseExportStatus(exportStatus) {
  if (!selectedBadCase) {
    alert('请先选择 bad case 候选');
    return;
  }
  const exportMetadata = { ...(selectedBadCase.export_metadata || {}) };
  exportMetadata.updated_by = userIdInput.value.trim() || 'frontend-user';
  exportMetadata.updated_at = new Date().toISOString();
  const updated = await fetchJson(`/api/v1/bad-case-candidates/${selectedBadCase.candidate_id}/export-status`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      export_status: exportStatus,
      export_metadata: exportMetadata,
    }),
  });
  selectedBadCase = updated;
  await loadBadCaseCandidates({ preserveSelection: true });
}

async function refreshWorkspaceData() {
  await Promise.allSettled([
    refreshSessionWorkbench(),
    loadPlaybooks({ preserveSelection: true }),
    loadCases({ preserveSelection: true }),
    loadBadCaseCandidates({ preserveSelection: true }),
  ]);
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
  messageForm.classList.toggle('supplement-mode', currentMessageMode === 'supplement');
  if (sendBtn) sendBtn.textContent = currentMessageMode === 'supplement' ? '发送补充' : '开始诊断';
  composerModeHint.textContent = currentMessageMode === 'supplement'
    ? '补充模式：这条消息会作为当前诊断的增量线索，不会新建工单。'
    : '默认模式：填写工单字段后发起诊断；如果需要自动执行高风险动作，会进入审批。';
}

async function handleMutationResponse(data, { autoOpenApproval = true } = {}) {
  renderSessionSummary(data.session);
  await loadRecentSessions();
  mergeMutationIntoCurrentDetail(data);

  const approvalContext = buildApprovalContext(data.approval_request, data.pending_interrupt, data.diagnosis);
  if (data.message || data.diagnosis) {
    addAssistantMessage(data.message, data.diagnosis);
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
  renderSessionInspector(data.session);
  renderDiagnosisTimeline(currentSessionDetail);
  await refreshSessionWorkbench();
  await refreshExecutionRecovery();

  if (autoOpenApproval && approvalContext && data.pending_interrupt?.type === 'approval') {
    openApprovalModal(approvalContext, data.diagnosis);
  }
}

async function resumeClarificationAnswer(answer) {
  return fetchJson(`/api/v1/conversations/${currentSessionId}/resume`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      interrupt_id: currentPendingInterrupt.interrupt_id,
      answer_payload: { text: answer },
    }),
  });
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
  startAgentActivity('正在提交澄清并继续诊断...');
  try {
    addMessage('user', userIdInput.value.trim() || '用户', answer);
    const data = await resumeClarificationAnswer(answer);
    clarificationAnswerInput.value = '';
    await handleMutationResponse(data);
    stopAgentActivity(data.pending_interrupt ? '等待人工处理' : '诊断完成');
  } catch (error) {
    stopAgentActivity('诊断中断');
    addMessage('system', '系统错误', error.message);
  } finally {
    clarificationSubmitBtn.disabled = false;
  }
}

async function submitFeedback(event) {
  event.preventDefault();
  if (!currentSessionId || !currentPendingInterrupt) return;

  feedbackSubmitBtn.disabled = true;
  startAgentActivity('正在提交人工反馈...');
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
    stopAgentActivity(data.pending_interrupt ? '等待人工处理' : '诊断完成');
  } catch (error) {
    stopAgentActivity('诊断中断');
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
  startAgentActivity(approved ? '审批通过，正在执行动作...' : '审批拒绝，正在收尾...');

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
    stopAgentActivity(data.pending_interrupt ? '等待人工处理' : '诊断完成');
  } catch (error) {
    stopAgentActivity('诊断中断');
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
    environment: environmentNameInput?.value.trim() || null,
    ...selectedMockWorldPayload(),
  };

  const hadActiveSession = Boolean(currentSessionId);
  const shouldResumeClarification = Boolean(
    currentSessionId
    && currentPendingInterrupt?.type === 'clarification'
    && currentPendingInterrupt?.interrupt_id
  );
  const isNewConversation = !hadActiveSession && !shouldResumeClarification;
  const newTicketId = isNewConversation ? getOrCreateTicketId() : currentTicketId;
  if (isNewConversation) {
    currentSessionId = newTicketId;
    currentTicketId = newTicketId;
    persistConversationSession();
  }

  const userMessageTitle = shouldResumeClarification
    ? `${basePayload.user_id}（澄清）`
    : hadActiveSession && currentMessageMode === 'supplement'
    ? `${basePayload.user_id}（补充）`
    : basePayload.user_id;
  if (isNewConversation && !selectedMockWorld) {
    addTicketMessage(userMessageTitle, basePayload);
  } else {
    addMessage('user', selectedMockWorld ? `${userMessageTitle} · ${selectedMockWorld.label}` : userMessageTitle, message);
  }
  messageInput.value = '';
  startAgentActivity(
    shouldResumeClarification
      ? '正在提交澄清并继续诊断...'
      : hadActiveSession
      ? '正在继续诊断...'
      : '正在创建会话并开始诊断...'
  );

  try {
    const data = shouldResumeClarification
      ? await resumeClarificationAnswer(message)
      : await fetchJson(
        isNewConversation ? '/api/v1/conversations' : `/api/v1/conversations/${currentSessionId}/messages`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(
            isNewConversation
              ? { ...basePayload, ticket_id: newTicketId }
              : { message, message_mode: currentMessageMode }
          ),
        }
      );
    await handleMutationResponse(data);
    stopAgentActivity(data.pending_interrupt ? '等待人工处理' : '诊断完成');
    setComposerMode('default');
  } catch (error) {
    stopAgentActivity('诊断中断');
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

pageNav.addEventListener('click', (event) => {
  const button = event.target.closest('[data-page-view]');
  if (!button) return;
  setPageView(button.dataset.pageView);
});

document.querySelectorAll('.new-session-action').forEach((button) => {
  button.addEventListener('click', startNewConversation);
});

workspaceTabs.addEventListener('click', (event) => {
  const button = event.target.closest('[data-workspace-tab]');
  if (!button) return;
  setWorkspaceTab(button.dataset.workspaceTab);
});
refreshWorkspaceBtn.addEventListener('click', refreshWorkspaceData);
refreshPlaybooksBtn.addEventListener('click', () => loadPlaybooks());
playbookStatusFilter.addEventListener('change', () => loadPlaybooks());
playbookReviewBtn.addEventListener('click', () => reviewSelectedPlaybook(true).catch((error) => alert(error.message)));
playbookRejectBtn.addEventListener('click', () => reviewSelectedPlaybook(false).catch((error) => alert(error.message)));
refreshCasesBtn.addEventListener('click', () => loadCases());
caseStatusFilter.addEventListener('change', () => loadCases());
caseVerifyBtn.addEventListener('click', () => reviewCase(true).catch((error) => alert(error.message)));
if (caseExtractPlaybookBtn) {
  caseExtractPlaybookBtn.addEventListener('click', () => extractPlaybookFromSelectedCase().catch((error) => alert(error.message)));
}
caseRejectBtn.addEventListener('click', () => reviewCase(false).catch((error) => alert(error.message)));
refreshBadCasesBtn.addEventListener('click', () => loadBadCaseCandidates());
badCaseStatusFilter.addEventListener('change', () => loadBadCaseCandidates());
badCaseExportBtn.addEventListener('click', () => exportBadCaseEvalSkeleton().catch((error) => alert(error.message)));
badCaseIgnoreBtn.addEventListener('click', () => updateBadCaseExportStatus('ignored').catch((error) => alert(error.message)));

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

clearChatBtn.addEventListener('click', startNewConversation);

if (mockWorldSelect) {
  mockWorldSelect.addEventListener('change', () => selectMockWorld(mockWorldSelect.value));
}

document.querySelectorAll('.quick-fill').forEach((button) => {
  button.addEventListener('click', () => {
    if (button.dataset.service !== undefined) serviceNameInput.value = button.dataset.service || '';
    if (button.dataset.environment !== undefined && environmentNameInput) {
      environmentNameInput.value = button.dataset.environment || '';
    }
    if (button.dataset.cluster !== undefined) clusterNameInput.value = button.dataset.cluster || '';
    if (button.dataset.namespace !== undefined) namespaceNameInput.value = button.dataset.namespace || '';
    messageInput.value = button.dataset.message || '';
    messageInput.focus();
  });
});

async function boot() {
  renderSessionSummary(null);
  renderInterruptPanel(null, null);
  renderExecutionRecovery(null);
  renderSessionInspector(null);
  renderDiagnosisTimeline(null);
  setComposerMode('default');
  setPageView(resolveInitialPageView(), { updateHash: false });

  await loadMockWorlds();

  const restored = await restoreConversationFromStorage();
  if (!restored) {
    renderInitialGreeting();
  }
  await Promise.allSettled([loadRecentSessions(), loadPlaybooks(), loadCases(), loadBadCaseCandidates()]);
}

boot();
