#!/usr/bin/env python3
"""Validate canonical JSON data and generate browser-consumable JS wrappers."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SPECS = (
    ("知识点数据库.json", "知识点数据库.js", "_DB_DATA"),
    ("关联连线数据库.json", "关联连线数据库.js", "_CONN_DATA"),
    ("易混淆概念对比表.json", "易混淆概念对比表.js", "_CONF_DATA"),
    ("知识链排序配置表.json", "知识链排序配置表.js", "_CHAIN_DATA"),
    ("生活应用场景.json", "生活应用场景.js", "_LIFE_DATA"),
)
EXTERNAL_SUBJECTS = {"数学", "生物", "化学", "地理", "工程", "艺术", "历史"}


def read_json(name: str) -> Any:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def dump_json(data: Any) -> str:
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").replace("\n", "\r\n")


def wrapper(data: Any, variable: str, source_name: str) -> str:
    return (
        f"// 此文件由 {source_name} 自动生成，请勿直接编辑。\n"
        f"window.{variable} = {json.dumps(data, ensure_ascii=False, indent=2)};\n"
    ).replace("\n", "\r\n")


def parse_wrapper(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    match = re.fullmatch(
        r"\s*(?://[^\n]*\n)?window\.[A-Z_]+\s*=\s*(.*);\s*",
        text,
        re.DOTALL,
    )
    if not match:
        raise ValueError(f"{path.name} 不是标准数据包装文件")
    return json.loads(match.group(1))


def bootstrap_from_js() -> None:
    """One-time migration: make the richer runtime JS data canonical JSON."""
    for json_name, js_name, _ in SPECS:
        data = parse_wrapper(ROOT / js_name)
        (ROOT / json_name).write_text(dump_json(data), encoding="utf-8")


def relation_key(relation: dict[str, Any]) -> tuple[str, str]:
    return str(relation.get("from", "")).strip(), str(relation.get("to", "")).strip()


def unify_prerequisites(nodes_doc: dict[str, Any], relations_doc: dict[str, Any]) -> dict[str, int]:
    """Use the lossless union of both legacy prerequisite sources."""
    nodes = nodes_doc["data"]
    relations = relations_doc["data"]
    node_ids = {node["id"] for node in nodes}
    union: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(source: str, target: str) -> None:
        pair = (str(source).strip(), str(target).strip())
        if pair[0] in node_ids and pair[1] in node_ids and pair not in seen:
            seen.add(pair)
            union.append(pair)

    for relation in relations:
        if relation.get("type") == "prerequisite":
            add(*relation_key(relation))
    for node in nodes:
        for prerequisite in node.get("prerequisites", []):
            add(prerequisite, node["id"])

    existing_pairs = {
        relation_key(relation)
        for relation in relations
        if relation.get("type") == "prerequisite"
    }
    added_relations = []
    for source, target in union:
        if (source, target) not in existing_pairs:
            added_relations.append(
                {
                    "from": source,
                    "to": target,
                    "type": "prerequisite",
                    "color": "",
                    "width": 2,
                    "strength": 3,
                }
            )
    relations_doc["data"] = relations + added_relations
    relations_doc["total_count"] = len(relations_doc["data"])

    incoming: dict[str, list[str]] = defaultdict(list)
    outgoing: dict[str, list[str]] = defaultdict(list)
    for source, target in union:
        incoming[target].append(source)
        outgoing[source].append(target)
    for node in nodes:
        node["prerequisites"] = incoming[node["id"]]
        node["follow_ups"] = outgoing[node["id"]]

    return {
        "nodes": len(nodes),
        "prerequisites": len(union),
        "relations": len(relations_doc["data"]),
    }


def validate(
    nodes_doc: dict[str, Any],
    relations_doc: dict[str, Any],
    chain_doc: dict[str, Any],
    life_doc: dict[str, Any],
    confusion_doc: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    nodes = nodes_doc.get("data", [])
    node_ids = {node.get("id") for node in nodes}
    if len(node_ids) != len(nodes):
        errors.append("知识点 ID 缺失或重复")

    field_pairs = {
        (prerequisite, node["id"])
        for node in nodes
        for prerequisite in node.get("prerequisites", [])
    }
    edge_pairs = {
        relation_key(relation)
        for relation in relations_doc.get("data", [])
        if relation.get("type") == "prerequisite"
    }
    if field_pairs != edge_pairs:
        errors.append(
            f"前置关系双源不一致：节点独有 {len(field_pairs - edge_pairs)}，关系独有 {len(edge_pairs - field_pairs)}"
        )

    for relation in relations_doc.get("data", []):
        source, target = relation_key(relation)
        missing = [endpoint for endpoint in (source, target) if endpoint not in node_ids]
        if not missing:
            continue
        if relation.get("type") != "cross_disciplinary":
            errors.append(f"非跨学科关系存在悬空端点：{source} -> {target}")
            continue
        for endpoint in missing:
            subject = endpoint.split("_", 1)[0]
            if subject not in EXTERNAL_SUBJECTS:
                errors.append(f"跨学科外部端点缺少有效学科前缀：{endpoint}")

    for chain in chain_doc.get("knowledgeChains", []):
        missing = [node_id for node_id in chain.get("nodes", []) if node_id not in node_ids]
        if missing:
            errors.append(f"知识链 {chain.get('chainId')} 存在无效节点：{', '.join(missing)}")
    for item in life_doc.get("lifeApplications", []):
        if item.get("nodeId") not in node_ids:
            errors.append(f"生活场景存在无效节点：{item.get('nodeId')}")
    for pair in confusion_doc.get("confusingConcepts", []):
        for key in ("idA", "idB"):
            if pair.get(key) and pair.get(key) not in node_ids:
                errors.append(f"易混概念 {pair.get('id')} 的 {key} 无效：{pair.get(key)}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--bootstrap-from-js",
        action="store_true",
        help="一次性将现有运行时 JS 数据迁移为规范 JSON",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="统一前置关系并从规范 JSON 生成 JS",
    )
    args = parser.parse_args()

    if args.bootstrap_from_js:
        bootstrap_from_js()

    nodes_doc = read_json("知识点数据库.json")
    relations_doc = read_json("关联连线数据库.json")
    stats = None
    if args.write:
        stats = unify_prerequisites(nodes_doc, relations_doc)
        (ROOT / "知识点数据库.json").write_text(dump_json(nodes_doc), encoding="utf-8")
        (ROOT / "关联连线数据库.json").write_text(dump_json(relations_doc), encoding="utf-8")

    documents = {json_name: read_json(json_name) for json_name, _, _ in SPECS}
    errors = validate(
        documents["知识点数据库.json"],
        documents["关联连线数据库.json"],
        documents["知识链排序配置表.json"],
        documents["生活应用场景.json"],
        documents["易混淆概念对比表.json"],
    )
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    mismatched = []
    for json_name, js_name, variable in SPECS:
        expected = wrapper(documents[json_name], variable, json_name)
        destination = ROOT / js_name
        if args.write:
            destination.write_text(expected, encoding="utf-8")
        elif not destination.exists() or destination.read_text(encoding="utf-8") != expected:
            mismatched.append(js_name)
    if mismatched:
        print("ERROR: 生成文件未同步：" + "、".join(mismatched), file=sys.stderr)
        return 1

    if stats:
        print(
            f"已统一 {stats['nodes']} 个节点、{stats['prerequisites']} 条前置关系；"
            f"关系总数 {stats['relations']}。"
        )
    print("数据校验通过；JSON 是唯一事实来源，JS 生成文件已同步。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
