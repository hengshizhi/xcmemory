# -*- coding: utf-8 -*-
"""
测试访问触发生命周期增长的 Sigmoid 导数衰减函数
"""

import sys
import os

# 测试文件路径设置
test_file = os.path.abspath(__file__)
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(test_file))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import numpy as np


def sigmoid_derivative(t, mid, k):
    """Sigmoid 导数"""
    sig = 1.0 / (1.0 + np.exp(-k * (t - mid)))
    return k * sig * (1.0 - sig)


def sigmoid_derivative_with_floor(t, mid, k, min_scale=0.1):
    """带保底的 Sigmoid 导数（归一化版本）"""
    sig = 1.0 / (1.0 + np.exp(-k * (t - mid)))
    deriv = k * sig * (1.0 - sig)
    max_deriv = k / 4.0
    normalized = deriv / max_deriv if max_deriv > 0 else 0.0
    scale = min_scale + (1.0 - min_scale) * normalized
    return scale


def apply_access_decay(old_lc, interim_new_lc, short_cap=7*86400, long_cap=30*86400,
                       transition_cap=365*86400, min_scale=0.1):
    """模拟 _apply_access_decay 逻辑"""
    delta = interim_new_lc - old_lc
    if delta <= 0:
        return old_lc
    if old_lc >= transition_cap:
        return 999999

    if old_lc < short_cap:
        mid = short_cap / 2.0
        k = 2.0 / short_cap
    else:
        mid = (short_cap + long_cap) / 2.0
        k = 2.0 / (long_cap - short_cap)

    scale = sigmoid_derivative_with_floor(old_lc, mid, k, min_scale)
    actual_delta = delta * scale
    return int(max(old_lc + 1, old_lc + actual_delta))


def main():
    print('=' * 70)
    print('访问触发生命周期增长 - Sigmoid 导数衰减测试')
    print('=' * 70)

    # ============================================================
    # 测试 1：短期记忆阶段（old_lc < 7天）
    # ============================================================
    print('\n[测试1] 短期记忆阶段（old_lc < 7天）')
    print('-' * 60)

    old_lc = 1 * 86400     # 1天
    interim_new_lc = 3 * 86400  # 目标：3天（delta = 2天）
    short_cap = 7 * 86400

    print(f'old_lc = {old_lc/86400:.1f}天')
    print(f'interim_new_lc = {interim_new_lc/86400:.1f}天 (delta = {(interim_new_lc-old_lc)/86400:.1f}天)')

    mid = short_cap / 2.0
    k = 2.0 / short_cap
    max_deriv = k / 4.0

    print(f'\nSigmoid 参数: mid={mid/86400:.1f}天, k={k:.6f}, max_deriv={max_deriv:.6f}')
    print(f'scale @ old_lc = {sigmoid_derivative_with_floor(old_lc, mid, k, 0.1):.4f}')

    # 测试不同 old_lc 的 scale
    print('\n不同 old_lc 的 scale:')
    for t_days in [0.5, 1, 2, 3, 4, 5, 6, 7]:
        t = t_days * 86400
        scale = sigmoid_derivative_with_floor(t, mid, k, 0.1)
        deriv = sigmoid_derivative(t, mid, k)
        print(f'  t={t_days}天: scale={scale:.4f} (原 deriv={deriv:.6f}, max={max_deriv:.6f})')

    new_lc = apply_access_decay(old_lc, interim_new_lc)
    print(f'\n最终结果: new_lc = {new_lc/86400:.2f}天')

    # ============================================================
    # 测试 2：长期记忆阶段（7天 <= old_lc < 30天）
    # ============================================================
    print('\n\n[测试2] 长期记忆阶段（7天 <= old_lc < 30天）')
    print('-' * 60)

    old_lc = 15 * 86400     # 15天
    interim_new_lc = 20 * 86400  # 目标：20天（delta = 5天）
    short_cap = 7 * 86400
    long_cap = 30 * 86400

    print(f'old_lc = {old_lc/86400:.1f}天')
    print(f'interim_new_lc = {interim_new_lc/86400:.1f}天 (delta = {(interim_new_lc-old_lc)/86400:.1f}天)')

    mid = (short_cap + long_cap) / 2.0
    k = 2.0 / (long_cap - short_cap)
    max_deriv = k / 4.0

    print(f'\nSigmoid 参数: mid={mid/86400:.1f}天, k={k:.6f}, max_deriv={max_deriv:.6f}')
    print(f'scale @ old_lc = {sigmoid_derivative_with_floor(old_lc, mid, k, 0.1):.4f}')

    # 测试不同 old_lc 的 scale
    print('\n不同 old_lc 的 scale:')
    for t_days in [7, 10, 15, 20, 25, 29]:
        t = t_days * 86400
        scale = sigmoid_derivative_with_floor(t, mid, k, 0.1)
        print(f'  t={t_days}天: scale={scale:.4f}')

    new_lc = apply_access_decay(old_lc, interim_new_lc)
    print(f'\n最终结果: new_lc = {new_lc/86400:.2f}天')

    # ============================================================
    # 测试 3：跃迁阶段（old_lc >= 365天）
    # ============================================================
    print('\n\n[测试3] 跃迁阶段（old_lc >= 365天）')
    print('-' * 60)

    old_lc = 365 * 86400     # 365天 = 跃迁临界值
    interim_new_lc = 400 * 86400  # 目标：400天

    print(f'old_lc = {old_lc/86400:.1f}天')
    print(f'interim_new_lc = {interim_new_lc/86400:.1f}天')

    new_lc = apply_access_decay(old_lc, interim_new_lc)
    print(f'\n最终结果: new_lc = {new_lc} (infinity = 999999)')
    assert new_lc == 999999, f'应该跃迁到 infinity，实际: {new_lc}'
    print('✓ 正确跃迁到 infinity')

    # ============================================================
    # 测试 4：增长曲线可视化
    # ============================================================
    print('\n\n[测试4] 短期记忆增长曲线（从 0.5天 出发）')
    print('-' * 60)
    print('old_lc(天) -> new_lc(天) [scale]')

    old_lc = 0.5 * 86400  # 0.5天

    for step in range(10):
        # 模拟每次访问：interim_new_lc 是相对于 old_lc 的目标值
        interim_new_lc = old_lc + (7*86400 - old_lc) * 0.3  # 每次向7天目标靠近30%
        new_lc = apply_access_decay(old_lc, interim_new_lc)
        mid = 3.5 * 86400
        k = 2.0 / (7 * 86400)
        scale = sigmoid_derivative_with_floor(old_lc, mid, k, 0.1)
        print(f'  {old_lc/86400:.2f}天 -> {new_lc/86400:.2f}天 [scale={scale:.3f}]')
        old_lc = new_lc
        if old_lc >= 7 * 86400:
            break

    print('\n' + '=' * 70)
    print('测试完成！')
    print('=' * 70)


if __name__ == '__main__':
    main()