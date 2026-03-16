# 学习进度快照
> 更新时间：2026-03-11

## 学习者画像
- **技术背景**：有 Python/JS 基础，LLM/Agent 领域新手
- **目标**：面试准备，侧重原理和八股
- **技术栈**：Python（LangChain / LangGraph）
- **周期**：3-4 周系统学
- **教学方式**：苏格拉底式提问 + 费曼学习法（我问你答、你用自己的话复述）

## 当前进度

### ✅ Week 1: 基础概念与 LLM 基础（4/4 已完成）
- 1.1 什么是 AI Agent — Agent 定义、三要素（LLM+工具+循环）、与 chatbot 和脚本的区别
- 1.2 LLM 基础 — Token 预测、Temperature、幻觉原因与对策、上下文窗口
- 1.3 Prompt Engineering — CoT 思维链、Few-shot、System Prompt、Agent 提示词六要素
- 1.4 Function Calling — LLM 不能调工具只输出 JSON、原生 FC vs 手写 Prompt、Agent 循环伪代码

### 🔄 Week 2: Agent 核心模式（1/4 进行中）
- ✅ 2.1 ReAct 模式 — ReAct vs CoT vs Standard、文本模板→FC 演进、三种模式对比（ReAct/Plan/Reflexion）
- 🔄 2.2 Planning — 正在学习，停在第二个问题：“任务计划由谁制定，LLM 还是开发者？”
- ⬜ 2.3 Memory 机制
- ⬜ 2.4 RAG

### ⬜ Week 3: 框架与工程实践（0/4）
### ⬜ Week 4: 进阶与面试冲刺（0/4）

## 已产出文件（目标结构）
```
week1-fundamentals/
  01-what-is-agent/       notes.md + interview.md
  02-llm-basics/          notes.md + interview.md
  03-prompt-engineering/  notes.md + interview.md
  04-function-calling/    notes.md + interview.md
week2-core-patterns/
  01-react-pattern/       notes.md + interview.md
  02-planning/            notes.md + interview.md
```

## 继续学习提示
对新电脑上的 Claude 说：
“我在学习 Agent 开发，请读一下 /path/to/agent-learn/PROGRESS.md 了解我的进度，然后继续从 2.2 Planning 的第二个问题开始教我。教学方式用苏格拉底提问法 + 费曼学习法。”

