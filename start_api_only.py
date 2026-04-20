"""极简启动脚本：只启动 API Server，不碰 lifecycle_manager/torch"""
import sys
from pathlib import Path

# 添加 src 路径
sys.path.insert(0, str(Path(__file__).parent / "src"))

# 直接导入 netapi（只依赖 user_manager + mql，不碰 torch）
from xcmemory_interest.netapi import APIServer

CONFIG_FILE = Path(__file__).parent / "config.toml"
import tomllib

config = tomllib.loads(CONFIG_FILE.read_text(encoding="utf-8"))

openai_cfg = {
    "api_key": config["openai"]["api_key"],
    "base_url": config["openai"].get("base_url", "https://openrouter.ai/api/v1"),
    "model": config["openai"].get("model", "xiaomi/mimo-v2-flash"),
}

server = APIServer(
    database_root=config["server"]["database_root"],
    host=config["server"]["host"],
    port=config["server"]["port"],
    ws_port=config["server"]["ws_port"],
    debug=False,
    openai_config=openai_cfg,
)

print(f"API Server starting on {config['server']['host']}:{config['server']['port']}")
server.start()
