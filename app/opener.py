"""打开目录。当前范围：只做「打开到目录」，不涉及软件版本与工程文件解析。"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def open_dir(path: str) -> str:
    """打开目录；路径是文件时在资源管理器中选中它。返回空串表示成功，否则返回错误说明。"""
    p = str(path or "").strip().strip('"')
    if not p:
        return "路径为空"
    p = os.path.normpath(p)
    try:
        if os.path.isdir(p):
            subprocess.Popen(["explorer", p])
            return ""
        if os.path.isfile(p):
            subprocess.Popen(["explorer", "/select,", p])
            return ""
        parent = str(Path(p).parent)
        if os.path.isdir(parent):
            subprocess.Popen(["explorer", parent])
            return f"路径不存在，已打开上级目录：{p}"
        return f"路径不存在：{p}"
    except Exception as exc:
        return str(exc)
