# agent-learn

## Interaction Defaults

- Default to Chinese responses in this repository.
- Keep responses concise, direct, and information-first.
- When the user asks to send a local file back to them, prefer direct delivery via `cc-connect send --file` or `cc-connect send --image`.
- Do not spend time converting formats first unless the user explicitly asks for a different format.

## Image Generation Default

- In this repository, when the user asks for image generation, prefer the repo-local `openai-imagegen-cli` workflow over the built-in global `imagegen` skill.
- Treat `skills/openai-imagegen-cli/SKILL.md` as the primary instruction source for text-to-image generation in this repo.
- Use `python3 skills/openai-imagegen-cli/scripts/generate_image.py` as the default execution path unless the user explicitly asks to use the built-in image tool instead.
- Default model is `gpt-image-2` unless the user asks for another model.
- Save project-bound outputs to an explicit path inside the workspace, not only to a transient preview location.

## Exceptions

- If the user explicitly asks to use the built-in `imagegen` skill, follow that request.
- If the task is image editing, inpainting, compositing, or thread-local preview-only iteration, the built-in image tool can still be used when it is the better fit.

## Documentation Priority

- When judging the current architecture or project status, prefer the latest source-of-truth docs in this repo over older knowledge-base material.
- Primary references for `projects/it-ticket-agent` are `projects/it-ticket-agent/docs/最新架构.md`, `projects/it-ticket-agent/README.md`, and `projects/it-ticket-agent/docs/Tool-First-ReAct优化记录.md`.
- Treat older knowledge-base notes as secondary references and verify them against the latest repo docs before relying on them.
- When architecture, evaluation, or the main workflow changes, update the relevant repo docs in the same round of work so stale documentation does not leak into later conversations.

## Optimization Log Rules

- After each meaningful optimization, append to the fixed optimization log instead of overwriting prior analysis.
- The default optimization log for `projects/it-ticket-agent` is `projects/it-ticket-agent/docs/Tool-First-ReAct优化记录.md`.
- Each appended record should include: problem, cause, change, impact, and next direction.

## Obsidian Sync

- After repo changes that affect architecture, evaluation, workflow, or knowledge documentation, also add or update the corresponding notes in the Obsidian vault under `/Users/lyb/Library/Mobile Documents/iCloud~md~obsidian/Documents/do c s`.
- Prefer updating the corresponding existing Obsidian note instead of creating a duplicate note for the same topic.
- Keep the Obsidian notes aligned with the latest repo design, and avoid leaving repo docs and Obsidian docs out of sync after the same change.
- For `IT Ticket Agent` interview material, prefer updating the existing deep-QA note in Q&A form instead of only appending changelog-style sync notes.
- If a change is a real optimization with reusable engineering reasoning, also summarize it into interview-style Q&A and sync it into the Obsidian deep-QA notes instead of leaving it only in repo docs or optimization logs.
- When adding new interview-facing design content, prefer the pattern: what it was before, what it is now, why it evolved, and what the current design boundary is.
- The primary deep-QA note is `/Users/lyb/Library/Mobile Documents/iCloud~md~obsidian/Documents/do c s/面试/Agent面试/项目实践/IT Ticket Agent项目深度问答.md`.
- Do not hand-maintain the deep-QA directory note. After updating the deep-QA note, refresh navigation and backlinks with `python3 scripts/generate_it_ticket_qa_nav.py`.
- The generated directory note is `/Users/lyb/Library/Mobile Documents/iCloud~md~obsidian/Documents/do c s/面试/Agent面试/项目实践/IT Ticket Agent项目深度问答目录.md`.
