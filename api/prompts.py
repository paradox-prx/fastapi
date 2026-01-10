from typing import Dict, List


def render_documents_list(items: List[Dict[str, str]]) -> str:
    if not items:
        return ""
    lines = []
    for item in items:
        title = item.get("display_title") or item.get("title") or ""
        caption = item.get("display_caption") or ""
        if caption:
            lines.append(f"- {title}: {caption}")
        else:
            lines.append(f"- {title}")
    return "\n".join(lines)


def render_prompt(template: str, context: Dict[str, str]) -> str:
    out = template
    for key, value in context.items():
        out = out.replace(f"{{{{{key}}}}}", value or "")
    return out
