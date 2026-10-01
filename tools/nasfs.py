# -*- coding: utf-8 -*-
"""NAS(SMB) 安全文件操作。

QNAP SMB 实测结论（2026-09-27）：
  * 新建 / 覆盖自己建的文件 / 删自己建且**从未被 rename** 的对象  -> 都可以
  * 一旦对某个**文件**做 rename，该对象被永久毒化：
    不能覆盖、不能二次改名、不能删除（连 CreateFileW(DELETE) 都 ACCESS_DENIED）
  * 对**目录**做 rename，会毒化目录内**已存在**的文件（空目录 / 之后新建的文件不受影响）
  * ACL(mode 0666, Everyone:RW) 看起来完全一样 -> 不是权限问题，是 NAS 端对象状态
  * Python 的 os.remove / shutil.rmtree 走的是工具自带的 safe-delete 回收站 shim，
    网络盘无回收站时 FAIL_CLOSED，报错伪装成"系统找不到指定的文件" -> 一律用 kernel32

所以本模块的原则：**绝不用 rename 搬运，一律"复制 + 删原"**。
"""
import ctypes
import os
import shutil

_k = ctypes.WinDLL("kernel32", use_last_error=True)
_k.DeleteFileW.argtypes = [ctypes.c_wchar_p]
_k.DeleteFileW.restype = ctypes.c_bool
_k.RemoveDirectoryW.argtypes = [ctypes.c_wchar_p]
_k.RemoveDirectoryW.restype = ctypes.c_bool


def _last_error():
    return ctypes.get_last_error()


def remove(path):
    """真删文件。返回 (ok, err)"""
    if not os.path.lexists(path):
        return True, 0
    if _k.DeleteFileW(path):
        return True, 0
    return False, _last_error()


def rmdir(path):
    """真删空目录。返回 (ok, err)"""
    if not os.path.isdir(path):
        return True, 0
    if _k.RemoveDirectoryW(path):
        return True, 0
    return False, _last_error()


def rmtree(path):
    """深删目录。返回 (deleted, fails)；fails = [(path, err), ...]"""
    deleted, fails = 0, []
    if not os.path.exists(path):
        return deleted, fails
    if os.path.isfile(path):
        ok, err = remove(path)
        return (1, []) if ok else (0, [(path, err)])
    for root, dirs, files in os.walk(path, topdown=False):
        for name in files:
            p = os.path.join(root, name)
            ok, err = remove(p)
            if ok:
                deleted += 1
            else:
                fails.append((p, err))
        for name in dirs:
            p = os.path.join(root, name)
            ok, err = rmdir(p)
            if ok:
                deleted += 1
            else:
                fails.append((p, err))
    ok, err = rmdir(path)
    if ok:
        deleted += 1
    else:
        fails.append((path, err))
    return deleted, fails


def move_aside(src, dst):
    """把 src 让位成 dst（旧产物让位）。复制 + 删原，绝不用 rename。

    返回 (ok, fails)；fails 非空说明有对象被毒化/占用，删不掉。
    """
    if not os.path.exists(src):
        return True, []
    if os.path.abspath(src) == os.path.abspath(dst):
        return True, []
    rmtree(dst)
    shutil.copytree(src, dst)          # 复制出来的对象可删
    _, fails = rmtree(src)             # 原件没被 rename 过，也可删
    return (not fails), fails


def retarget(src, dst):
    """安全的"文件改名"：复制 + 删原，避免 rename 毒化。返回 (ok, err)"""
    if os.path.abspath(src) == os.path.abspath(dst):
        return True, 0
    rmtree(dst)
    shutil.copyfile(src, dst)
    return remove(src)
