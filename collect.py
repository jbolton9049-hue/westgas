# -*- coding: utf-8 -*-
"""收集模块：单个文件 / 批量文件夹导入到知识库 01_原始资料库。"""

import os
import shutil
import datetime

from ai_tools import get_kb_path
from process import SUPPORTED

# 支持的资料类型
SUPPORTED_EXT = set(SUPPORTED)


def ensure_raw_dir():
    """确保 01_原始资料库 存在。"""
    raw_dir = os.path.join(get_kb_path(), "01_原始资料库")
    os.makedirs(raw_dir, exist_ok=True)
    return raw_dir


def collect_single(file_path, verbose=True):
    """导入单个文件。返回目标路径。"""
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"文件不存在: {file_path}")
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in SUPPORTED_EXT:
        raise ValueError(f"不支持的文件类型: {ext}（支持 {sorted(SUPPORTED_EXT)}）")

    raw_dir = ensure_raw_dir()
    today = datetime.date.today().isoformat()
    fname = os.path.basename(file_path)
    # 目标命名：日期_原名
    target = os.path.join(raw_dir, f"{today}_{fname}")
    # 重名则加序号
    i = 1
    while os.path.exists(target):
        name, e = os.path.splitext(fname)
        target = os.path.join(raw_dir, f"{today}_{name}_{i}{e}")
        i += 1
    shutil.copy2(file_path, target)
    if verbose:
        print(f"✅ 已导入: {target}")
    return target


def collect_batch(folder, verbose=True):
    """批量导入文件夹内所有支持的文件。返回导入数量。"""
    if not os.path.isdir(folder):
        raise FileNotFoundError(f"文件夹不存在: {folder}")
    count = 0
    imported = []
    for fname in sorted(os.listdir(folder)):
        fpath = os.path.join(folder, fname)
        if os.path.isfile(fpath) and os.path.splitext(fname)[1].lower() in SUPPORTED_EXT:
            try:
                t = collect_single(fpath, verbose=False)
                imported.append(t)
                count += 1
            except Exception as e:
                print(f"⚠️ 跳过 {fname}: {e}")
    if verbose:
        print(f"✅ 批量导入完成，共 {count} 个文件")
        for t in imported:
            print(f"   - {t}")
    return count
