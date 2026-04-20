"""
相对时间过滤器 - MQL 时间条件解析

为 Interpreter 增加相对时间解析能力，支持：
- WHERE time > last_7_days
- WHERE time < last_month
- WHERE time >= last_3_hours
- WHERE time between last_7_days and last_1_day

相对时间映射表：
- last_5_minutes: 5 分钟
- last_15_minutes: 15 分钟
- last_1_hour: 1 小时
- last_3_hours: 3 小时
- last_24_hours: 24 小时
- last_7_days: 7 天
- last_30_days: 30 天
- last_3_months: 90 天
- last_1_year: 365 天
"""

from datetime import datetime
from typing import Optional, Union

import pendulum

# ============================================================================
# 相对时间映射表 (单位：秒)
# ============================================================================

RELATIVE_TIME_MAP = {
    "last_5_minutes": 5 * 60,
    "last_15_minutes": 15 * 60,
    "last_1_hour": 3600,
    "last_3_hours": 3 * 3600,
    "last_24_hours": 24 * 3600,
    "last_7_days": 7 * 86400,
    "last_30_days": 30 * 86400,
    "last_3_months": 90 * 86400,  # 近似 3 个月
    "last_1_year": 365 * 86400,
}

# 额外的常见相对时间别名
RELATIVE_TIME_ALIASES = {
    "last_minute": 60,
    "last_hour": 3600,
    "last_3_hours": 3 * 3600,
    "last_6_hours": 6 * 3600,
    "last_12_hours": 12 * 3600,
    "last_day": 86400,
    "last_week": 7 * 86400,
    "last_month": 30 * 86400,
    "last_3_months": 90 * 86400,
    "last_6_months": 180 * 86400,
    "last_year": 365 * 86400,
}

# 合并映射表
RELATIVE_TIME_MAP.update(RELATIVE_TIME_ALIASES)


# ============================================================================
# 时间过滤 MixIn
# ============================================================================

