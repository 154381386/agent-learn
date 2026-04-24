---
name: openai-imagegen-cli
description: |
  Generate raster images through the OpenAI Images API or an OpenAI-compatible endpoint using a repo-local CLI script with explicit output paths. Use this skill when the user asks for 生图/文生图/出图脚本、OpenAI Images API、OPENAI_API_KEY、OPENAI_BASE_URL、命令行生成图片、兼容 OpenAI 的图片接口、或希望把结果稳定保存到本地文件，而不是只在对话里预览。
---

# OpenAI Image CLI Skill

This skill is for a narrow path:

- use a local CLI script
- call the OpenAI Images API or a compatible gateway
- generate a new raster image from text
- save the output to an explicit file path

It is intentionally narrower than the built-in global `imagegen` skill.

## Use this skill when

- The user wants a reproducible local image-generation command.
- The user explicitly mentions `OPENAI_API_KEY`, `OPENAI_BASE_URL`, CLI, script, or output path control.
- The user wants to wire image generation into local automation or another script.

## Do not use this skill when

- The user wants to edit an existing image, inpaint, or composite.
- The task is better served by the built-in image tool for quick preview or ideation.
- The output should be SVG, HTML/CSS, canvas, or some other code-native asset.

## Files

- Script: `skills/openai-imagegen-cli/scripts/generate_image.py`
- Prompt guide: `skills/openai-imagegen-cli/references/prompting.md`

## Environment

Required:

- `OPENAI_API_KEY` or `LLM_API_KEY`

Optional:

- `OPENAI_BASE_URL` or `LLM_BASE_URL` for OpenAI-compatible gateways

Dependency:

- `python3 -m pip install openai`

## Default choices

Unless the user asks otherwise:

- model: `gpt-image-2`
- size: `auto`
- quality: `auto`
- format: `png`
- background: `auto`

These defaults are aligned to the current OpenAI image-generation docs.

## Workflow

1. Confirm this is a new-image generation request, not an edit request.
2. Normalize the prompt if it is vague.
3. Pick an explicit output path inside the workspace when the result is project-bound.
4. Run the script.
5. Check the saved path and the revised prompt returned by the API.

If the prompt quality is the main blocker, read `references/prompting.md` first.

## Commands

Basic:

```bash
python3 skills/openai-imagegen-cli/scripts/generate_image.py \
  --prompt "一只戴着橙色围巾的水獭，坐在书堆上看书，插画风，细节丰富，暖色调" \
  --output output/imagegen/otter.png
```

Use a compatible gateway:

```bash
OPENAI_BASE_URL="https://your-gateway.example/v1" \
python3 skills/openai-imagegen-cli/scripts/generate_image.py \
  --prompt "极简风产品海报，一只银色保温杯置于浅灰背景中央，柔和棚拍光" \
  --output output/imagegen/poster.png
```

Transparent background:

```bash
python3 skills/openai-imagegen-cli/scripts/generate_image.py \
  --prompt "Q版宇航员贴纸，干净边缘，适合贴纸裁切" \
  --background transparent \
  --format png \
  --output output/imagegen/sticker.png
```

WebP with compression:

```bash
python3 skills/openai-imagegen-cli/scripts/generate_image.py \
  --prompt "网站 hero 图，未来感数据中心，蓝青色光线，写实风" \
  --format webp \
  --output-compression 80 \
  --output output/imagegen/hero.webp
```

## Notes

- The script only covers generation, not editing.
- If the user asks for editing, switch to a different workflow instead of stretching this one.
- If the output path suffix does not match the selected format, the script will correct the suffix automatically.
