#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path


DOCS_DIR = "do\u2006c\u2006s"
DEFAULT_BASE_DIR = (
    Path.home()
    / "Library"
    / "Mobile Documents"
    / "iCloud~md~obsidian"
    / "Documents"
    / DOCS_DIR
    / "面试"
    / "Agent面试"
    / "项目实践"
)
DEFAULT_QA_FILE = DEFAULT_BASE_DIR / "IT Ticket Agent项目深度问答.md"
DEFAULT_INDEX_FILE = DEFAULT_BASE_DIR / "IT Ticket Agent项目深度问答目录.md"

QA_NOTE_LINK = "面试/Agent面试/项目实践/IT Ticket Agent项目深度问答"
INDEX_NOTE_LINK = "面试/Agent面试/项目实践/IT Ticket Agent项目深度问答目录"
OVERVIEW_NOTE_LINK = "面试/Agent面试/项目实践/IT Ticket Agent项目总览"

RETURN_LINK_RE = re.compile(
    r"^\[\[面试/Agent面试/项目实践/IT Ticket Agent项目深度问答目录#.+\|返回目录\]\]$"
)


@dataclass
class Section:
    title: str
    questions: list[str] = field(default_factory=list)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the IT Ticket Agent interview QA directory and backlinks."
    )
    parser.add_argument(
        "--qa-file",
        type=Path,
        default=DEFAULT_QA_FILE,
        help=f"Path to the main QA note. Default: {DEFAULT_QA_FILE}",
    )
    parser.add_argument(
        "--index-file",
        type=Path,
        default=DEFAULT_INDEX_FILE,
        help=f"Path to the generated index note. Default: {DEFAULT_INDEX_FILE}",
    )
    return parser.parse_args()


def parse_sections(text: str) -> list[Section]:
    sections: list[Section] = []
    current: Section | None = None

    for raw_line in text.splitlines():
        if raw_line.startswith("## "):
            title = raw_line[3:].strip()
            current = Section(title=title)
            sections.append(current)
            continue
        if raw_line.startswith("### ") and current is not None:
            current.questions.append(raw_line[4:].strip())

    return [section for section in sections if section.title != "相关笔记"]


def build_index_content(sections: list[Section]) -> str:
    lines = [
        "---",
        "title: IT Ticket Agent项目深度问答目录",
        "tags:",
        "  - interview/agent",
        "  - interview/project",
        "  - interview/index",
        "  - obsidian",
        "aliases:",
        "  - IT Ticket Agent 深度问答目录",
        "---",
        "",
        "# IT Ticket Agent项目深度问答目录",
        "",
        f"> [!info] 原文：[[{QA_NOTE_LINK}|IT Ticket Agent项目深度问答]]",
        "",
        "> [!summary]",
        "> 这页由 `scripts/generate_it_ticket_qa_nav.py` 自动生成。",
        "> 不要手工维护题目列表；正文标题变更后重新运行脚本即可。",
        "> 可以从这里按题跳到正文；正文每题下也都加了“返回目录”。",
        "",
        "## 快速导航",
        "",
        f"- [[{OVERVIEW_NOTE_LINK}|返回项目总览]]",
        f"- [[{QA_NOTE_LINK}|打开深度问答全文]]",
        "",
    ]

    for section in sections:
        lines.append(f"## {section.title}")
        lines.append("")
        if section.questions:
            for question in section.questions:
                lines.append(f"- [[{QA_NOTE_LINK}#{question}|{question}]]")
        else:
            lines.append(f"- [[{QA_NOTE_LINK}#{section.title}|{section.title}]]")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def consume_spacing_and_backlink(lines: list[str], start: int) -> int:
    index = start
    while index < len(lines) and lines[index].strip() == "":
        index += 1

    if index < len(lines) and RETURN_LINK_RE.match(lines[index].strip()):
        index += 1
        while index < len(lines) and lines[index].strip() == "":
            index += 1
        return index

    return index if index > start else start


def build_backlink(section_title: str) -> str:
    return f"[[{INDEX_NOTE_LINK}#{section_title}|返回目录]]"


def rewrite_qa_content(text: str, sections: list[Section]) -> str:
    question_to_section = {
        question: section.title for section in sections for question in section.questions
    }
    leaf_sections = {section.title for section in sections if not section.questions}

    lines = text.splitlines()
    output: list[str] = []
    current_section: str | None = None
    index = 0

    while index < len(lines):
        line = lines[index]

        if line.startswith("## "):
            current_section = line[3:].strip()
            output.append(line)
            index += 1

            if current_section in leaf_sections:
                index = consume_spacing_and_backlink(lines, index)
                output.append("")
                output.append(build_backlink(current_section))
                output.append("")
            continue

        if line.startswith("### "):
            question = line[4:].strip()
            output.append(line)
            index += 1
            index = consume_spacing_and_backlink(lines, index)
            output.append("")
            output.append(build_backlink(question_to_section[question]))
            output.append("")
            continue

        output.append(line)
        index += 1

    return "\n".join(output).rstrip() + "\n"


def main() -> None:
    args = parse_args()

    qa_file = args.qa_file.expanduser().resolve()
    index_file = args.index_file.expanduser().resolve()

    if not qa_file.exists():
        raise SystemExit(f"QA file not found: {qa_file}")

    qa_text = qa_file.read_text(encoding="utf-8")
    sections = parse_sections(qa_text)

    if not sections:
        raise SystemExit("No sections found in the QA note.")

    qa_file.write_text(rewrite_qa_content(qa_text, sections), encoding="utf-8")
    index_file.write_text(build_index_content(sections), encoding="utf-8")

    question_count = sum(len(section.questions) for section in sections)
    leaf_count = sum(1 for section in sections if not section.questions)
    print(
        f"Updated {qa_file.name} and {index_file.name}: "
        f"{question_count} questions, {leaf_count} leaf sections."
    )


if __name__ == "__main__":
    main()
