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
        """获取安全的端口名称（用于文件名）"""
        # 先替换冒号，再替换斜杠，最后处理COM前缀（只替换COM而非COM3的COM）
        safe = port.replace(':', '_').replace('/', '_')
        # 只在开头出现COM时替换，避免替换COM3中的COM
        if safe.startswith('COM'):
            safe = safe[3:]  # 移除COM前缀
        return safe if safe else 'unknown'

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
                # 追加模式，重新创建 writer
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


def cleanup_old_data(data_dir: str = 'wind_data', keep_days: int = 30):
    """
    清理超过保留天数的旧数据文件
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
                # 提取日期: wind_data_COM3_20250614.csv
                parts = basename.replace('wind_data_', '').split('_')
                if len(parts) >= 2:
                    date_str = parts[-1].replace('.csv', '')
                    if len(date_str) == 8 and date_str.isdigit():
                        file_date = datetime.strptime(date_str, '%Y%m%d')
                        if file_date < cutoff_date:
                            os.remove(file_path)
                            logger.info(f"已删除过期数据文件: {file_path}")
            except Exception:
                pass

    except Exception as e:
        logger.error(f"清理旧数据文件失败: {e}")