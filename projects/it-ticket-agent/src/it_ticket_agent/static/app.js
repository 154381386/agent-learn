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
const clearChatBtn = document.getElementById('clearChatBtn');
const modalBackdrop = document.querySelector('.modal-backdrop');

let pendingApproval = null;

function genTicketId() {
  return `INC-${Date.now()}-${Math.floor(Math.random() * 1000)}`;
}

function addMessage(role, title, body) {
  const article = document.createElement('article');
  article.className = `message ${role}`;

  const meta = document.createElement('span');
  meta.className = 'message-meta';
  meta.textContent = title;

  const pre = document.createElement('pre');
  pre.textContent = body;

  article.appendChild(meta);
  article.appendChild(pre);
  chatMessages.appendChild(article);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function formatDiagnosis(diagnosis) {
  if (!diagnosis) return '';
  const lines = [];
  if (diagnosis.conclusion) lines.push(`结论：${diagnosis.conclusion}`);
  if (typeof diagnosis.confidence === 'number') lines.push(`置信度：${diagnosis.confidence}`);
  if (Array.isArray(diagnosis.sources) && diagnosis.sources.length > 0) {
    lines.push('证据来源：');
    diagnosis.sources.forEach((source) => {
      lines.push(`- ${source.agent}: ${source.conclusion}`);
      if (Array.isArray(source.evidence)) {
        source.evidence.forEach((evidence) => lines.push(`  · ${evidence}`));
      }
    });
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

function openApprovalModal(approvalRequest, diagnosis) {
  pendingApproval = approvalRequest;
  approvalContent.innerHTML = '';
  appendApprovalRow('Ticket', approvalRequest.ticket_id);
  appendApprovalRow('动作', approvalRequest.action);
  appendApprovalRow('风险', approvalRequest.risk, true);
  appendApprovalRow('原因', approvalRequest.reason);
  appendApprovalRow('参数', JSON.stringify(approvalRequest.params || {}, null, 2));
  if (diagnosis && diagnosis.conclusion) {
    appendApprovalRow('诊断', diagnosis.conclusion);
  }
  approvalModal.classList.remove('hidden');
  approvalModal.setAttribute('aria-hidden', 'false');
}

function closeApprovalModal() {
  approvalModal.classList.add('hidden');
  approvalModal.setAttribute('aria-hidden', 'true');
}

async function submitDecision(approved) {
  if (!pendingApproval) return;

  const approverId = approverIdInput.value.trim();
  if (!approverId) {
    alert('请填写审批人');
    return;
  }

  approveBtn.disabled = true;
  rejectBtn.disabled = true;

  try {
    const response = await fetch(`/api/v1/approvals/${pendingApproval.approval_id}/decision`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        approved,
        approver_id: approverId,
        comment: approvalCommentInput.value.trim() || null,
      }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || '审批失败');
    }

    addMessage(
      'approval',
      '审批结果',
      `${approved ? '已批准' : '已拒绝'}：${pendingApproval.action}\n审批人：${approverId}`
    );
    addMessage('agent', '罗伯特🤖', `${data.message}\n\n${formatDiagnosis(data.diagnosis)}`.trim());
    pendingApproval = null;
    approvalCommentInput.value = '';
    closeApprovalModal();
  } catch (error) {
    alert(error.message);
  } finally {
    approveBtn.disabled = false;
    rejectBtn.disabled = false;
  }
}

messageForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const message = messageInput.value.trim();
  if (!message) return;

  const payload = {
    ticket_id: genTicketId(),
    user_id: userIdInput.value.trim() || 'zhangsan',
    message,
    service: serviceNameInput.value.trim() || null,
    cluster: clusterNameInput.value.trim() || 'prod-shanghai-1',
    namespace: namespaceNameInput.value.trim() || 'default',
  };

  addMessage('user', payload.user_id, message);
  messageInput.value = '';

  try {
    const response = await fetch('/api/v1/tickets', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || '请求失败');
    }

    addMessage('agent', '罗伯特🤖', `${data.message}\n\n${formatDiagnosis(data.diagnosis)}`.trim());
    if (data.status === 'awaiting_approval' && data.approval_request) {
      addMessage('approval', '系统提示', '检测到高风险动作，需要人工审批。');
      openApprovalModal(data.approval_request, data.diagnosis);
    }
  } catch (error) {
    addMessage('agent', '系统错误', error.message);
  }
});

approveBtn.addEventListener('click', () => submitDecision(true));
rejectBtn.addEventListener('click', () => submitDecision(false));
closeModalBtn.addEventListener('click', closeApprovalModal);
modalBackdrop.addEventListener('click', closeApprovalModal);
clearChatBtn.addEventListener('click', () => {
  chatMessages.innerHTML = '';
  addMessage('agent', '罗伯特🤖', '你好，我是 IT 工单机器人。请输入你的问题；如果涉及高风险操作，我会弹出审批卡片。');
});

document.querySelectorAll('.quick-fill').forEach((button) => {
  button.addEventListener('click', () => {
    messageInput.value = button.dataset.message || '';
    messageInput.focus();
  });
});

addMessage('agent', '罗伯特🤖', '你好，我是 IT 工单机器人。请输入你的问题；如果涉及高风险操作，我会弹出审批卡片。');
