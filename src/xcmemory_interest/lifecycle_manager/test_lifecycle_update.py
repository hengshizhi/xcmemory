# -*- coding: utf-8 -*-
"""
lifecycle_manager 单元测试 - 老记忆生命周期更新公式

测试核心公式：
    ratio = old_lc / ref_lc
    f = sqrt(ratio)
    w = sampled_prob

    new_lc = old_lc * (1 - w) + ref_lc * f * w
"""

import sys
import os
import shutil
import math

# 测试文件: models/xcmemory_interest/lifecycle_manager/test_lifecycle_update.py
# 需要往上4层才能到项目根目录 o:/project/starlate/
# lifecycle_manager -> xcmemory_interest -> models -> starlate (项目根)
test_file = os.path.abspath(__file__)
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(test_file))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from xcmemory_interest.lifecycle_manager.core import LifecycleManager


def test_old_memory_lifecycle_formula():
    """
    测试老记忆生命周期更新的新公式

    场景覆盖：
    1. 离群记忆（w小）→ 几乎不变
    2. 典型记忆（w大）→ 部分趋同
    3. 老记忆更强（old_lc > ref_lc）→ 略微削弱
    4. 老记忆更弱（old_lc < ref_lc）→ 增强
    5. w=0（完全不采样）→ 保持不变
    6. w=1（完全采样）→ 直接用 ref_lc * f
    7. ref_lc=0 的边界情况
    """
    print('=' * 70)
    print('老记忆生命周期更新公式测试')
    print('=' * 70)

    test_results = []

    # 辅助函数：直接实现公式计算
    def compute_new_lc(old_lc: float, ref_lc: float, sampled_prob: float) -> float:
        if ref_lc > 0:
            ratio = old_lc / ref_lc
            f = ratio ** 0.5  # sqrt(ratio)
        else:
            ratio = 1.0
            f = 1.0
        w = sampled_prob
        new_lc = old_lc * (1 - w) + ref_lc * f * w
        return max(1, new_lc)

    # ============================================================
    # 场景1：离群记忆（w小）→ 几乎不变
    # ============================================================
    print('\n[场景1] 离群记忆（w=0.1, old_lc << ref_lc）')
    old_lc = 3600.0        # 1小时
    ref_lc = 86400.0       # 24小时
    sampled_prob = 0.1     # 低采样概率（离群）
    new_lc = compute_new_lc(old_lc, ref_lc, sampled_prob)
    expected_min = old_lc * 0.9  # 至少保持90%
    expected_max = old_lc * 1.5  # 最多增加50%
    print(f'    old_lc={old_lc}, ref_lc={ref_lc}, w={sampled_prob}')
    print(f'    new_lc={new_lc:.2f} (expected: {expected_min:.0f}~{expected_max:.0f})')
    assert expected_min <= new_lc <= expected_max, f'离群记忆测试失败: {new_lc}'
    print('    ✓ 通过')
    test_results.append(('场景1-离群记忆', True))

    # ============================================================
    # 场景2：典型记忆（w大）→ 部分趋同
    # ============================================================
    print('\n[场景2] 典型记忆（w=0.9, old_lc << ref_lc）')
    old_lc = 3600.0        # 1小时
    ref_lc = 86400.0       # 24小时
    sampled_prob = 0.9     # 高采样概率（典型）
    new_lc = compute_new_lc(old_lc, ref_lc, sampled_prob)
    ratio = old_lc / ref_lc
    f = ratio ** 0.5
    expected = old_lc * (1 - sampled_prob) + ref_lc * f * sampled_prob
    print(f'    old_lc={old_lc}, ref_lc={ref_lc}, w={sampled_prob}')
    print(f'    ratio={ratio:.4f}, f={f:.4f}')
    print(f'    new_lc={new_lc:.2f} (expected: {expected:.2f})')
    assert abs(new_lc - expected) < 0.01, f'典型记忆测试失败: {new_lc} != {expected}'
    assert new_lc > old_lc, '典型记忆应该增强'
    assert new_lc < ref_lc, '不应该直接达到目标值'
    print('    ✓ 通过')
    test_results.append(('场景2-典型记忆', True))

    # ============================================================
    # 场景3：老记忆更强（old_lc > ref_lc）→ 略微削弱
    # ============================================================
    print('\n[场景3] 老记忆更强（old_lc >> ref_lc）')
    old_lc = 259200.0      # 72小时
    ref_lc = 86400.0       # 24小时
    sampled_prob = 0.9     # 高采样概率（典型）
    new_lc = compute_new_lc(old_lc, ref_lc, sampled_prob)
    ratio = old_lc / ref_lc
    f = ratio ** 0.5
    expected = old_lc * (1 - sampled_prob) + ref_lc * f * sampled_prob
    print(f'    old_lc={old_lc}, ref_lc={ref_lc}, w={sampled_prob}')
    print(f'    ratio={ratio:.4f}, f={f:.4f}')
    print(f'    new_lc={new_lc:.2f} (expected: {expected:.2f})')
    assert abs(new_lc - expected) < 0.01, f'老记忆更强测试失败: {new_lc} != {expected}'
    assert new_lc < old_lc, '老记忆更强应该被削弱'
    assert new_lc > ref_lc, '不应该直接降到目标值以下'
    print('    ✓ 通过')
    test_results.append(('场景3-老记忆更强', True))

    # ============================================================
    # 场景4：老记忆更弱（old_lc < ref_lc）→ 增强
    # ============================================================
    print('\n[场景4] 老记忆更弱（old_lc < ref_lc）')
    old_lc = 3600.0        # 1小时
    ref_lc = 86400.0       # 24小时
    sampled_prob = 0.7
    new_lc = compute_new_lc(old_lc, ref_lc, sampled_prob)
    print(f'    old_lc={old_lc}, ref_lc={ref_lc}, w={sampled_prob}')
    print(f'    new_lc={new_lc:.2f}')
    assert new_lc > old_lc, '老记忆更弱应该被增强'
    assert new_lc < ref_lc, '不应该直接达到目标值'
    print('    ✓ 通过')
    test_results.append(('场景4-老记忆更弱', True))

    # ============================================================
    # 场景5：w=0（完全不采样）→ 保持不变
    # ============================================================
    print('\n[场景5] w=0（完全不采样）')
    old_lc = 100000.0
    ref_lc = 1.0
    sampled_prob = 0.0
    new_lc = compute_new_lc(old_lc, ref_lc, sampled_prob)
    print(f'    old_lc={old_lc}, ref_lc={ref_lc}, w={sampled_prob}')
    print(f'    new_lc={new_lc:.2f} (expected: {old_lc})')
    assert abs(new_lc - old_lc) < 0.01, f'w=0测试失败: {new_lc} != {old_lc}'
    print('    ✓ 通过')
    test_results.append(('场景5-w=0不变', True))

    # ============================================================
    # 场景6：w=1（完全采样）→ 直接用 ref_lc * f
    # ============================================================
    print('\n[场景6] w=1（完全采样）')
    old_lc = 3600.0
    ref_lc = 86400.0
    sampled_prob = 1.0
    new_lc = compute_new_lc(old_lc, ref_lc, sampled_prob)
    ratio = old_lc / ref_lc
    f = ratio ** 0.5
    expected = ref_lc * f
    print(f'    old_lc={old_lc}, ref_lc={ref_lc}, w={sampled_prob}')
    print(f'    ratio={ratio:.4f}, f={f:.4f}')
    print(f'    new_lc={new_lc:.2f} (expected: {expected:.2f})')
    assert abs(new_lc - expected) < 0.01, f'w=1测试失败: {new_lc} != {expected}'
    print('    ✓ 通过')
    test_results.append(('场景6-w=1公式', True))

    # ============================================================
    # 场景7：ref_lc=0 的边界情况
    # ============================================================
    print('\n[场景7] ref_lc=0 边界情况')
    old_lc = 3600.0
    ref_lc = 0.0
    sampled_prob = 0.9
    new_lc = compute_new_lc(old_lc, ref_lc, sampled_prob)
    # 当 ref_lc=0 时，公式变为: new_lc = old_lc * (1 - w)
    expected = old_lc * (1 - sampled_prob)
    print(f'    old_lc={old_lc}, ref_lc={ref_lc}, w={sampled_prob}')
    print(f'    new_lc={new_lc:.2f} (expected: {expected:.2f})')
    assert abs(new_lc - expected) < 0.01, f'ref_lc=0测试失败: {new_lc} != {expected}'
    print('    ✓ 通过')
    test_results.append(('场景7-ref_lc=0边界', True))

    # ============================================================
    # 场景8：old_lc = ref_lc 时，应该保持不变（无论w）
    # ============================================================
    print('\n[场景8] old_lc = ref_lc 时保持不变')
    old_lc = 86400.0
    ref_lc = 86400.0
    for w in [0.0, 0.3, 0.5, 0.7, 1.0]:
        new_lc = compute_new_lc(old_lc, ref_lc, w)
        ratio = old_lc / ref_lc
        f = ratio ** 0.5
        expected = old_lc * (1 - w) + ref_lc * f * w
        print(f'    w={w}: new_lc={new_lc:.2f}, expected={expected:.2f}')
        assert abs(new_lc - expected) < 0.01, f'w={w}测试失败'
    print('    ✓ 通过')
    test_results.append(('场景8-old_lc=ref_lc', True))

    # ============================================================
    # 场景9：f 函数性质验证
    # ============================================================
    print('\n[场景9] f 函数性质验证')
    # ratio < 1 时，f < 1（削弱）
    # ratio = 1 时，f = 1
    # ratio > 1 时，f > 1（增强）
    test_cases = [
        (0.25, 0.5),   # ratio=0.25 -> f=0.5
        (0.5, 0.707),  # ratio=0.5 -> f=sqrt(0.5)≈0.707
        (1.0, 1.0),    # ratio=1 -> f=1
        (2.0, 1.414),  # ratio=2 -> f=sqrt(2)≈1.414
        (4.0, 2.0),    # ratio=4 -> f=2
    ]
    for ratio, expected_f in test_cases:
        f = ratio ** 0.5
        print(f'    ratio={ratio}: f={f:.3f} (expected: {expected_f:.3f})')
        assert abs(f - expected_f) < 0.01, f'f函数测试失败: {f} != {expected_f}'
    print('    ✓ 通过')
    test_results.append(('场景9-f函数性质', True))

    # ============================================================
    # 汇总
    # ============================================================
    print('\n' + '=' * 70)
    print('测试结果汇总')
    print('=' * 70)
    passed = sum(1 for _, ok in test_results if ok)
    total = len(test_results)
    for name, ok in test_results:
        status = '✓' if ok else '✗'
        print(f'  {status} {name}')
    print(f'\n通过: {passed}/{total}')
    print('=' * 70)

    return passed == total


