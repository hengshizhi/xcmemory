"""
星尘记忆 - 兴趣编码器配置
"""

# 槽位定义
SLOT_NAMES = ["scene", "subject", "action", "object", "purpose", "result"]
NUM_SLOTS = len(SLOT_NAMES)
SLOT_DIM = 64


def _detect_device() -> str:
    """自动检测最优计算设备。"""
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except ImportError:
        pass
    return "cpu"


DEVICE = _detect_device()

# 模型配置
DEFAULT_CONFIG = {
    "vocab_size": 32000,      # 词汇表大小
    "slot_dim": SLOT_DIM,      # 每个槽位64维
    "num_heads": 4,            # 注意力头数
    "num_layers": 2,           # 注意力层数
    "dropout": 0.0,            # Dropout（推理用0）
    "margin": 0.3,             # 对比损失 margin
}

# 训练配置
TRAIN_CONFIG = {
    "batch_size": 64,
    "lr": 1e-3,
    "weight_decay": 0.01,
    "warmup_steps": 500,
    "max_steps": 10000,
    "eval_interval": 200,
    "save_interval": 1000,
}
