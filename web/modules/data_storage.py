"""
数据存储模块
负责数据的存储和管理 - 支持按天分割、自动切换、合并功能
"""

import csv
import logging
import os
import shutil
import threading
from datetime import datetime, timedelta
from collections import deque
from typing import List, Optional
import traceback

from .data_model import WindData


logger = logging.getLogger(__name__)

# 中文列名列表（按顺序）
CHINESE_FIELDNAMES = ['时间', '端口', '风速', '风向', '温度', '气压', '湿度']


def _safe_port_name(port: str) -> str:
    """获取安全的端口名称（用于文件名）"""
    # 先替换冒号，再替换斜杠，最后处理COM前缀（只替换COM而非COM3的COM）
    safe = port.replace(':', '_').replace('/', '_')
    # 只在开头出现COM时替换，避免替换COM3中的COM
    if safe.startswith('COM'):
        safe = safe[3:]  # 移除COM前缀
    return safe if safe else 'unknown'


def _safe_float(value) -> Optional[float]:
    """CSV 字段安全转浮点，失败返回 None"""
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


class DataStorage:
    """数据存储类 - 支持按天分割的CSV文件存储"""

    def __init__(self, port: str, data_dir: str = 'wind_data'):
        self.port = port
        self.data_dir = data_dir
        self.data_buffer = deque(maxlen=10000)
        self.data_file = None
        self.data_writer = None
        self._file_lock = threading.Lock()
        self._current_date = None  # 跟踪当前日期，用于跨日期切换

        # 创建数据目录
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)

        # 初始化文件（按天分割）
        self._init_daily_file()

        logger.info(f"端口 {self.port} 数据存储初始化完成，数据目录: {self.data_dir}")

    def _get_safe_port_name(self, port: str) -> str:
        """获取安全的端口名称（用于文件名），委托模块级 _safe_port_name"""
        return _safe_port_name(port)

    def _get_daily_filename(self) -> str:
        """获取当天的数据文件名"""
        safe_port = self._get_safe_port_name(self.port)
        date_str = datetime.now().strftime('%Y%m%d')
        return f"wind_data_{safe_port}_{date_str}.csv"

    def _init_daily_file(self):
        """初始化当天数据文件"""
        try:
            today = datetime.now().strftime('%Y%m%d')

            # 检查是否需要切换文件（跨日期）
            if self._current_date == today and self.data_file is not None:
                return  # 同一天，无需切换

            # 关闭旧文件
            if self.data_file is not None:
                try:
                    self.data_file.close()
                except Exception:
                    pass

            # 创建新文件
            self.data_filename = os.path.join(self.data_dir, self._get_daily_filename())
            self._current_date = today

            logger.info(f"初始化端口 {self.port} 当日数据文件: {self.data_filename}")

            # UTF-8 BOM - 让 Excel 正确识别 UTF-8 编码
            import codecs
            self.data_file = codecs.open(self.data_filename, 'a', encoding='utf-8-sig')

            # 如果是新文件（空文件），写入中文表头
            if os.path.getsize(self.data_filename) == 0:
                self.data_writer = csv.DictWriter(self.data_file, fieldnames=CHINESE_FIELDNAMES)
                self.data_writer.writeheader()
                self.data_file.flush()
            else:
                # P2-8: 追加模式，但先校验首行是否为表头。
                # 崩溃/os._exit 可能留下"文件非空但表头缺失"的窗口；
                # 若首行不是表头，则读取剩余数据重建文件（写表头 + 原数据）。
                # 注意：此路径只在跨日首次打开时触发，代价可接受。
                with codecs.open(self.data_filename, 'r', encoding='utf-8-sig') as _probe:
                    first_line = _probe.readline()
                header_text = ','.join(CHINESE_FIELDNAMES)
                if first_line.strip() != header_text:
                    logger.warning(f"端口 {self.port} 数据文件 {self.data_filename} 缺少表头，正在重建")
                    try:
                        with codecs.open(self.data_filename, 'r', encoding='utf-8-sig') as _old:
                            old_content = _old.read()
                        # 重建：先写表头，再写原有数据
                        with codecs.open(self.data_filename, 'w', encoding='utf-8-sig') as _rebuild:
                            _rebuild.write(header_text + '\n')
                            _rebuild.write(old_content)
                        # 重新以追加模式打开
                        self.data_file.close()
                        self.data_file = codecs.open(self.data_filename, 'a', encoding='utf-8-sig')
                    except Exception as e:
                        logger.error(f"端口 {self.port} 重建数据文件表头失败: {e}")
                        logger.error(traceback.format_exc())

                self.data_writer = csv.DictWriter(self.data_file, fieldnames=CHINESE_FIELDNAMES)

            logger.info(f"端口 {self.port} 数据文件初始化完成: {self.data_filename}")

        except PermissionError as e:
            logger.error(f"权限错误，无法创建数据文件: {e}")
            logger.error(traceback.format_exc())
        except FileNotFoundError as e:
            logger.error(f"目录不存在: {e}")
            logger.error(traceback.format_exc())
        except Exception as e:
            logger.error(f"初始化数据文件失败: {e}")
            logger.error(traceback.format_exc())

    def _check_date_change(self):
        """检查是否跨日期，自动切换文件"""
        today = datetime.now().strftime('%Y%m%d')
        if self._current_date != today:
            logger.info(f"日期变更: {self._current_date} -> {today}，切换数据文件")
            self._init_daily_file()

    def write_data_to_file(self, data: WindData):
        """将数据写入文件（自动处理跨日期切换）"""
        try:
            with self._file_lock:
                # 检查是否需要跨日期切换
                self._check_date_change()

                if self.data_file is not None and self.data_writer is not None:
                    # 将英文键名转换为中文键名
                    row = data.to_dict()
                    chinese_row = {
                        '时间': row.get('timestamp', ''),
                        '端口': row.get('port', ''),
                        '风速': row.get('wind_speed', ''),
                        '风向': row.get('wind_direction', ''),
                        '温度': row.get('temperature', ''),
                        '气压': row.get('pressure', ''),
                        '湿度': row.get('humidity', '')
                    }
                    self.data_writer.writerow(chinese_row)
                    self.data_file.flush()
                    logger.debug(f"端口 {self.port} 数据已写入: {chinese_row}")
                else:
                    logger.warning(f"端口 {self.port} 数据文件未初始化")
        except PermissionError as e:
            logger.error(f"权限错误，无法写入数据: {e}")
            logger.error(traceback.format_exc())
        except Exception as e:
            logger.error(f"写入数据失败: {e}")
            logger.error(traceback.format_exc())

    def save_and_close_data_file(self):
        """关闭数据文件"""
        try:
            with self._file_lock:
                logger.info(f"关闭端口 {self.port} 的数据文件")
                if self.data_file:
                    self.data_file.close()
                    self.data_file = None
                    self.data_writer = None
                    logger.info(f"数据文件已关闭")
        except Exception as e:
            logger.error(f"关闭数据文件出错: {e}")
            logger.error(traceback.format_exc())

    def append_data(self, data: WindData):
        """添加数据到缓冲区"""
        try:
            self.data_buffer.append(data)
            logger.debug(f"端口 {self.port} 数据已添加到缓冲区，当前大小: {len(self.data_buffer)}")
        except Exception as e:
            logger.error(f"添加数据到缓冲区失败: {e}")
            logger.error(traceback.format_exc())

    def get_latest_data(self) -> Optional[WindData]:
        """获取最新数据"""
        try:
            if self.data_buffer:
                return self.data_buffer[-1]
            return None
        except Exception as e:
            logger.error(f"获取最新数据失败: {e}")
            return None

    def get_all_data(self) -> List[WindData]:
        """获取所有数据"""
        try:
            return list(self.data_buffer)
        except Exception as e:
            logger.error(f"获取所有数据失败: {e}")
            return []

    def save_data_to_csv(self, filename: str):
        """保存全部数据到新的CSV文件"""
        try:
            with self._file_lock:
                if self.data_file:
                    self.data_file.flush()

                # 复制当前文件
                if os.path.exists(self.data_filename):
                    shutil.copy2(self.data_filename, filename)
                    logger.info(f"数据已保存到: {filename}")
                else:
                    logger.warning(f"数据文件不存在: {self.data_filename}")
        except Exception as e:
            logger.error(f"保存数据失败: {e}")
            logger.error(traceback.format_exc())

    def get_daily_files(self) -> List[str]:
        """获取所有按天分割的数据文件"""
        try:
            import glob
            safe_port = self._get_safe_port_name(self.port)
            pattern = os.path.join(self.data_dir, f"wind_data_{safe_port}_*.csv")
            files = glob.glob(pattern)
            files.sort()
            return files
        except Exception as e:
            logger.error(f"获取数据文件列表失败: {e}")
            return []

    def merge_daily_files(self, output_file: str = None) -> str:
        """
        合并所有按天分割的CSV数据文件
        返回合并后的文件路径
        """
        try:
            daily_files = self.get_daily_files()

            if not daily_files:
                logger.warning(f"端口 {self.port} 没有数据文件可合并")
                return ""

            if output_file is None:
                safe_port = self._get_safe_port_name(self.port)
                output_file = os.path.join(
                    self.data_dir,
                    f"merged_{safe_port}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
                )

            import codecs
            with codecs.open(output_file, 'w', encoding='utf-8-sig') as outfile:
                writer = csv.DictWriter(outfile, fieldnames=CHINESE_FIELDNAMES)
                writer.writeheader()

                for daily_file in daily_files:
                    try:
                        with open(daily_file, 'r', encoding='utf-8-sig') as infile:
                            reader = csv.DictReader(infile)
                            for row in reader:
                                writer.writerow(row)
                    except Exception as e:
                        logger.warning(f"读取文件失败 {daily_file}: {e}")

            logger.info(f"数据合并完成: {output_file}，合并了 {len(daily_files)} 个文件")
            return output_file

        except Exception as e:
            logger.error(f"合并数据文件失败: {e}")
            logger.error(traceback.format_exc())
            return ""


