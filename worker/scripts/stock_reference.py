#!/usr/bin/env python3
"""Helpers for optional A-share terminology validation."""

from __future__ import annotations

import csv
import os
import pathlib
import re
from functools import lru_cache


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_CODE_LIST = PROJECT_ROOT / "docs" / "code_list_20260612.csv"


@lru_cache(maxsize=1)
def load_stock_rows() -> list[dict[str, str]]:
    raw_path = os.environ.get("A_SHARE_CODE_LIST_FILE", "").strip()
    path = pathlib.Path(raw_path).expanduser() if raw_path else DEFAULT_CODE_LIST
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [row for row in csv.DictReader(handle) if row.get("name") and row.get("ts_code")]


def _normalize_code(token: str) -> str:
    token = token.strip().upper()
    if re.fullmatch(r"[036]\d{5}", token):
        suffix = ".SZ" if token.startswith(("0", "3")) else ".SH"
        return token + suffix
    return token


def detect_stock_matches(text: str, limit: int = 40) -> list[dict[str, str]]:
    if not text.strip():
        return []
    rows = load_stock_rows()
    if not rows:
        return []

    code_map = {row["ts_code"].upper(): row for row in rows}
    name_rows = sorted(rows, key=lambda row: len(row["name"]), reverse=True)
    matches: list[dict[str, str]] = []
    seen: set[str] = set()

    for raw_code in re.findall(r"\b(?:[036]\d{5}(?:\.(?:SZ|SH))?)\b", text, flags=re.IGNORECASE):
        code = _normalize_code(raw_code)
        row = code_map.get(code)
        if not row:
            continue
        key = row["ts_code"]
        if key in seen:
            continue
        seen.add(key)
        matches.append(row)
        if len(matches) >= limit:
            return matches

    for row in name_rows:
        name = row["name"]
        if name in text and row["ts_code"] not in seen:
            seen.add(row["ts_code"])
            matches.append(row)
            if len(matches) >= limit:
                break
    return matches


def build_stock_reference_prompt(text: str, enabled: bool) -> str:
    if not enabled:
        return ""
    matches = detect_stock_matches(text)
    if matches:
        refs = "；".join(f"{row['name']}({row['ts_code']})" for row in matches[:20])
        return (
            "6z) A股术语校验：如果内容涉及以下 A 股证券，请保持股票名称、股票代码和简称完全一致，"
            "不要改写成同音词、近义词或错误代码；若不确定，以原始表述为准并在必要时标注待核验。\n"
            f"参考证券：{refs}\n"
        )
    return (
        "6z) A股术语校验：如果内容涉及 A 股公司、股票简称或 6 位股票代码，"
        "请尽量保持标准写法；遇到不确定的股票名称或代码时，不要擅自编造，可以保留原文并标注待核验。\n"
    )


def build_stock_validation_section(text: str, enabled: bool) -> str:
    if not enabled:
        return ""
    matches = detect_stock_matches(text, limit=80)
    if not matches:
        return "\n\n".join(
            [
                "## A股术语校验",
                "> 未在正文中识别到明确的 A 股股票名称或标准代码；如果这是证券内容，建议人工复核专有名词。",
            ]
        )
    rows = ["| 股票名称 | 股票代码 | 地区 | 行业 | 市场 |", "| --- | --- | --- | --- | --- |"]
    for row in matches[:40]:
        rows.append(
            f"| {row.get('name', '')} | {row.get('ts_code', '')} | {row.get('area', '')} | {row.get('industry', '')} | {row.get('market', '')} |"
        )
    return "\n\n".join(
        [
            "## A股术语校验",
            "> 以下证券名称/代码已在本笔记中识别，可用来复核 ASR 或整理结果是否写错。",
            "\n".join(rows),
        ]
    )
