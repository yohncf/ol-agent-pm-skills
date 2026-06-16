"""Outlook Agent model → routing path diagnostic helper.

Reads `scripts/agent_models_reference.json` (the canonical catalog of
resolved model names + routing rules) and exposes:

    friendly_tag(model)          -> str
    diagnose_path(resolved)      -> (slug, display_name, rule_id, confidence)
    format_models_label(resolved)-> "<PathSlug>: <model>, <model>, ..."

Used by:
    - scripts/dash_ocv_extract.py   (per-ticket path column)
    - scripts/ado_sync.py           (ADO body link suffix)
    - scripts/_build_weekly_artifacts.py (manifest aggregates)
    - scripts/publish_ocv_report.py (HTML report)

The reference JSON ships with the repo so the rules are versioned with
the code that consumes them.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

REFERENCE_PATH = Path(__file__).resolve().parent.parent / "agent_models_reference.json"

# Pretty short tag shown after each Dash link in ADO/HTML output.
PATH_SLUG_MAP: Dict[str, str] = {
    "codegen_claude": "CodeGen-Claude",
    "codegen_ghcp": "CodeGen-GHCP",
    "codegen_ghcp_router": "CodeGen-GHCP-mini",
    "sydney_tools": "Sydney-Tools",
    "sydney_tools_with_workberry": "Sydney-Tools+WorkBerry",
    "unknown": "Unknown",
}

# Roles that are NOT path-defining; used by the "only" rule to ignore
# classifier/router noise when checking for a missing top-level model.
NON_PATH_ROLES = {"classifier_router_utility", "router_classifier"}


@lru_cache(maxsize=1)
def _load_reference() -> Tuple[Dict[str, dict], List[dict], dict]:
    """Load and index the reference JSON. Returns (by_name, rules, raw)."""
    raw = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    by_name: Dict[str, dict] = {}
    for entry in raw.get("model_catalog", []):
        canonical = entry.get("resolved_model_name")
        if canonical:
            by_name[canonical] = entry
        for alias in entry.get("aliases", []) or []:
            by_name.setdefault(alias, entry)
    return by_name, list(raw.get("path_diagnosis_rules", [])), raw


def reload_reference() -> None:
    """Force a cache reset (used by tests / hot-reload scenarios)."""
    _load_reference.cache_clear()  # type: ignore[attr-defined]


def get_catalog_entry(model: str) -> dict:
    by_name, _, _ = _load_reference()
    return by_name.get(model, {})


def friendly_tag(model: str) -> str:
    entry = get_catalog_entry(model)
    return entry.get("friendly_tag") or model


def _names_set(models: Iterable[str]) -> set[str]:
    return {m for m in models if m}


def diagnose_path(resolved_models: Iterable[str]) -> Tuple[str, str, str, str]:
    """Run the rule engine. Returns (slug, display_name, rule_id, confidence).

    Falls back to ("unknown", "Unknown", "", "low") if nothing matches or
    the input is empty.
    """
    models = _names_set(resolved_models)
    if not models:
        return ("unknown", "Unknown", "", "low")

    by_name, rules, _ = _load_reference()

    # For the "only" rule: strip non-path-defining utility models so a
    # presence-of-classifier doesn't disqualify the "only" check.
    non_path_models = {
        m for m in models
        if (by_name.get(m, {}).get("role") in NON_PATH_ROLES)
    }
    path_defining_models = models - non_path_models

    for rule in rules:
        any_set = set(rule.get("if_resolved_models_contain_any_of", []) or [])
        all_set = set(rule.get("if_resolved_models_contain_all_of", []) or [])
        and_any = set(rule.get("and_contain_any_of", []) or [])
        and_not = set(rule.get("and_do_not_contain", []) or [])
        only_set = set(rule.get("if_resolved_models_contain_only", []) or [])

        # 1. Pure "only" rule (e.g. classifier-only -> unknown)
        if only_set and not (any_set or all_set):
            # All path-defining models must be in only_set, and the set
            # of models the rule lists must actually be present.
            if models and models.issubset(only_set):
                return (
                    rule.get("branch", "unknown"),
                    rule.get("then_path", "Unknown"),
                    rule.get("id", ""),
                    rule.get("confidence", "low"),
                )
            continue

        # 2. all_of + and_any_of (e.g. SonicBerry present AND 5.3 chat present)
        if all_set:
            if not all_set.issubset(models):
                continue
            if and_any and not (and_any & models):
                continue
            if and_not & models:
                continue
            return (
                rule.get("branch", "unknown"),
                rule.get("then_path", "Unknown"),
                rule.get("id", ""),
                rule.get("confidence", "low"),
            )

        # 3. any_of (with optional and_do_not_contain)
        if any_set:
            if not (any_set & models):
                continue
            if and_not & models:
                continue
            return (
                rule.get("branch", "unknown"),
                rule.get("then_path", "Unknown"),
                rule.get("id", ""),
                rule.get("confidence", "low"),
            )

    return ("unknown", "Unknown", "", "low")


def path_slug(branch: str) -> str:
    return PATH_SLUG_MAP.get(branch, branch or "Unknown")


def format_models_label(resolved_models: Iterable[str]) -> str:
    """Render the "[<PathSlug>: <model>, <model>, ...]" payload (without
    the surrounding brackets). Returns "" if no models."""
    models = [m for m in resolved_models if m]
    if not models:
        return ""
    branch, _, _, _ = diagnose_path(models)
    return f"{path_slug(branch)}: {', '.join(models)}"
