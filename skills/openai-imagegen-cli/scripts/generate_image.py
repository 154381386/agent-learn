#!/usr/bin/env python3
import argparse
import base64
import os
from pathlib import Path


SUPPORTED_SIZES = ["auto", "1024x1024", "1536x1024", "1024x1536"]
SUPPORTED_QUALITIES = ["auto", "low", "medium", "high"]
SUPPORTED_FORMATS = ["png", "jpeg", "webp"]
SUPPORTED_BACKGROUNDS = ["auto", "opaque", "transparent"]
SUPPORTED_MODERATION = ["auto", "low"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a new image with the OpenAI Images API."
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key. Defaults to OPENAI_API_KEY.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Base URL. Defaults to OPENAI_BASE_URL.",
    )
    parser.add_argument(
        "--prompt",
        default=None,
        help="Prompt text for image generation.",
    )
    parser.add_argument(
        "--prompt-file",
        default=None,
        help="Read prompt text from a UTF-8 file.",
    )
    parser.add_argument(
        "--output",
        default="output/imagegen/generated.png",
        help="Path to save the generated image.",
    )
    parser.add_argument(
        "--model",
        default="gpt-image-2",
        help="Image model name. Default: gpt-image-2",
    )
    parser.add_argument(
        "--size",
        default="auto",
        choices=SUPPORTED_SIZES,
        help="Image size.",
    )
    parser.add_argument(
        "--quality",
        default="auto",
        choices=SUPPORTED_QUALITIES,
        help="Render quality.",
    )
    parser.add_argument(
        "--format",
        default="png",
        choices=SUPPORTED_FORMATS,
        help="Output image format.",
    )
    parser.add_argument(
        "--background",
        default="auto",
        choices=SUPPORTED_BACKGROUNDS,
        help="Background mode.",
    )
    parser.add_argument(
        "--moderation",
        default="auto",
        choices=SUPPORTED_MODERATION,
        help="Moderation strictness.",
    )
    parser.add_argument(
        "--output-compression",
        type=int,
        default=None,
        help="Compression 0-100 for jpeg/webp outputs.",
    )
    return parser.parse_args()


def resolve_prompt(args: argparse.Namespace) -> str:
    if args.prompt and args.prompt_file:
        raise SystemExit("Use either --prompt or --prompt-file, not both.")
    if args.prompt_file:
        return Path(args.prompt_file).read_text(encoding="utf-8").strip()
    if args.prompt:
        return args.prompt.strip()
    raise SystemExit("Missing prompt. Pass --prompt or --prompt-file.")


def validate_args(args: argparse.Namespace) -> None:
    if args.output_compression is not None and not 0 <= args.output_compression <= 100:
        raise SystemExit("--output-compression must be between 0 and 100.")
    if args.background == "transparent" and args.format == "jpeg":
        raise SystemExit("transparent background is not supported with jpeg output.")


def normalize_output_path(output: str, output_format: str) -> Path:
    path = Path(output)
    if path.suffix.lower() != f".{output_format}":
        path = path.with_suffix(f".{output_format}")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def main() -> None:
    args = parse_args()
    prompt = resolve_prompt(args)
    validate_args(args)

    try:
        from openai import OpenAI
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Missing dependency: openai. Install it with `python3 -m pip install openai` "
            "or your preferred environment manager."
        ) from exc

    api_key = args.api_key or os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")
    base_url = args.base_url or os.getenv("OPENAI_BASE_URL") or os.getenv("LLM_BASE_URL")
    if not api_key:
        raise SystemExit(
            "Missing API key. Pass --api-key or set OPENAI_API_KEY / LLM_API_KEY."
        )

    output_path = normalize_output_path(args.output, args.format)

    client_kwargs = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = OpenAI(**client_kwargs)

    request = {
        "model": args.model,
        "prompt": prompt,
        "size": args.size,
        "quality": args.quality,
        "output_format": args.format,
        "background": args.background,
        "moderation": args.moderation,
    }
    if args.output_compression is not None and args.format in {"jpeg", "webp"}:
        request["output_compression"] = args.output_compression

    result = client.images.generate(**request)
    if not result.data:
        raise SystemExit("No image returned from API.")

    image_base64 = result.data[0].b64_json
    if not image_base64:
        raise SystemExit("Response does not contain b64_json image data.")

    image_bytes = base64.b64decode(image_base64)
    output_path.write_bytes(image_bytes)

    print(f"Saved image to: {output_path.resolve()}")
    revised_prompt = getattr(result.data[0], "revised_prompt", None)
    if revised_prompt:
        print("Revised prompt:")
        print(revised_prompt)


if __name__ == "__main__":
    main()
