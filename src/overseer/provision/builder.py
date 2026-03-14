"""Cloud-init template renderer for VPS provisioning."""

from __future__ import annotations

import string
from pathlib import Path

import yaml

from overseer.types import Err, Ok, Result


def render_cloud_init(
    variables: dict[str, str],
    template_path: str = "cloud-init/hermes-vps.yaml",
) -> Result[str]:
    """Read cloud-init template and substitute $variable placeholders.

    Uses Python string.Template safe_substitute so missing variables produce
    Err rather than raising KeyError.

    Args:
        variables: Mapping of template variable names to values.
        template_path: Path to the cloud-init YAML template (relative to cwd
            or absolute).

    Returns:
        Ok(rendered_yaml_string) or Err(description).
    """
    path = Path(template_path)
    if not path.exists():
        return Err(f"Template not found: {template_path}", source="provision")

    try:
        raw = path.read_text()
    except OSError as exc:
        return Err(f"Failed to read template {template_path}: {exc}", source="provision")

    try:
        template = string.Template(raw)
        # substitute raises KeyError on missing placeholders; safe_substitute
        # leaves them as-is, so we use substitute to catch missing vars early.
        rendered = template.substitute(variables)
    except KeyError as exc:
        return Err(f"Missing template variable: {exc}", source="provision")
    except ValueError as exc:
        return Err(f"Template substitution error: {exc}", source="provision")

    return Ok(rendered)


def validate_cloud_init(rendered: str) -> Result[str]:
    """Validate a rendered cloud-init document.

    Checks:
    - Valid YAML
    - Top-level mapping with required keys: users, packages, runcmd

    Returns Ok(rendered) or Err(validation error message).
    """
    try:
        doc = yaml.safe_load(rendered)
    except yaml.YAMLError as exc:
        return Err(f"Invalid YAML in rendered cloud-init: {exc}", source="provision")

    if not isinstance(doc, dict):
        return Err("Cloud-init document must be a YAML mapping", source="provision")

    required_keys = {"users", "packages", "runcmd"}
    missing = required_keys - doc.keys()
    if missing:
        return Err(
            f"Cloud-init missing required top-level keys: {sorted(missing)}",
            source="provision",
        )

    return Ok(rendered)
