# Change log:
# Fri Nov 20 01:39:30 CET 2026 - Initial version: turn arbitrary MCP tool payloads into tables, metrics and chart data
# Fri Nov 20 01:39:30 CET 2026 - Added csv_safe() for CSV export
# Fri Nov 20 02:45:17 CET 2026 - Increase MAX_ROWS and add render_paginated_table for multipage/scrollable data display
"""Helpers that normalize an arbitrary JSON payload into displayable structures."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

MAX_ROWS = 20000
MAX_CATEGORIES = 30


@dataclass
class ParsedPayload:
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    text: str = ""


# Fri Nov 20 01:39:30 CET 2026 - Stringify nested cells so Arrow can render the frame
def _stringify_nested(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.columns:
        if df[col].map(lambda v: isinstance(v, dict | list)).any():
            df[col] = df[col].map(lambda v: json.dumps(v, default=str) if isinstance(v, dict | list) else v)
    return df


# Fri Nov 20 01:39:30 CET 2026 - Build a DataFrame from a list payload (records or plain values)
def _list_to_frame(items: list) -> pd.DataFrame:
    items = items[:MAX_ROWS]
    if items and all(isinstance(i, dict) for i in items):
        return _stringify_nested(pd.json_normalize(items))
    return pd.DataFrame({"value": [json.dumps(i, default=str) if isinstance(i, dict | list) else i for i in items]})


# Fri Nov 20 01:39:30 CET 2026 - Flatten nested dicts into dotted scalar keys
def _flatten(prefix: str, value: Any, out: dict[str, Any]) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            _flatten(f"{prefix}.{k}" if prefix else str(k), v, out)
    else:
        out[prefix] = value


# Fri Nov 20 01:39:30 CET 2026 - Split a payload into tables (lists), metrics (scalars) and text
def parse_payload(payload: Any) -> ParsedPayload:
    parsed = ParsedPayload()
    if isinstance(payload, list):
        if payload:
            parsed.tables["Records"] = _list_to_frame(payload)
    elif isinstance(payload, dict):
        scalars: dict[str, Any] = {}
        for key, value in payload.items():
            if isinstance(value, list):
                if value:
                    parsed.tables[str(key)] = _list_to_frame(value)
            elif isinstance(value, dict):
                _flatten(str(key), value, scalars)
            else:
                scalars[str(key)] = value
        parsed.metrics = {
            k: (json.dumps(v, default=str) if isinstance(v, list) else v) for k, v in scalars.items()
        }
    elif isinstance(payload, str):
        parsed.text = payload
    elif payload is not None:
        parsed.metrics["value"] = payload
    return parsed


# Fri Nov 20 01:39:30 CET 2026 - Low-cardinality text columns suitable for a count chart
def categorical_columns(df: pd.DataFrame) -> list[str]:
    cols = []
    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]) and not pd.api.types.is_bool_dtype(df[col]):
            continue
        if 2 <= df[col].nunique(dropna=True) <= MAX_CATEGORIES:
            cols.append(str(col))
    return cols


# Fri Nov 20 01:39:30 CET 2026 - Numeric (non-boolean) columns
def numeric_columns(df: pd.DataFrame) -> list[str]:
    return [
        str(c) for c in df.columns
        if pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c])
    ]


# Fri Nov 20 01:39:30 CET 2026 - Count of rows per category value
def count_by(df: pd.DataFrame, column: str) -> pd.DataFrame:
    counts = df[column].fillna("(empty)").astype(str).value_counts().reset_index()
    counts.columns = [column, "count"]
    return counts


# Fri Nov 20 01:39:30 CET 2026 - First rows of a frame for a label/value bar chart
def label_value_frame(df: pd.DataFrame, label: str, value: str, limit: int = 25) -> pd.DataFrame:
    out = df[[label, value]].dropna().head(limit).copy()
    out[label] = out[label].astype(str)
    return out


# Fri Nov 20 01:39:30 CET 2026 - Neutralize spreadsheet formula injection in exported CSV text cells
def csv_safe(df: pd.DataFrame) -> pd.DataFrame:
    def fix(v):
        return "'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r") else v

    out = df.copy()
    for col in out.columns:
        if out[col].dtype == object or pd.api.types.is_string_dtype(out[col]):
            out[col] = out[col].map(fix)
    return out


# Fri Nov 20 02:45:17 CET 2026 - Render a DataFrame with pagination controls (rows per page, page jump, prev/next) and scrollable view
def render_paginated_table(
    df: pd.DataFrame,
    key_prefix: str,
    tool_name: str = "",
    table_name: str = "Records",
    default_page_size: int | str = 50,
) -> None:
    """Render a DataFrame with pagination controls and scrollable table view."""
    import streamlit as st

    total_rows = len(df)
    if total_rows == 0:
        st.info(f"No records found in {table_name}.")
        return

    # For small tables (<= 25 records), render directly
    if total_rows <= 25:
        st.dataframe(df, hide_index=True, alt=f"{table_name} data ({total_rows:,} records)", width="stretch")
        return

    page_size_options = [25, 50, 100, 250, 500, "All"]
    default_idx = (
        page_size_options.index(default_page_size)
        if default_page_size in page_size_options
        else 1
    )

    page_key = f"curr_page_{key_prefix}"
    if page_key not in st.session_state:
        st.session_state[page_key] = 1
        st.session_state[f"sel_{page_key}"] = 1

    c_info, c_prev, c_page, c_next, c_size = st.columns([3, 1, 1.5, 1, 1.5], vertical_alignment="center")

    with c_size:
        page_size_val = st.selectbox(
            "Rows per page",
            options=page_size_options,
            index=default_idx,
            key=f"ps_{key_prefix}",
            help="Choose records per page or select 'All' to scroll through all records.",
            label_visibility="collapsed",
        )

    if page_size_val == "All":
        with c_info:
            st.caption(f"Showing all **{total_rows:,}** records (scroll table below)")
        st.dataframe(df, hide_index=True, alt=f"{table_name} data (all {total_rows:,} records)", width="stretch")
        return

    page_size = int(page_size_val)
    total_pages = max(1, math.ceil(total_rows / page_size))

    if st.session_state[page_key] > total_pages:
        st.session_state[page_key] = total_pages
        st.session_state[f"sel_{page_key}"] = total_pages
    if st.session_state[page_key] < 1:
        st.session_state[page_key] = 1
        st.session_state[f"sel_{page_key}"] = 1

    def _prev():
        new_p = max(1, st.session_state[page_key] - 1)
        st.session_state[page_key] = new_p
        st.session_state[f"sel_{page_key}"] = new_p

    def _next():
        new_p = min(total_pages, st.session_state[page_key] + 1)
        st.session_state[page_key] = new_p
        st.session_state[f"sel_{page_key}"] = new_p

    def _set_page():
        st.session_state[page_key] = st.session_state[f"sel_{page_key}"]

    current_page = st.session_state[page_key]
    start_idx = (current_page - 1) * page_size
    end_idx = min(start_idx + page_size, total_rows)

    with c_info:
        st.caption(f"Showing **{start_idx + 1:,}–{end_idx:,}** of **{total_rows:,}** records")

    with c_prev:
        st.button("◀", key=f"btn_prev_{key_prefix}", disabled=(current_page <= 1), on_click=_prev, width="stretch", help="Previous page")

    with c_page:
        st.selectbox(
            "Page",
            options=list(range(1, total_pages + 1)),
            index=current_page - 1,
            key=f"sel_{page_key}",
            on_change=_set_page,
            label_visibility="collapsed",
            help="Jump to page",
        )

    with c_next:
        st.button("▶", key=f"btn_next_{key_prefix}", disabled=(current_page >= total_pages), on_click=_next, width="stretch", help="Next page")

    page_df = df.iloc[start_idx:end_idx]
    st.dataframe(page_df, hide_index=True, alt=f"{table_name} data (page {current_page} of {total_pages})", width="stretch")
