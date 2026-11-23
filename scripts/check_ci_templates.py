"""Validate the CI templates against the official schemas, the way `init --ci` writes them.

    pip install pyyaml==6.0.2 jsonschema==4.23.0
    python scripts/check_ci_templates.py

GitHub Actions: SchemaStore's github-workflow schema. Azure Pipelines: the schema that
Microsoft's Azure Pipelines VS Code extension uses. Each template is checked as written
for one root and for a monorepo with several roots. A schema pass means the YAML is well
formed for that CI; it does not prove a run is green (the GitHub template is run by the
kit's own CI; the Azure one needs a run in an Azure DevOps project).
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import urllib.request
from pathlib import Path

import jsonschema
import yaml

KIT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT))
import verikit  # noqa: E402

SCHEMAS = {
    "github": "https://json.schemastore.org/github-workflow.json",
    "azure": "https://raw.githubusercontent.com/microsoft/azure-pipelines-vscode/main/service-schema.json",
}


def schema(name: str) -> dict:
    cache = Path(os.environ.get("VERIKIT_CACHE", Path(tempfile.gettempdir()))) / f"ci-schema-{name}.json"
    if not cache.exists():
        with urllib.request.urlopen(SCHEMAS[name], timeout=60) as r:
            cache.write_bytes(r.read())
    return json.loads(cache.read_text(encoding="utf-8-sig"))


class AzureLoader(yaml.SafeLoader):
    """Azure's schema types numbers as strings (fetchDepth: "0") but booleans as booleans."""


AzureLoader.yaml_implicit_resolvers = {
    ch: [(tag, rx) for tag, rx in rs if tag not in ("tag:yaml.org,2002:int", "tag:yaml.org,2002:float")]
    for ch, rs in yaml.SafeLoader.yaml_implicit_resolvers.items()}


EACH = re.compile(r"^\$\{\{\s*each\s+(\w+)\s+in\s+parameters\.(\w+)\s*\}\}$")


def expand_azure(doc: dict) -> dict:
    """Do what Azure does before it validates: expand `${{ each x in parameters.p }}` blocks
    with the parameters' defaults and substitute `${{ x }}`. Other expressions stay as text."""
    params = {p["name"]: p.get("default") for p in doc.get("parameters", [])}

    def subst(node, var, value):
        if isinstance(node, str):
            return re.sub(r"\$\{\{\s*" + var + r"\s*\}\}", value, node)
        if isinstance(node, list):
            return [subst(n, var, value) for n in node]
        if isinstance(node, dict):
            return {subst(k, var, value): subst(v, var, value) for k, v in node.items()}
        return node

    def walk(node):
        if isinstance(node, list):
            out = []
            for item in node:
                m = EACH.match(next(iter(item))) if isinstance(item, dict) and len(item) == 1 else None
                if m:
                    body = item[m.group(0)]
                    for value in params[m.group(2)]:
                        out.extend(walk(subst(body, m.group(1), value)))
                else:
                    out.append(walk(item))
            return out
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        return node

    return walk(doc)


def leaves(err):
    """The most specific errors under an anyOf/oneOf failure."""
    if not err.context:
        yield err
    for sub in err.context:
        yield from leaves(sub)


def errors(doc: dict, sch: dict) -> list[str]:
    v = jsonschema.Draft7Validator(sch)
    found = []
    for e in v.iter_errors(doc):
        best = jsonschema.exceptions.best_match([e])
        deepest = max(leaves(best), key=lambda x: len(x.absolute_path))
        found.append(f"{'/'.join(map(str, deepest.absolute_path)) or '(top)'}: {deepest.message[:240]}")
    return found


def main() -> int:
    failed = 0
    cases = [(ci, src, roots) for ci, (src, _) in verikit.CI_TEMPLATES.items()
             for roots in (["."], ["services/api", "web/app"])]
    cases.append(("github", "../../.github/workflows/ci.yml", None))  # the kit's own CI
    for ci, src, roots in cases:
        text = (KIT / "templates" / "ci" / src).read_text(encoding="utf-8")
        if roots is not None:
            text = verikit.set_ci_roots(text, roots)
        doc = yaml.load(text, Loader=AzureLoader) if ci == "azure" else yaml.safe_load(text)
        if ci == "azure":
            doc = expand_azure(doc)
            each_steps = [s for s in doc["steps"] if "script" in s and "verikit.py run" in s["script"]]
            if len(each_steps) != len(roots or []):
                print(f"FAILED  {ci} {src}: expected one run step per root, got {len(each_steps)}")
                failed += 1
        if True in doc:  # YAML 1.1 reads the key `on` as true
            doc["on"] = doc.pop(True)
        errs = errors(doc, schema(ci))
        label = f"{ci:6} {Path(src).name:22} roots={roots}"
        print(f"{'FAILED' if errs else 'valid ':6}  {label}")
        for e in errs[:10]:
            print(f"          {e}")
        failed += bool(errs)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
