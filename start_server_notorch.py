"""启动脚本 - 跳过 torch 依赖，直接启动 API 服务器"""
import sys
import os

# 在 import 任何 xcmemory 模块之前，先把 lifecycle_manager/core.py 的 torch 引用替换掉
_lifecycle_core_path = os.path.join(
    os.path.dirname(__file__),
    "src", "xcmemory_interest", "lifecycle_manager", "core.py"
)

with open(_lifecycle_core_path, "r", encoding="utf-8") as f:
    _src = f.read()

# 把 import torch 和 torch.nn.functional 替换为空注释
_new_src = _src.replace("import torch\n", "# import torch (patched)\n")
_new_src = _new_src.replace("import torch", "# import torch (patched)")
_new_src = _new_src.replace("import torch.nn.functional as F", "# import torch.nn.functional as F (patched)")

# 写回临时文件
with open(_lifecycle_core_path, "w", encoding="utf-8") as f:
    f.write(_new_src)

print("[patched] lifecycle_manager/core.py - removed torch import")

# 恢复原文件（下次 import 前恢复，避免污染源码）
import atexit
def _restore():
    with open(_lifecycle_core_path, "w", encoding="utf-8") as f:
        f.write(_src)
    print("[restored] lifecycle_manager/core.py")

atexit.register(_restore)

# 现在正常启动
sys.argv = ["start_server.py"]  # 保持原 startup 逻辑
exec(open("start_server.py", encoding="utf-8").read())