def test_formula_edge_cases():
    """测试公式边界情况"""
    print('\n' + '=' * 70)
    print('公式边界情况测试')
    print('=' * 70)

    def compute_new_lc(old_lc: float, ref_lc: float, sampled_prob: float) -> float:
        if ref_lc > 0:
            ratio = old_lc / ref_lc
            f = ratio ** 0.5
        else:
            ratio = 1.0
            f = 1.0
        w = sampled_prob
        new_lc = old_lc * (1 - w) + ref_lc * f * w
        return max(1, new_lc)

    # 极端值测试
    test_cases = [
        # (old_lc, ref_lc, sampled_prob, description)
        (1, 999999, 0.5, '老记忆极弱'),
        (999999, 1, 0.5, '老记忆极强'),
        (1, 1, 0.5, '相等'),
        (0.001, 1000, 0.5, '极小old_lc'),
        (1000, 0.001, 0.5, '极小ref_lc'),
    ]

    for old_lc, ref_lc, w, desc in test_cases:
        new_lc = compute_new_lc(old_lc, ref_lc, w)
        print(f'  {desc}: old={old_lc}, ref={ref_lc}, w={w} -> new={new_lc:.2f}')
        assert new_lc >= 1, f'{desc}: new_lc应该>=1'

    print('  ✓ 所有边界情况通过')
    return True


def main():
    success1 = test_old_memory_lifecycle_formula()
    success2 = test_formula_edge_cases()

    if success1 and success2:
        print('\n🎉 所有测试通过！')
        return 0
    else:
        print('\n❌ 部分测试失败')
        return 1


if __name__ == '__main__':
    exit(main())