class TimeFilterMixIn:
    """
    为 Interpreter 增加相对时间解析能力

    使用方式：
        class MyInterpreter(Interpreter, TimeFilterMixIn):
            pass

        interpreter = MyInterpreter()
        interpreter.bind("mem", memory_system)
        result = interpreter.execute("SELECT * FROM memories WHERE time > last_7_days")
    """

    def _parse_time_condition(self, time_str: str) -> Optional[datetime]:
        """
        解析时间条件，支持相对时间

        Args:
            time_str: 时间字符串，可以是：
                - 相对时间关键字（如 "last_7_days"）
                - ISO 格式时间字符串
                - 其他可解析的时间格式

        Returns:
            datetime 对象，解析失败返回 None

        示例：
            >>> _parse_time_condition("last_7_days")
            datetime(2026, 4, 12, 21, 46)  # 假设当前是 4/19

            >>> _parse_time_condition("2026-04-01T00:00:00")
            datetime(2026, 4, 1, 0, 0, 0)
        """
        if not time_str:
            return None

        # 标准化输入（小写，去空格）
        normalized = time_str.strip().lower()

        # 检查是否是相对时间关键字
        if normalized in RELATIVE_TIME_MAP:
            seconds = RELATIVE_TIME_MAP[normalized]
            return pendulum.now().subtract(seconds=seconds)

        # 检查是否可以直接转换为相对时间（处理末尾的 _ 等情况）
        # 例如 "last_7_days" -> 检查 "last_7_days" in map

        # 尝试 ISO 格式解析
        try:
            return pendulum.parse(time_str)
        except Exception:
            pass

        # 尝试标准格式解析
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d",
            "%d-%m-%Y %H:%M:%S",
            "%d-%m-%Y",
            "%m-%d-%Y %H:%M:%S",
            "%m-%d-%Y",
        ]

        for fmt in formats:
            try:
                return datetime.strptime(time_str, fmt)
            except ValueError:
                continue

        return None

    def _is_relative_time(self, time_str: str) -> bool:
        """
        检查是否是相对时间关键字

        Args:
            time_str: 时间字符串

        Returns:
            bool
        """
        if not time_str:
            return False
        normalized = time_str.strip().lower()
        return normalized in RELATIVE_TIME_MAP

    def _get_relative_time_seconds(self, time_str: str) -> Optional[int]:
        """
        获取相对时间对应的秒数

        Args:
            time_str: 相对时间关键字

        Returns:
            秒数，解析失败返回 None
        """
        if not time_str:
            return None
        normalized = time_str.strip().lower()
        return RELATIVE_TIME_MAP.get(normalized)

    def _evaluate_time_condition(
        self,
        field_value: Union[str, datetime],
        operator: str,
        time_str: str
    ) -> bool:
        """
        评估时间条件，支持相对时间

        Args:
            field_value: 记忆中的时间字段值（字符串或 datetime）
            operator: 操作符（>, <, >=, <=, =）
            time_str: 条件中的时间字符串（可能是相对时间）

        Returns:
            bool

        示例：
            >>> _evaluate_time_condition("2026-04-15T10:00:00", ">", "last_7_days")
            True  # 如果当前是 4/19，last_7_days 是 4/12，15号 > 12号
        """
        # 解析条件中的时间
        condition_time = self._parse_time_condition(time_str)
        if condition_time is None:
            return False

        # 解析字段值
        if isinstance(field_value, datetime):
            field_dt = field_value
        elif isinstance(field_value, str):
            try:
                field_dt = pendulum.parse(field_value)
            except Exception:
                return False
        else:
            return False

        # 比较
        if operator == ">":
            return field_dt > condition_time
        elif operator == "<":
            return field_dt < condition_time
        elif operator == ">=":
            return field_dt >= condition_time
        elif operator == "<=":
            return field_dt <= condition_time
        elif operator == "=" or operator == "==":
            return field_dt == condition_time

        return False

    def _evaluate_between_time_condition(
        self,
        field_value: Union[str, datetime],
        time_start: str,
        time_end: str
    ) -> bool:
        """
        评估 BETWEEN 时间条件

        Args:
            field_value: 记忆中的时间字段值
            time_start: 起始时间（可以是相对时间）
            time_end: 结束时间（可以是相对时间）

        Returns:
            bool

        示例：
            >>> _evaluate_between_time_condition("2026-04-15T10:00:00", "last_7_days", "last_1_day")
            True  # 如果当前是 4/19，范围是 4/12 ~ 4/18，15号在范围内
        """
        # 解析起始和结束时间
        start_dt = self._parse_time_condition(time_start)
        end_dt = self._parse_time_condition(time_end)

        if start_dt is None or end_dt is None:
            return False

        # 解析字段值
        if isinstance(field_value, datetime):
            field_dt = field_value
        elif isinstance(field_value, str):
            try:
                field_dt = pendulum.parse(field_value)
            except Exception:
                return False
        else:
            return False

        # 确保 start <= end（如果用户给的顺序相反，交换它们）
        if start_dt > end_dt:
            start_dt, end_dt = end_dt, start_dt

        return start_dt <= field_dt <= end_dt


# ============================================================================
# 便捷函数
# ============================================================================

def parse_relative_time(time_str: str) -> Optional[datetime]:
    """
    便捷函数：解析相对时间或绝对时间

    Args:
        time_str: 时间字符串

    Returns:
        datetime 对象，解析失败返回 None
    """
    if time_str in RELATIVE_TIME_MAP:
        seconds = RELATIVE_TIME_MAP[time_str]
        return pendulum.now().subtract(seconds=seconds)
    try:
        return pendulum.parse(time_str)
    except Exception:
        return None


def get_relative_time_seconds(time_str: str) -> Optional[int]:
    """
    便捷函数：获取相对时间对应的秒数

    Args:
        time_str: 相对时间关键字

    Returns:
        秒数，不存在返回 None
    """
    return RELATIVE_TIME_MAP.get(time_str.strip().lower())


# ============================================================================
# 导出
# ============================================================================

__all__ = [
    "RELATIVE_TIME_MAP",
    "TimeFilterMixIn",
    "parse_relative_time",
    "get_relative_time_seconds",
]