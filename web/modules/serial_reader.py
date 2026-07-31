"""
串口数据读取器模块
负责管理单个串口的数据读取流程
"""

import serial
import threading
import time
import logging
from typing import Dict, List, Optional
import traceback

from .serial_communicator import SerialCommunicator
from .data_parser import DataParser
from .data_storage import DataStorage
from .data_model import WindData


logger = logging.getLogger(__name__)

# 需要发送的十六进制命令
HEX_COMMAND = bytes([0x30, 0x30, 0x54, 0x52, 0x30, 0x30, 0x30, 0x30, 0x30, 0x0D])


class SerialWindDataReader:
    def __init__(self, port: str, baudrate: int = 9600, bytesize: int = 8, 
                 parity: str = 'N', stopbits: int = 1, on_data_callback=None):
        self.port = port
        self.baudrate = baudrate
        self.bytesize = bytesize  # 数据位
        self.parity = parity      # 校验位
        self.stopbits = stopbits  # 停止位
        self.communicator = SerialCommunicator(port, baudrate, bytesize, parity, stopbits)
        self.parser = DataParser(port)
        self.storage = DataStorage(port)
        self.is_running = False
        self.lock = threading.RLock()  # 使用可重入锁
        self.reconnect_delay = 5  # 重连延迟(秒)
        self.command_interval = 1  # 命令发送间隔(秒)，改为每秒发送一次
        self.last_command_time = 0  # 上次发送命令的时间
        self.custom_name = ""  # 端口自定义名称
        self.command_timer_thread = None  # 命令发送定时器线程
        self.on_data_callback = on_data_callback  # 数据回调函数
        self.data_buffer = ""  # 帧缓冲区，用于拼接跨次读取的#...#帧数据
        self._buffer_lock = threading.Lock()  # 帧缓冲区线程安全锁
        logger.info(f"串口数据读取器初始化完成，端口: {self.port}, 波特率: {self.baudrate}, 数据位: {self.bytesize}, 校验位: {self.parity}, 停止位: {self.stopbits}")
        
    def connect(self) -> bool:
        """连接串口"""
        logger.info(f"开始连接端口 {self.port}")
        result = self.communicator.connect()
        if result:
            logger.info(f"端口 {self.port} 连接成功")
        else:
            logger.error(f"端口 {self.port} 连接失败")
        return result
    
    def disconnect(self):
        """断开串口连接"""
        logger.info(f"开始断开端口 {self.port} 连接")
        self.is_running = False
        # 等待命令定时器线程结束
        if self.command_timer_thread and self.command_timer_thread.is_alive():
            logger.info(f"等待端口 {self.port} 命令定时器线程结束")
            self.command_timer_thread.join(timeout=2)  # 等待最多2秒
        # 保存剩余数据并关闭文件
        self.storage.save_and_close_data_file()
        self.communicator.disconnect()
        logger.info(f"端口 {self.port} 连接已断开")
    
    def _initialize_data_file(self):
        """初始化数据文件"""
        logger.info(f"初始化端口 {self.port} 数据文件")
        self.storage._init_daily_file()
    
    def _write_data_to_file(self, data):
        """将数据写入文件"""
        self.storage.write_data_to_file(data)
    
    def _save_and_close_data_file(self):
        """保存缓冲区中剩余数据并关闭文件"""
        self.storage.save_and_close_data_file()

    def send_command(self, command: bytes = HEX_COMMAND) -> bool:
        """向串口发送命令"""
        logger.info(f"向端口 {self.port} 发送命令")
        result = self.communicator.send_command(command)
        if result:
            logger.info(f"命令成功发送到端口 {self.port}")
            self.last_command_time = time.time()  # 更新最后发送时间
        else:
            logger.error(f"命令发送到端口 {self.port} 失败")
        return result
    
    def _command_timer_loop(self):
        """命令发送定时器循环 - 独立线程确保连续发送命令"""
        logger.info(f"端口 {self.port} 命令发送定时器循环开始")
        while self.is_running:
            try:
                current_time = time.time()
                # 检查是否到了发送命令的时间
                if current_time - self.last_command_time >= self.command_interval:
                    # 检查串口是否连接且可用
                    if self.communicator.get_port_status():
                        logger.debug(f"端口 {self.port} 定时发送命令")
                        self.send_command()
                    else:
                        logger.debug(f"端口 {self.port} 未连接，跳过本次命令发送")
                
                # 短暂休眠以减少CPU占用
                time.sleep(0.1)
                
            except Exception as e:
                logger.error(f"端口 {self.port} 命令定时器循环出现异常: {e}")
                logger.error(f"详细错误信息: {traceback.format_exc()}")
                time.sleep(self.command_interval)
        
        logger.info(f"端口 {self.port} 命令发送定时器循环结束")
    
    def start_reading(self):
        """开始读取数据"""
        logger.info(f"开始读取端口 {self.port} 数据")
        # 初始化数据文件
        self._initialize_data_file()
        
        # 启动读取和重连线程
        self.is_running = True
        
        # 先启动重连线程（负责首次连接及后续重连）
        reconnect_thread = threading.Thread(target=self._reconnect_loop, daemon=True, name=f"ReconnectThread-{self.port}")
        reconnect_thread.start()
        logger.info(f"端口 {self.port} 重连线程已启动")
        
        # 启动命令发送定时器线程（独立于数据读取）
        self.command_timer_thread = threading.Thread(target=self._command_timer_loop, daemon=True, name=f"CommandTimerThread-{self.port}")
        self.command_timer_thread.start()
        logger.info(f"端口 {self.port} 命令发送定时器线程已启动")
    
    def _reconnect_loop(self):
        """重连循环"""
        logger.info(f"端口 {self.port} 重连循环开始")
        read_thread = None
        first_attempt = True  # 首次进入时立即尝试连接，不等待
        while self.is_running:
            try:
                # 检查是否已经连接
                if not self.communicator.get_port_status():
                    logger.info(f"端口 {self.port} 未连接，尝试重连")
                    if self.connect():
                        # 连接成功，发送初始化命令
                        self.send_command()
                        # 启动读取线程 - P1-1: 先等待旧线程退出再启动新线程，避免多线程竞争
                        if read_thread is not None and read_thread.is_alive():
                            logger.warning(f"端口 {self.port} 检测到旧的读取线程仍在运行，等待其退出")
                            read_thread.join(timeout=3)
                        read_thread = threading.Thread(target=self._read_loop, daemon=True, name=f"ReadThread-{self.port}")
                        read_thread.start()
                        logger.info(f"端口 {self.port} 读取线程已启动")
                    else:
                        logger.info(f"端口 {self.port} 连接失败，{self.reconnect_delay}秒后重试")
                if first_attempt:
                    first_attempt = False
                    time.sleep(0)  # 首次尝试不等待，立即进入下一轮
                else:
                    time.sleep(self.reconnect_delay)
            except Exception as e:
                logger.error(f"端口 {self.port} 重连循环出现异常: {e}")
                logger.error(f"详细错误信息: {traceback.format_exc()}")
                time.sleep(self.reconnect_delay)
        logger.info(f"端口 {self.port} 重连循环结束")
    
    def _read_loop(self):
        """数据读取循环"""
        logger.info(f"端口 {self.port} 数据读取循环开始")
        while self.is_running:
            try:
                should_sleep_longer = True
                # 使用 is_connected() 而非直接访问 serial_conn，确保锁保护
                if self.communicator.is_connected():
                    raw_data = self.communicator.read_line()
                    if raw_data:
                        logger.debug(f"端口 {self.port} 读取到原始数据: {raw_data}")
                        # 将读取到的数据追加到帧缓冲区（加锁保护）
                        with self._buffer_lock:
                            self.data_buffer += raw_data
                            # 从缓冲区中提取完整的 #...# 帧
                            while '#' in self.data_buffer:
                                start_idx = self.data_buffer.find('#')
                                # 查找起始#之后的下一个#作为帧结束
                                end_idx = self.data_buffer.find('#', start_idx + 1)
                                if end_idx == -1:
                                    # 没有找到帧结束标记，数据不完整，等待更多数据
                                    logger.debug(f"端口 {self.port} 帧数据不完整，等待更多数据，缓冲区: {self.data_buffer}")
                                    break
                                # 提取完整帧
                                frame = self.data_buffer[start_idx:end_idx + 1]
                                # P1-8: 检查帧内是否包含多余的#号，可能格式异常
                                frame_content = frame[1:-1] if len(frame) > 2 else frame
                                if frame_content.count('#') > 0:
                                    logger.warning(f"端口 {self.port} 帧内包含多余的#号，可能格式异常: {frame}")
                                # 从缓冲区移除已处理的数据
                                self.data_buffer = self.data_buffer[end_idx + 1:]
                                logger.debug(f"端口 {self.port} 提取完整帧: {frame}")
                                parsed_data = self._parse_data(frame)
                                if parsed_data:
                                    # 将解析后的WindData对象存储
                                    wind_data_obj = WindData.from_dict(parsed_data)
                                    self.storage.append_data(wind_data_obj)
                                    # 实时将数据写入文件
                                    self.storage.write_data_to_file(wind_data_obj)
                                    # 通过回调推送数据
                                    if self.on_data_callback:
                                        self.on_data_callback(self.port, wind_data_obj)
                                    else:
                                        logger.warning(f"端口 {self.port} 无数据回调，无法推送数据")
                                    should_sleep_longer = False
                                    logger.info(f"端口 {self.port} 数据处理完成: {parsed_data}")
                                else:
                                    logger.warning(f"端口 {self.port} 数据解析失败: {frame}")
                            # P0-3: 缓冲区超长保护 - 防止起始#但无结束#时无限增长
                            if len(self.data_buffer) > 4096:
                                if '#' not in self.data_buffer:
                                    # 完全没有帧标记，整体清空
                                    logger.warning(f"端口 {self.port} 缓冲区数据过长且无帧标记，清空缓冲区: {self.data_buffer[:100]}...")
                                    self.data_buffer = ""
                                else:
                                    # 有起始#但无结束#，丢弃到第一个#位置
                                    first_hash = self.data_buffer.find('#')
                                    if first_hash > 0:
                                        self.data_buffer = self.data_buffer[first_hash:]
                                        logger.warning(f"端口 {self.port} 缓冲区超长，截断到第一个#号")
                                    else:
                                        # first_hash == 0 但缓冲区超长，说明有#但无结束#，
                                        # 截断到缓冲区一半的位置，保留后半段继续等待
                                        half = len(self.data_buffer) // 2
                                        self.data_buffer = self.data_buffer[half:]
                                        logger.warning(f"端口 {self.port} 缓冲区超长（有起始#但无结束#），截断到一半")
                                
                # 根据是否有数据来决定休眠时间
                if should_sleep_longer:
                    time.sleep(1)  # 如果没有数据或未连接，等待更长时间
                else:
                    time.sleep(0.01)  # 短暂休眠避免CPU占用过高
            except serial.SerialException as e:
                logger.error(f"串口 {self.port} 通信错误: {e}")
                logger.error(f"详细错误信息: {traceback.format_exc()}")
                time.sleep(self.reconnect_delay)
            except Exception as e:
                logger.error(f"读取端口 {self.port} 数据时出错: {e}")
                logger.error(f"详细错误信息: {traceback.format_exc()}")
                time.sleep(self.reconnect_delay)
        logger.info(f"端口 {self.port} 数据读取循环结束")
    
    def _is_number(self, s):
        """检查字符串是否为有效数字"""
        if not s:
            return False
        try:
            float(s)
            return True
        except ValueError:
            return False
    
    def _safe_float_conversion(self, value_str):
        """安全地将字符串转换为浮点数"""
        if not value_str:
            return None
        # 去除首尾空格
        value_str = value_str.strip()
        # 检查是否为有效数字
        if self._is_number(value_str):
            return float(value_str)

    def _parse_data(self, raw_data: str) -> Optional[Dict]:
        """解析数据"""
        try:
            parsed_data = self.parser.parse_data(raw_data)
            if parsed_data:
                logger.debug(f"端口 {self.port} 数据解析成功: {parsed_data.to_dict()}")
                return parsed_data.to_dict()
            else:
                logger.warning(f"端口 {self.port} 数据解析失败: {raw_data}")
                return None
        except Exception as e:
            logger.error(f"端口 {self.port} 解析数据时发生异常: {e}")
            logger.error(f"原始数据: {raw_data}")
            logger.error(f"详细错误信息: {traceback.format_exc()}")
            return None
    
    def get_latest_data(self) -> Optional[Dict]:
        """获取最新数据"""
        try:
            latest_data = self.storage.get_latest_data()
            if latest_data:
                logger.debug(f"端口 {self.port} 获取最新数据成功: {latest_data.to_dict()}")
                return latest_data.to_dict()
            else:
                logger.debug(f"端口 {self.port} 无最新数据")
                return None
        except Exception as e:
            logger.error(f"获取端口 {self.port} 最新数据时发生异常: {e}")
            logger.error(f"详细错误信息: {traceback.format_exc()}")
            return None
    
    def get_all_data(self) -> List[Dict]:
        """获取所有数据"""
        try:
            all_data = self.storage.get_all_data()
            logger.debug(f"端口 {self.port} 获取所有数据成功，总数: {len(all_data)}")
            return [data.to_dict() for data in all_data]
        except Exception as e:
            logger.error(f"获取端口 {self.port} 所有数据时发生异常: {e}")
            logger.error(f"详细错误信息: {traceback.format_exc()}")
            return []
    
    def save_data_to_csv(self, filename: str):
        """保存数据到CSV文件"""
        logger.info(f"端口 {self.port} 保存数据到CSV文件: {filename}")
        self.storage.save_data_to_csv(filename)