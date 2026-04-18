"""
星尘记忆 - 兴趣编码器配置
"""

# 槽位定义
SLOT_NAMES = ["time", "subject", "action", "object", "purpose", "result"]
NUM_SLOTS = len(SLOT_NAMES)
SLOT_DIM = 64

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
