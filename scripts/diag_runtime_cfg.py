#!/usr/bin/env python3
"""诊断 runtime_config.json 中的 api_pools"""
import json
import os

candidates = [
    "/bot/shizuku/data/personification/runtime_config.json",
    "./data/personification/runtime_config.json",
    os.path.expanduser("~/data/personification/runtime_config.json"),
]

cfg_path = None
for p in candidates:
    if os.path.exists(p):
        cfg_path = p
        break

if cfg_path is None:
    print("未找到 runtime_config.json，已搜索:")
    for p in candidates:
        print(f"  {p}")
    raise SystemExit(1)

print(f"读取文件: {cfg_path}")
with open(cfg_path, "r", encoding="utf-8") as f:
    d = json.load(f)

mg = d.get("managed_globals", {})
ap = mg.get("api_pools")

print(f"\n=== managed_globals.api_pools ===")
print(f"存在: {ap is not None}")
if ap is None:
    print("=> runtime_config.json 中没有 api_pools，说明配置来自 .env 文件")
elif isinstance(ap, list):
    print(f"类型: list  长度: {len(ap)}")
    for item in ap:
        if isinstance(item, dict):
            print(f"  - name={item.get('name')}  priority={item.get('priority')}")
else:
    print(f"类型: {type(ap).__name__}  值: {ap}")

print("\n=== managed_globals 所有键 ===")
for k, v in mg.items():
    if k == "api_pools":
        continue
    print(f"  {k}: {repr(v)[:80]}")
