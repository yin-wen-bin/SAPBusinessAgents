"""Refresh/check bounded documentation facts; never rewrites narrative or archives.

Run from the repository root: python scripts/documentation.py --write / --check.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from sap_business_agents_platform.manifests import AgentRepository, is_agent_executable
from sap_business_agents_platform.workflows import WorkflowRepository

ROOT = Path(__file__).resolve().parents[1]
MODULE_NAMES = {"Common": "通用 / Common", "FI": "财务会计 / Financial Accounting", "CO": "管理会计 / Controlling", "MM": "物料管理 / Materials Management", "PP": "生产计划 / Production Planning", "SD": "销售与分销 / Sales and Distribution"}
LABELS = {"PASS": "验收通过 / Passed", "BLOCKED": "因证据或验收缺口受阻 / Blocked", "NOT_TESTED": "尚未验收 / Not tested", "PARTIAL": "部分通过 / Partial", "FAIL": "验收未通过 / Failed", "active": "使用中 / Active", "inactive": "已停用 / Inactive"}


def relative(source: Path, target: Path) -> str:
    return Path(os.path.relpath(target, source.parent)).as_posix()


def cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def replace_block(text: str, key: str, content: str) -> str:
    start, end = f"<!-- generated:{key}:start -->", f"<!-- generated:{key}:end -->"
    if text.count(start) != 1 or text.count(end) != 1:
        raise ValueError(f"Missing or duplicate documentation block: {key}")
    before, tail = text.split(start)
    _, after = tail.split(end)
    return f"{before}{start}\n{content.rstrip()}\n{end}{after}"


def input_rows(schema: dict, prefix: str = "", inherited_required: bool = True):
    for key, field in schema.get("properties", {}).items():
        if field.get("x-sapba-internal") or field.get("x-sapba-workflow-only"):
            continue
        name = f"{prefix}{key}"
        required = key in schema.get("required", [])
        fallback_titles = {"company_code": {"zh": "公司代码", "en": "Company code"}, "fiscal_year": {"zh": "会计年度", "en": "Fiscal year"}, "period": {"zh": "会计期间", "en": "Fiscal period"}, "as_of": {"zh": "查询基准日", "en": "As-of date"}}
        title = field.get("title") or fallback_titles.get(key, {})
        if isinstance(title, str):
            title = {"zh": title, "en": title}
        constraints = {k: field[k] for k in ("format", "minItems", "maxItems", "minimum", "maximum", "minLength", "maxLength", "enum", "pattern", "default", "x-sapba-server-default", "x-sapba-sensitive") if k in field}
        rule = "必填 / Required" if required and inherited_required else "提供本行时必填 / Required when supplied" if required else "可选 / Optional"
        yield f"| `{name}` | {cell(title.get('zh', key))} / {cell(title.get('en', key))} | {rule} | `{field.get('type', 'object')}` | {cell(json.dumps(constraints, ensure_ascii=False)) if constraints else '—'} |"
        child = field.get("items", {}) if field.get("type") == "array" else field
        if child.get("properties"):
            yield from input_rows(child, name + ("[]." if field.get("type") == "array" else "."), required and inherited_required)


def agent_facts(repo: AgentRepository, a: dict, path: Path) -> str:
    state = repo.lifecycle(a["slug"])["state"]
    v = a.get("validation") or {}
    assistant = a.get("kind") == "platform_assistant"
    lines = [f"版本 / Version: **{a['version']}** · {LABELS[state]} · {LABELS.get(v.get('verdict'), '平台助理：由 Runtime 能力门禁控制 / Platform assistant: Runtime capability gate')}", "",
             "网页 / Web: " + " · ".join(f"[{lang}](http://127.0.0.1:4321/{lang}/agents/{a['module']}/{a['slug']}/)" for lang in ("zh", "en")), ""]
    if state == "inactive":
        lines.append("已停用：只在 Agent 管理中维护，不接受新运行。 / Inactive: manage it in Agent management; new runs are blocked.")
    elif not assistant and not is_agent_executable(a):
        lines.append("当前不能执行；完成证据与验收门禁后才可启用执行。 / Execution is blocked until evidence and acceptance gates pass.")
    if v:
        reuse = v.get("documentationReuse")
        lines.extend([f"验收模式 / Acceptance mode: `{v.get('acceptanceMode', 'not_recorded')}` · 原记录日期 / Recorded date: {v.get('testedAt', 'not_recorded')}",
                      f"证据范围 / Evidence scope: `{v.get('evidenceScope', 'not_recorded')}`"])
        if reuse:
            lines.append(f"复用 {reuse['sourceVersion']} 验收；文案复核不代表重新查询 SAP。 / Acceptance reused from {reuse['sourceVersion']}; documentation review is not a new SAP test.")
        for lang in ("zh", "en"):
            lines.append((v.get("summary") or {}).get(lang, ""))
        gaps = v.get("blockingLimitations") or []
        if gaps:
            lines += ["", "当前阻塞项 / Current blockers:", ""] + [f"- `{cell(g)}`" for g in gaps]
    lines += ["", "### 输入 / Inputs", "", "| 字段 / Field | 名称 / Name | 要求 / Requirement | 类型 / Type | 约束 / Constraints |", "|---|---|---|---|---|"]
    schema = (a.get("execution") or {}).get("inputSchema") or a.get("inputSchema") or {}
    if assistant:
        schema = {"properties": {
            "roleDescription": {"type": "string", "maxLength": 12000, "title": {"zh": "岗位描述（与路径至少提供一项）", "en": "Role description (or paths required)"}},
            "paths": {"type": "array", "maxItems": 100, "title": {"zh": "文档路径（与描述至少提供一项）", "en": "Document paths (or description required)"}},
            "consentToRuntime": {"type": "boolean", "enum": [True], "title": {"zh": "确认发送正文", "en": "Consent to send source text"}},
        }, "required": ["consentToRuntime"]}
    rows = list(input_rows(schema))
    lines += rows or ["| — | 参见使用说明 / See usage below | — | — | — |"]
    lines += ["", "约束中的 default 是默认值；示例不是 SAP 测试样本。条件必填规则以网页提示和完整 Schema 为准。 / `default` denotes a default, not live test data. Conditional requirements are defined by the form and full Schema.", "", "### 结果字段 / Result fields", ""]
    lines += [f"- {cell(zh)} / {cell(en)}" for zh, en in zip(a.get("outputs", {}).get("zh", []), a.get("outputs", {}).get("en", []), strict=True)]
    lines += ["", "### 数据与开发资料 / Contracts and development", "", "- [Agent 定义与完整输入输出契约 / Manifest and complete I/O contract](agent.json)"]
    report = v.get("reportPath")
    if report:
        target = path.parent / report
        if not target.exists():
            target = ROOT / report
        lines.append(f"- [原始验收记录（适用范围以原报告为准） / Original acceptance record (original scope applies)]({relative(path, target)})")
    for name in ("docs/sap-data-contract.md", "docs/offline-regression.md", "rules.py", "tests"):
        if (path.parent / name).exists():
            lines.append(f"- [{name}]({name})")
    lines.append(f"- [开发指南 / Developer guide]({relative(path, ROOT / 'docs/developer-guide.md')})")
    return "\n".join(lines)


def index(repo: AgentRepository, agents: list[dict], path: Path, lang: str | None = None) -> str:
    header = "| 模块 / Module | Agent | 版本 / Version | 生命周期 / Lifecycle | 验收 / Acceptance |"
    if lang == "zh":
        header = "| 模块 | Agent | 版本 | 生命周期 | 验收 |"
    elif lang == "en":
        header = "| Module | Agent | Version | Lifecycle | Acceptance |"
    lines = [header, "|---|---|---|---|---|"]
    for a in agents:
        title = a["title"].get(lang) if lang else a["title"]["zh"] + " / " + a["title"]["en"]
        target = repo._path(a["slug"]).parent / "README.md"
        state = LABELS[repo.lifecycle(a['slug'])['state']]
        acceptance = LABELS.get((a.get('validation') or {}).get('verdict'), '平台能力门禁 / Platform gate')
        if lang:
            offset = 0 if lang == "zh" else 1
            state, acceptance = state.split(" / ")[offset], acceptance.split(" / ")[offset]
        lines.append(f"| {a['module']} | [{cell(title)}]({relative(path, target)}) | {a['version']} | {state} | {acceptance} |")
    return "\n".join(lines)


def document_updates(root: Path = ROOT):
    repo = AgentRepository(root / "agents")
    agents = sorted(repo.list_all(), key=lambda a: (a["module"], a["slug"]))
    readme = root / "README.md"
    text = readme.read_text(encoding="utf-8")
    for lang in ("zh", "en"):
        text = replace_block(text, "agents-" + lang, index(repo, agents, readme, lang))
    yield readme, text
    for module in sorted({a["module"] for a in agents}):
        path = root / "agents" / module / "README.md"
        text = path.read_text(encoding="utf-8")
        yield path, replace_block(text, "agents", index(repo, [a for a in agents if a["module"] == module], path))
    for a in agents:
        path = repo._path(a["slug"]).parent / "README.md"
        text = path.read_text(encoding="utf-8")
        yield path, replace_block(text, "facts", agent_facts(repo, a, path))
    workflows = WorkflowRepository(root / "workflows", repo)
    for w in workflows.catalog("all"):
        path = workflows._current_path(w["id"]).parent / "README.md"
        text = path.read_text(encoding="utf-8")
        yield path, replace_block(text, "workflow", workflow_facts(root, workflows, w, path))


def workflow_facts(root: Path, repo, w: dict, path: Path) -> str:
    manifest = json.loads((path.parent / "workflow.json").read_text(encoding="utf-8"))
    publication = json.loads((path.parent / "publication.json").read_text(encoding="utf-8")) if (path.parent / "publication.json").exists() else {}
    validation = json.loads((path.parent / "validation.json").read_text(encoding="utf-8")) if (path.parent / "validation.json").exists() else {}
    state = w["lifecycle"]["state"]
    lines = [f"版本 / Version: {manifest['version']} · {LABELS.get(state, state)}", "", f"[正式定义 / Pinned definition](workflow.json) · [我的工作流 / My workflows](http://127.0.0.1:4321/zh/workflows/)", "", "### 输入 / Inputs", "", "| 字段 / Field | 名称 / Name | 要求 / Requirement | 类型 / Type | 约束 / Constraints |", "|---|---|---|---|---|"]
    lines += list(input_rows(manifest["inputSchema"]))
    lines += ["", "### 固定节点 / Pinned nodes", ""]
    for node in manifest["nodes"]:
        lines.append(f"- `{node['id']}` → `{node.get('agentId')}` **{node.get('agentVersion')}**")
    lines += ["", "### 最终输出 / Final outputs", ""]
    lines += [f"- `{o['name']}`" for o in manifest.get("outputs", [])]
    if validation:
        lines += ["", f"发布验证 / Publication validation: `{w['publication']['validation_status']}` · {w['publication'].get('validated_at') or 'not_recorded'}", "", "[发布验证记录（保留原结论） / Publication validation (original verdict preserved)](validation.json)"]
        lines += [f"- `{gap}`" for gap in w['publication']['evidence_gap_codes']]
    else:
        lines += ["", "无发布验证记录，不据此宣称通过。 / No publication-validation record; this does not establish acceptance."]
    return "\n".join(lines)


def local_link_errors(paths: list[Path], root: Path = ROOT) -> list[str]:
    errors = []
    for path in paths:
        text = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.S)
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
            target = target.strip("<>").split(' "')[0]
            url = urlsplit(target)
            if url.scheme or url.netloc:
                continue
            resolved = (path.parent / unquote(url.path)).resolve() if url.path else path.resolve()
            if not resolved.is_relative_to(root.resolve()) or not resolved.exists():
                errors.append(f"{path.relative_to(root)}: broken local link {target}")
            elif url.fragment and resolved.suffix == ".md":
                linked = resolved.read_text(encoding="utf-8")
                anchors = set(re.findall(r'<a\s+id="([^"]+)"', linked))
                seen = {}
                for heading in re.findall(r"^#{1,6}\s+(.+)$", linked, flags=re.M):
                    slug = re.sub(r"[^\w\s-]", "", heading.lower()).replace(" ", "-")
                    count = seen.get(slug, 0)
                    seen[slug] = count + 1
                    anchors.add(slug + (f"-{count}" if count else ""))
                if unquote(url.fragment) not in anchors:
                    errors.append(f"{path.relative_to(root)}: broken local anchor {target}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    changed, paths = [], []
    for path, text in document_updates():
        paths.append(path)
        if path.read_text(encoding="utf-8") != text:
            changed.append(str(path.relative_to(ROOT)))
            if args.write:
                path.write_text(text, encoding="utf-8")
    paths += [p for p in (ROOT / "docs").glob("*.md") if p.name in {"getting-started.md", "developer-guide.md", "runtime-settings.md", "free-query.md", "documentation-update.md", "user-workflows.md", "role-agent-matching.md", "ar-collection-cash-application.md", "local-plugin-platform.md", "runtime-integrations.md", "agent-lifecycle-management.md"}]
    errors = local_link_errors(paths)
    if not args.write:
        errors += ["Stale documentation: " + p for p in changed]
    print(json.dumps({"checked_documents": len(paths), "changed_blocks": len(changed), "errors": errors}, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
