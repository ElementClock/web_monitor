"""
串口数据读取器模块
负责管理单个串口的数据读取流程
"""

import serial
import threading
import time
import logging
from datetime import datetime
from typing import Dict, List, Optional
import traceback

from .serial_communicator import SerialCommunicator
from .data_parser import DataParser
from .data_storage import DataStorage
from .data_model import WindData


logger = logging.getLogger(__name__)

# 需要发送的十六进制命令
HEX_COMMAND = bytes([0x30, 0x30, 0x54, 0x52, 0x30, 0x30, 0x30, 0x30, 0x30, 0x0D])

# 单帧最大长度（字节）：设备一帧约50字节，4000 远大于正常帧但可防止坏帧无限增长
MAX_FRAME_LENGTH = 4000
# 命令发送失败时的退避上限(秒)
MAX_COMMAND_BACKOFF = 10
# 重连失败时的退避上限(秒)
MAX_RECONNECT_BACKOFF = 30


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
        # P2-3: 统一停止信号：disconnect 置位后各线程的 wait() 立即返回
        self.stop_event = threading.Event()
        self._disconnected = False  # P2-13: disconnect 幂等守卫
        self.reconnect_delay = 5  # 重连延迟(秒)
        self.command_interval = 1  # 命令发送间隔(秒)，改为每秒发送一次
        self.last_command_time = 0  # 上次发送命令的时间
        self.custom_name = ""  # 端口自定义名称
        self.command_timer_thread = None  # 命令发送定时器线程
        self._read_thread = None  # 数据读取线程（单线程模型，不随重连重启）
        self._reconnect_thread = None  # 重连线程
        self.on_data_callback = on_data_callback  # 数据回调函数
        self.data_buffer = ""  # 帧缓冲区，用于拼接跨次读取的#...#帧数据
        self._buffer_lock = threading.Lock()  # 帧缓冲区线程安全锁
        # 鲁棒性统计（网络/串口拥塞分片场景）
        self.stats = {
            'read_chunks': 0,          # 累计读取的原始数据块数
            'reassembled_frames': 0,   # 跨多次读取重组成功的帧数
            'discarded_frames': 0,     # 丢弃的异常帧数
            'buffer_truncations': 0,   # 缓冲区超限截断次数
            'last_reassembly': None,   # 最近一次重组时间(ISO字符串)
        }
        self._stats_lock = threading.Lock()  # 统计字段线程安全锁
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
        """断开串口连接（幂等，可安全重复调用/与 remove_reader 并发）

        P2-3: 置位 stop_event 后按顺序 join 各线程，再关闭文件与串口。
        顺序：命令定时器 → 读取线程 → 重连线程。read 线程最慢 1s 一轮、
        command 线程最慢 0.1s 一轮、reconnect 可能正卡在 connect() 上，
        因此 reconnect 最后 join 且超时后继续收尾（daemon 兜底）。
        注意：不要在此把内存 deque 重写进文件——每帧解析时已实时落盘，
        重写会造成重复行；deque 仅服务内存查询。
        """
        if self._disconnected:
            logger.info(f"端口 {self.port} 已断开，忽略重复断开请求")
            return
        self._disconnected = True  # P2-13: 置位幂等守卫，防二次清理
        logger.info(f"开始断开端口 {self.port} 连接")
        self.is_running = False
        self.stop_event.set()

        # 等待命令定时器线程结束
        if self.command_timer_thread and self.command_timer_thread.is_alive():
            logger.info(f"等待端口 {self.port} 命令定时器线程结束")
            self.command_timer_thread.join(timeout=2)  # 等待最多2秒

        # 等待读取线程结束（确保最后几帧已写入文件，避免"写已关文件"丢帧）
        if self._read_thread and self._read_thread.is_alive():
            logger.info(f"等待端口 {self.port} 读取线程结束")
            self._read_thread.join(timeout=2)

        # 等待重连线程结束（可能卡在 connect()，超时后由 daemon 兜底）
        if self._reconnect_thread and self._reconnect_thread.is_alive():
            logger.info(f"等待端口 {self.port} 重连线程结束")
            self._reconnect_thread.join(timeout=2)

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
        """命令发送定时器循环 - 独立线程确保连续发送命令
        发送失败时按指数退避，避免网络拥塞时高频重试加剧堵塞
        """
        logger.info(f"端口 {self.port} 命令发送定时器循环开始")
        backoff = self.command_interval
        while self.is_running and not self.stop_event.is_set():
            try:
                current_time = time.time()
                # 检查是否到了发送命令的时间
                if current_time - self.last_command_time >= backoff:
                    # 检查串口是否连接且可用
                    if self.communicator.get_port_status():
                        logger.debug(f"端口 {self.port} 定时发送命令")
                        if self.send_command():
                            backoff = self.command_interval  # 成功，恢复正常间隔
                        else:
                            # 发送失败：指数退避，最多 MAX_COMMAND_BACKOFF 秒
                            backoff = min(backoff * 2, MAX_COMMAND_BACKOFF)
                            logger.warning(f"端口 {self.port} 命令发送失败，退避至 {backoff} 秒后重试")
                    else:
                        backoff = self.command_interval
                        logger.debug(f"端口 {self.port} 未连接，跳过本次命令发送")

                # P2-3: 用 stop_event.wait 替代 sleep，disconnect 时立即返回
                self.stop_event.wait(0.1)

            except Exception as e:
                logger.error(f"端口 {self.port} 命令定时器循环出现异常: {e}")
                logger.error(f"详细错误信息: {traceback.format_exc()}")
                self.stop_event.wait(self.command_interval)

        logger.info(f"端口 {self.port} 命令发送定时器循环结束")
    
    def start_reading(self):
        """开始读取数据（幂等：已运行则直接返回）"""
        if self.is_running:
            logger.info(f"端口 {self.port} 已在读取中，忽略重复启动")
            return
        logger.info(f"开始读取端口 {self.port} 数据")
        # 初始化数据文件
        self._initialize_data_file()

        # 启动读取和重连线程
        self.stop_event.clear()
        self.is_running = True

        # P2-3: 单线程模型——三个线程一次性启动且只在 disconnect 时停止。
        # 重连循环不再重启读取线程，避免短暂双读线程竞态。
        # 先启动重连线程（负责首次连接及后续重连）
        self._reconnect_thread = threading.Thread(target=self._reconnect_loop, daemon=True, name=f"ReconnectThread-{self.port}")
        self._reconnect_thread.start()
        logger.info(f"端口 {self.port} 重连线程已启动")

        # 启动命令发送定时器线程（独立于数据读取）
        self.command_timer_thread = threading.Thread(target=self._command_timer_loop, daemon=True, name=f"CommandTimerThread-{self.port}")
        self.command_timer_thread.start()
        logger.info(f"端口 {self.port} 命令发送定时器线程已启动")

        # 启动数据读取线程
        self._read_thread = threading.Thread(target=self._read_loop, daemon=True, name=f"ReadThread-{self.port}")
        self._read_thread.start()
        logger.info(f"端口 {self.port} 读取线程已启动")
    
    def _reconnect_loop(self):
        """重连循环，连接失败时指数退避（5s→30s），成功后恢复初始间隔

        P2-3: 单线程模型——只负责建立/恢复串口连接，不再启动读取线程
        （读取线程在 start_reading 时一次性启动）。连接成功后可尝试
        复活异常退出的读取线程，但绝不同时存在两个。
        """
        logger.info(f"端口 {self.port} 重连循环开始")
        current_backoff = self.reconnect_delay
        while self.is_running and not self.stop_event.is_set():
            try:
                # 停止信号：disconnect 已置位，退出
                if self.stop_event.is_set():
                    break
                # 检查是否已经连接
                if not self.communicator.get_port_status():
                    logger.info(f"端口 {self.port} 未连接，尝试重连")
                    if self.connect():
                        # 连接成功，发送初始化命令
                        self.send_command()
                        current_backoff = self.reconnect_delay  # 重置退避
                        # P2-3: 兜底复活：读取线程异常退出时重建（若还活着则不动）
                        if self._read_thread is None or not self._read_thread.is_alive():
                            logger.warning(f"端口 {self.port} 读取线程已退出，重新启动")
                            self._read_thread = threading.Thread(target=self._read_loop, daemon=True, name=f"ReadThread-{self.port}")
                            self._read_thread.start()
                            logger.info(f"端口 {self.port} 读取线程已重启")
                    else:
                        # 连接失败：指数退避，最多 MAX_RECONNECT_BACKOFF 秒
                        logger.info(f"端口 {self.port} 连接失败，{current_backoff}秒后重试")
                        self.stop_event.wait(current_backoff)
                        current_backoff = min(current_backoff * 2, MAX_RECONNECT_BACKOFF)
                        continue
                # P2-3: 用 stop_event.wait 替代 sleep，disconnect 时立即返回
                self.stop_event.wait(self.reconnect_delay)
            except Exception as e:
                logger.error(f"端口 {self.port} 重连循环出现异常: {e}")
                logger.error(f"详细错误信息: {traceback.format_exc()}")
                self.stop_event.wait(current_backoff)
                current_backoff = min(current_backoff * 2, MAX_RECONNECT_BACKOFF)
        logger.info(f"端口 {self.port} 重连循环结束")
    
    def _read_loop(self):
        """数据读取循环（单线程模型：仅在 start_reading/兜底复活时启动一次）"""
        logger.info(f"端口 {self.port} 数据读取循环开始")
        while self.is_running and not self.stop_event.is_set():
            try:
                # 使用 is_connected() 而非直接访问 serial_conn，确保锁保护
                if self.communicator.is_connected():
                    raw_data = self.communicator.read_line()
                    if raw_data:
                        # 将读取到的数据追加到帧缓冲区（加锁保护）
                        # P3-1: 不再逐帧记录原始数据（serial_communicator 同改），
                        # 失败路径 WARNING 已带原始帧
                        with self._buffer_lock:
                            self.data_buffer += raw_data
                            # 统计读取块数
                            with self._stats_lock:
                                self.stats['read_chunks'] += 1
                            # 重组完整帧并处理
                            self._reassemble_frames()
                # P2-3: 用 stop_event.wait 替代 sleep，disconnect 时立即返回
                self.stop_event.wait(1)
            except serial.SerialException as e:
                logger.error(f"串口 {self.port} 通信错误: {e}")
                logger.error(f"详细错误信息: {traceback.format_exc()}")
                self.stop_event.wait(self.reconnect_delay)
            except Exception as e:
                logger.error(f"读取端口 {self.port} 数据时出错: {e}")
                logger.error(f"详细错误信息: {traceback.format_exc()}")
                self.stop_event.wait(self.reconnect_delay)
        logger.info(f"端口 {self.port} 数据读取循环结束")

    def _handle_frame(self, frame: str) -> bool:
        """处理一个完整帧（解析、存储、推送）
        返回: True=处理成功，False=解析失败
        注意: 调用方必须已持有 _buffer_lock
        """
        frame = frame.strip()
        if not frame:
            return False
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
            # P2-4: 降为 DEBUG——原始数据已有 DEBUG 级日志，逐帧 INFO 造成磁盘写放大
            logger.debug(f"端口 {self.port} 数据处理完成: {parsed_data}")
            return True
        else:
            logger.warning(f"端口 {self.port} 数据解析失败: {frame}")
            return False

    def get_stats(self) -> Dict:
        """获取鲁棒性统计信息（线程安全）"""
        with self._stats_lock:
            return dict(self.stats)

    def _reassemble_frames(self):
        """从帧缓冲区重组完整帧并处理
        支持两种定界方式：
          1. 换行符定界（\n 或 \r）：标准行协议，通常由设备按行输出
          2. #...# 定界：帧以#开头和结尾
        网络/串口拥塞导致的分片（一次只到上半截、下一秒到下半截）会先落在
        缓冲区等待，直到定界符到齐后才被提取，从而避免解析成错误数据。
        单帧长度上限 MAX_FRAME_LENGTH 防止坏帧无限增长。
        注意: 调用方必须已持有 _buffer_lock
        """
        while self.data_buffer:
            # P3-5: 换行定界同时识别 \n 与 \r，取先出现的（\r\n 连排中 \r 在前）。
            # 此前只 find('\n')，纯 \r 结尾的设备数据会一直积压到超限被丢弃。
            i_nl = self.data_buffer.find('\n')
            i_cr = self.data_buffer.find('\r')
            if i_nl != -1 or i_cr != -1:
                if i_nl == -1:
                    idx = i_cr
                elif i_cr == -1:
                    idx = i_nl
                else:
                    idx = min(i_nl, i_cr)
                frame = self.data_buffer[:idx]
                # \r\n 连排时跳过余下的换行，避免下一轮产生空帧计数
                self.data_buffer = self.data_buffer[idx + 1:].lstrip('\r\n')
                self._handle_frame(frame)
                continue

            # 无换行符，尝试 #...# 帧
            start_idx = self.data_buffer.find('#')
            if start_idx == -1:
                # 没有换行、也没有#：可能是断帧前导部分或脏数据。
                # 若有完整的换行数据但缺末尾换行（尾部残余），暂留等待；
                # 超过单帧上限则丢弃（避免脏数据无限累积）。
                if len(self.data_buffer) > MAX_FRAME_LENGTH:
                    logger.warning(f"端口 {self.port} 缓冲区无帧定界符且超长，丢弃脏数据: {self.data_buffer[:100]!r}...")
                    with self._stats_lock:
                        self.stats['discarded_frames'] += 1
                        self.stats['buffer_truncations'] += 1
                    self.data_buffer = ""
                return  # 等待更多数据

            # 存在#起始，查找配对的结束#
            end_idx = self.data_buffer.find('#', start_idx + 1)
            if end_idx == -1:
                # 有起始#但无结束#：帧不完整，等待更多数据（分片场景核心等待点）
                if len(self.data_buffer) - start_idx > MAX_FRAME_LENGTH:
                    logger.warning(f"端口 {self.port} 帧数据超长且无结束#，截断该帧: {self.data_buffer[start_idx:start_idx + 100]!r}...")
                    with self._stats_lock:
                        self.stats['discarded_frames'] += 1
                        self.stats['buffer_truncations'] += 1
                    # 丢弃该帧，保留#起始之后的部分继续等待（下一帧可能已混入）
                    self.data_buffer = self.data_buffer[start_idx + 1:]
                return  # 等待更多数据

            # 帧完整：#...#。先清理起始#之前的任何残余垃圾
            if start_idx > 0:
                logger.debug(f"端口 {self.port} 丢弃帧起始#之前的数据: {self.data_buffer[:start_idx]!r}")
                with self._stats_lock:
                    self.stats['discarded_frames'] += 1
                self.data_buffer = self.data_buffer[start_idx:]

            frame = self.data_buffer[:end_idx + 1]
            self.data_buffer = self.data_buffer[end_idx + 1:]

            # P1-8: 检查帧内是否包含多余的#号，可能格式异常
            frame_content = frame[1:-1] if len(frame) > 2 else frame
            if frame_content.count('#') > 0:
                logger.warning(f"端口 {self.port} 帧内包含多余的#号，可能格式异常: {frame}")

            # 记录重组信息：只有当帧跨越多块数据才标记重组（分片场景）
            with self._stats_lock:
                self.stats['last_reassembly'] = datetime.now().isoformat()
                self.stats['reassembled_frames'] += 1

            handled = self._handle_frame(frame)
            if not handled:
                with self._stats_lock:
                    self.stats['discarded_frames'] += 1

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