def merge_all_port_data(data_dir: str = 'wind_data', output_file: str = None) -> str:
    """
    合并所有端口的所有数据文件
    用于导出所有数据
    """
    try:
        import glob

        if not os.path.exists(data_dir):
            return ""

        # 获取所有 CSV 文件
        all_files = glob.glob(os.path.join(data_dir, "wind_data_*.csv"))

        if not all_files:
            return ""

        if output_file is None:
            output_file = os.path.join(
                data_dir,
                f"all_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            )

        import codecs
        with codecs.open(output_file, 'w', encoding='utf-8-sig') as outfile:
            writer = csv.DictWriter(outfile, fieldnames=CHINESE_FIELDNAMES)
            writer.writeheader()

            for csv_file in all_files:
                try:
                    with open(csv_file, 'r', encoding='utf-8-sig') as infile:
                        reader = csv.DictReader(infile)
                        for row in reader:
                            writer.writerow(row)
                except Exception as e:
                    logger.warning(f"读取文件失败 {csv_file}: {e}")

        logger.info(f"全量数据合并完成: {output_file}")
        return output_file

    except Exception as e:
        logger.error(f"合并全量数据失败: {e}")
        return ""


def read_history_data(port: str, minutes: int = 30, data_dir: str = 'wind_data',
                      limit: int = 8000) -> List[dict]:
    """读取指定端口近 minutes 分钟的历史数据（从按天分割的 CSV 文件）。

    返回按时间升序排列的 dict 列表，字段与 WindData.to_dict 一致（不含 port）。
    端口可能未连接，故为模块级函数，不依赖 reader/storage 实例。
    """
    try:
        import glob
        import codecs
        from datetime import timedelta

        safe = _safe_port_name(port)
        if not os.path.exists(data_dir):
            return []

        files = sorted(glob.glob(os.path.join(data_dir, f"wind_data_{safe}_*.csv")))

        now = datetime.now()
        cutoff = now - timedelta(minutes=minutes)

        # P2: 按文件名日期预过滤——只读 cutoff 日期当天及之后的文件，
        # 避免逐行扫描过期文件（文件名 wind_data_<safe>_YYYYMMDD.csv）
        cutoff_date = cutoff.strftime('%Y%m%d')
        files = [f for f in files
                 if os.path.basename(f).split('_')[-1].split('.')[0] >= cutoff_date]

        points = []
        for f in files:
            try:
                # utf-8-sig 自动剥离 BOM，与 merge 逻辑一致
                with codecs.open(f, 'r', encoding='utf-8-sig') as infile:
                    reader = csv.DictReader(infile)
                    for row in reader:
                        # 时间戳为当前写入格式 '%Y-%m-%d %H:%M:%S'（空格分隔、秒级）
                        ts_raw = (row.get('时间') or '').strip()
                        if not ts_raw:
                            continue
                        try:
                            ts = datetime.strptime(ts_raw, '%Y-%m-%d %H:%M:%S')
                        except ValueError:
                            continue  # 无法解析的时间戳行跳过
                        if ts < cutoff or ts > now:
                            continue
                        speed = _safe_float(row.get('风速'))
                        if speed is None:
                            continue
                        points.append({
                            'timestamp': ts.strftime('%Y-%m-%d %H:%M:%S'),
                            'wind_speed': speed,
                            'wind_direction': _safe_float(row.get('风向')),
                            'temperature': _safe_float(row.get('温度')),
                            'pressure': _safe_float(row.get('气压')),
                            'humidity': _safe_float(row.get('湿度')),
                        })
            except Exception as e:
                logger.warning(f"读取历史数据文件失败 {f}: {e}")

        # 升序返回（前端 dataPoints 按时间追加，最新在末尾）
        points.sort(key=lambda p: p['timestamp'])
        # P3: 防御性上限——超过 limit 只保留最新 limit 行（默认与前端 8000 点上限一致）
        return points[-limit:]
    except Exception as e:
        logger.error(f"读取历史数据失败: {e}")
        logger.error(traceback.format_exc())
        return []


def cleanup_old_data(data_dir: str = 'wind_data', keep_days: int = 30):
    """
    清理超过保留天数的旧数据文件。

    注意：合并产物（merged_*.csv / all_data_*.csv）不匹配 wind_data_* 命名，
    刻意不纳入自动清理（数量少、由用户主动生成），如需删除请手动处理。
    """
    try:
        import glob
        from datetime import timedelta

        if not os.path.exists(data_dir):
            return

        cutoff_date = datetime.now() - timedelta(days=keep_days)

        # 查找所有 wind_data_*.csv 文件
        pattern = os.path.join(data_dir, "wind_data_*.csv")

        for file_path in glob.glob(pattern):
            try:
                basename = os.path.basename(file_path)
                # 优先按文件名日期判断（wind_data_<port>_YYYYMMDD.csv），
                # 保持"按数据归属日期保留"语义：断连端口的文件按其数据日期起算
                file_date = None
                parts = basename.replace('wind_data_', '').split('_')
                if len(parts) >= 2:
                    date_str = parts[-1].replace('.csv', '')
                    if len(date_str) == 8 and date_str.isdigit():
                        try:
                            file_date = datetime.strptime(date_str, '%Y%m%d')
                        except ValueError:
                            file_date = None

                if file_date is None:
                    # P3-4: 文件名日期无法解析（手工保存 wind_data_COM3_名字_时间戳.csv 等）
                    # 回退按文件 mtime 判断，避免这类文件永不清理
                    mtime = datetime.fromtimestamp(os.path.getmtime(file_path))
                    if mtime < cutoff_date:
                        os.remove(file_path)
                        logger.info(f"已删除过期数据文件(按mtime): {file_path}")
                else:
                    if file_date < cutoff_date:
                        os.remove(file_path)
                        logger.info(f"已删除过期数据文件: {file_path}")
            except Exception:
                pass

    except Exception as e:
        logger.error(f"清理旧数据文件失败: {e}")