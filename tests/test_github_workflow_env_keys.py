from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_DIR = ROOT / ".github" / "workflows"
NETWORK_SMOKE = WORKFLOW_DIR / "network-smoke.yml"


def _iter_env_mappings(node: object, trail: tuple[str, ...] = ()):
    if isinstance(node, dict):
        for key, value in node.items():
            next_trail = (*trail, str(key))
            if str(key) == "env" and isinstance(value, dict):
                yield next_trail, value
            yield from _iter_env_mappings(value, next_trail)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _iter_env_mappings(value, (*trail, str(index)))


def test_workflow_env_keys_are_case_insensitively_unique() -> None:
    collisions: list[str] = []
    for path in sorted(WORKFLOW_DIR.glob("*.y*ml")):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for trail, env in _iter_env_mappings(workflow):
            seen: dict[str, str] = {}
            for key in env:
                key_text = str(key)
                folded = key_text.casefold()
                prior = seen.get(folded)
                if prior is not None and prior != key_text:
                    location = ".".join(trail)
                    collisions.append(
                        f"{path.relative_to(ROOT)}:{location}: {prior!r} vs {key_text!r}"
                    )
                else:
                    seen[folded] = key_text

    assert not collisions, (
        "GitHub Actions rejects env names that collide case-insensitively:\n"
        + "\n".join(collisions)
    )


def test_network_smoke_clears_both_proxy_name_cases_without_duplicate_env_keys() -> None:
    workflow = yaml.safe_load(NETWORK_SMOKE.read_text(encoding="utf-8"))
    step = workflow["jobs"]["public-direct-egress"]["steps"][0]
    assert set(step["env"]) == {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}

    run = str(step["run"])
    assert (
        "unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy"
        in run
    )
