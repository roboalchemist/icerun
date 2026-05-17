"""LLM-based structured extraction via instructor."""
from __future__ import annotations

from typing import Optional

TYPE_MAP: dict[str, type] = {
    "string": str,
    "str": str,
    "number": float,
    "float": float,
    "integer": int,
    "int": int,
    "boolean": bool,
    "bool": bool,
    "array": list,
    "object": dict,
}


def _parse_schema(schema_dict: dict) -> dict:
    """Convert JSON Schema or shorthand {field: type} to {field: (type, default)} for create_model.

    Handles two formats:
    - Formal JSON Schema: {"type": "object", "properties": {"title": {"type": "string"}}}
    - Shorthand: {"title": "string", "price": "number"}
    """
    # Handle formal JSON Schema: {"type": "object", "properties": {...}}
    if "properties" in schema_dict:
        props = schema_dict["properties"]
        return {
            k: (
                Optional[TYPE_MAP.get(
                    str(v.get("type", "string") if isinstance(v, dict) else v),
                    str,
                )],
                None,
            )
            for k, v in props.items()
        }
    # Shorthand: {"title": "string", "price": "number"}
    # Support "array of strings" or similar verbose descriptions — take the first word
    return {
        k: (
            Optional[TYPE_MAP.get(
                str(v).split()[0] if isinstance(v, str) else "string",
                str,
            )],
            None,
        )
        for k, v in schema_dict.items()
    }


def run_extraction(text: str, url: str, schema_dict: dict, llm_cfg: dict) -> dict:
    """Run LLM-based structured extraction.

    Args:
        text: The scraped text/markdown content to extract from.
        url: The original URL (included in output envelope).
        schema_dict: JSON Schema or shorthand {field: type} dict.
        llm_cfg: LLM configuration dict with keys: provider, model, api_key.

    Returns:
        {"url": ..., "extracted": {...}, "model": ..., "tokens_used": N}

    Raises:
        ImportError: If instructor is not installed.
    """
    try:
        import instructor
    except ImportError:
        raise ImportError(
            "instructor not installed. Install with: uv sync --extra extract"
        )

    from pydantic import create_model

    provider = llm_cfg.get("provider", "anthropic")
    model = llm_cfg.get("model", "claude-sonnet-4-6")
    api_key = llm_cfg.get("api_key") or None

    fields = _parse_schema(schema_dict)
    DynModel = create_model("Extracted", **fields)  # type: ignore[call-overload]

    if provider == "openai":
        import openai

        client = instructor.from_openai(openai.OpenAI(api_key=api_key))
        extracted, raw = client.create_with_completion(
            response_model=DynModel,
            messages=[
                {
                    "role": "user",
                    "content": f"Extract structured data from:\n\n{text[:8000]}",
                }
            ],
            model=model,
        )
        tokens = getattr(getattr(raw, "usage", None), "total_tokens", 0)
    else:
        # Anthropic (default)
        client = instructor.from_provider(
            f"anthropic/{model}", api_key=api_key, max_tokens=4096
        )
        extracted, raw = client.create_with_completion(
            response_model=DynModel,
            messages=[
                {
                    "role": "user",
                    "content": f"Extract structured data from:\n\n{text[:8000]}",
                }
            ],
            model=model,
            max_tokens=4096,
        )
        usage = getattr(raw, "usage", None)
        tokens = (
            getattr(usage, "input_tokens", 0) + getattr(usage, "output_tokens", 0)
            if usage is not None
            else 0
        )

    return {
        "url": url,
        "extracted": extracted.model_dump(),
        "model": model,
        "tokens_used": tokens,
    }
