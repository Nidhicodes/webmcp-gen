"""Validate generated tools against the WebMCP ToolDescriptor schema.

Checks names are valid identifiers, inputSchema is a valid object schema with
known types, `required` references declared properties, and names are unique.
Returns a list of ValidationIssue; empty means compliant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

_VALID_JSON_TYPES = {"string", "number", "integer", "boolean", "object", "array", "null"}
_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


@dataclass
class ValidationIssue:
    severity: Literal["error", "warning"]
    tool: str
    message: str

    def __str__(self) -> str:
        icon = "" if self.severity == "error" else ""
        return f"{icon} [{self.tool}] {self.message}"


def validate_tools(analysis: dict) -> list[ValidationIssue]:
    """Validate an analysis result against the WebMCP tool schema.

    Args:
        analysis: The dict returned by analyze_with_llm / analyze_without_llm.

    Returns:
        A list of issues. Empty list == fully spec-compliant.
    """
    issues: list[ValidationIssue] = []

    if not isinstance(analysis, dict):
        return [ValidationIssue("error", "<root>", "analysis is not an object")]

    tools = analysis.get("tools")
    if tools is None:
        return [ValidationIssue("error", "<root>", "missing 'tools' array")]
    if not isinstance(tools, list):
        return [ValidationIssue("error", "<root>", "'tools' is not an array")]

    seen_names: set[str] = set()

    for i, tool in enumerate(tools):
        label = tool.get("name", f"tool[{i}]") if isinstance(tool, dict) else f"tool[{i}]"

        if not isinstance(tool, dict):
            issues.append(ValidationIssue("error", label, "tool is not an object"))
            continue

        # name
        name = tool.get("name")
        if not name or not isinstance(name, str):
            issues.append(ValidationIssue("error", label, "missing or non-string 'name'"))
        else:
            if not _NAME_RE.match(name):
                issues.append(ValidationIssue(
                    "error", name,
                    f"name '{name}' is not a valid identifier (no spaces/special chars)"
                ))
            if name in seen_names:
                issues.append(ValidationIssue("error", name, f"duplicate tool name '{name}'"))
            seen_names.add(name)

        # description
        desc = tool.get("description")
        if desc is None:
            issues.append(ValidationIssue("warning", label, "missing 'description'"))
        elif not isinstance(desc, str):
            issues.append(ValidationIssue("error", label, "'description' must be a string"))
        elif len(desc) < 3:
            issues.append(ValidationIssue("warning", label, "description is very short"))

        # inputSchema / parameters (we use 'parameters' internally; MCP uses 'inputSchema')
        schema = tool.get("inputSchema") or tool.get("parameters")
        if schema is None:
            issues.append(ValidationIssue(
                "warning", label,
                "no inputSchema/parameters (defaults to empty object schema)"
            ))
            continue

        issues.extend(_validate_schema(label, schema))

    return issues


def _validate_schema(tool_label: str, schema: dict) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    if not isinstance(schema, dict):
        return [ValidationIssue("error", tool_label, "inputSchema is not an object")]

    if schema.get("type") != "object":
        issues.append(ValidationIssue(
            "error", tool_label, "inputSchema.type must be 'object'"
        ))

    props = schema.get("properties", {})
    if not isinstance(props, dict):
        issues.append(ValidationIssue("error", tool_label, "properties must be an object"))
        return issues

    for pname, pdef in props.items():
        if not isinstance(pdef, dict):
            issues.append(ValidationIssue(
                "error", tool_label, f"property '{pname}' is not an object"
            ))
            continue
        ptype = pdef.get("type")
        if ptype is None:
            issues.append(ValidationIssue(
                "warning", tool_label, f"property '{pname}' has no type"
            ))
        elif ptype not in _VALID_JSON_TYPES:
            issues.append(ValidationIssue(
                "error", tool_label,
                f"property '{pname}' has invalid type '{ptype}'"
            ))
        # enum must be a list if present
        if "enum" in pdef and not isinstance(pdef["enum"], list):
            issues.append(ValidationIssue(
                "error", tool_label, f"property '{pname}' enum must be an array"
            ))

    # required must reference declared properties
    required = schema.get("required", [])
    if required:
        if not isinstance(required, list):
            issues.append(ValidationIssue(
                "error", tool_label, "'required' must be an array"
            ))
        else:
            for req in required:
                if req not in props:
                    issues.append(ValidationIssue(
                        "error", tool_label,
                        f"required field '{req}' is not a declared property"
                    ))

    return issues


def is_compliant(analysis: dict) -> bool:
    """True if the analysis has no error-level issues (warnings allowed)."""
    return not any(i.severity == "error" for i in validate_tools(analysis))


def format_report(issues: list[ValidationIssue]) -> str:
    """Human-readable validation report."""
    if not issues:
        return " Fully WebMCP spec-compliant (no issues)"
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    lines = []
    if errors:
        lines.append(f"{len(errors)} error(s):")
        lines.extend(f"{i}" for i in errors)
    if warnings:
        lines.append(f"{len(warnings)} warning(s):")
        lines.extend(f"{i}" for i in warnings)
    return "\n".join(lines)
