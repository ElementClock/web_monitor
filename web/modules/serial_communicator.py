"""
串口通信模块
负责串口连接和数据读取
"""

import serial
import threading
import logging
from typing import Optional
import traceback


logger = logging.getLogger(__name__)


class SerialCommunicator:
    """串口通信类，专门负责串口连接和数据读取"""
    def __init__(self, port: str, baudrate: int = 9600, bytesize: int = 8, 
                 parity: str = 'N', stopbits: int = 1):
        self.port = port
        self.baudrate = baudrate
        self.bytesize = bytesize  # 数据位
        self.parity = parity      # 校验位
        self.stopbits = stopbits  # 停止位
        self.serial_conn: Optional[serial.Serial] = None
        self.is_running = False
        self.lock = threading.RLock()  # 使用可重入锁
        
    def connect(self) -> bool:
        """连接串口"""
        try:
            logger.info(f"尝试连接到串口 {self.port}，波特率 {self.baudrate}")
            logger.info(f"串口参数: 数据位={self.bytesize}, 校验位={self.parity}, 停止位={self.stopbits}")
            # 确保先断开任何现有连接
            with self.lock:
                if self.serial_conn and self.serial_conn.is_open:
                    logger.info(f"断开已存在的串口 {self.port} 连接")
                    self.serial_conn.close()
                
                self.serial_conn = serial.Serial(
                    port=self.port,
                    baudrate=self.baudrate,
                    bytesize=self.bytesize,
                    parity=self.parity,
                    stopbits=self.stopbits,
                    timeout=1,
                    write_timeout=0.5  # 减少写入超时到0.5秒
                )
                
            logger.info(f"成功连接到端口 {self.port}，波特率 {self.baudrate}")
            return True
        except serial.SerialException as e:
            logger.error(f"串口 {self.port} 连接失败，可能端口不存在或被占用: {e}")
            logger.error(f"详细错误信息: {traceback.format_exc()}")
            return False
        except PermissionError as e:
            logger.error(f"串口 {self.port} 访问被拒绝，可能端口正在被其他程序使用: {e}")
            logger.error(f"详细错误信息: {traceback.format_exc()}")
            return False
        except Exception as e:
            logger.error(f"连接端口 {self.port} 时发生未知错误: {e}")
            logger.error(f"详细错误信息: {traceback.format_exc()}")
            return False

    def disconnect(self):
        """断开串口连接"""
        logger.info(f"开始断开端口 {self.port} 连接")
        with self.lock:
            if self.serial_conn and self.serial_conn.is_open:
                try:
                    self.serial_conn.close()
                    logger.info(f"端口 {self.port} 连接已断开")
                except Exception as e:
                    logger.error(f"断开端口 {self.port} 连接时发生错误: {e}")
                    logger.error(f"详细错误信息: {traceback.format_exc()}")
            else:
                logger.warning(f"端口 {self.port} 未处于打开状态，无需断开")
        logger.info(f"断开端口 {self.port} 连接完成")

    def send_command(self, command: bytes) -> bool:
        """向串口发送命令"""
        try:
            with self.lock:
                if self.serial_conn and self.serial_conn.is_open:
                    try:
                        # 尝试写入命令
                        bytes_written = self.serial_conn.write(command)
                        logger.debug(f"向端口 {self.port} 写入 {bytes_written} 字节")
                        
                        # 尝试flush，但不强制要求成功
                        try:
                            self.serial_conn.flush()
                            logger.debug(f"端口 {self.port} flush操作成功")
                        except Exception as flush_error:
                            logger.warning(f"端口 {self.port} flush操作超时或失败: {flush_error}")
                            # flush失败不影响命令发送结果
                        
                        logger.info(f"向端口 {self.port} 发送命令: {command.hex() if isinstance(command, bytes) else command}")
                        return True
                    except serial.SerialTimeoutException as timeout_error:
                        logger.warning(f"端口 {self.port} 写入超时: {timeout_error}")
                        # 超时意味着数据可能未完全写入，返回False通知调用方
                        return False
                    except Exception as write_error:
                        logger.error(f"端口 {self.port} 写入命令失败: {write_error}")
                        return False
                else:
                    logger.warning(f"端口 {self.port} 未连接，无法发送命令")
                    return False
        except serial.SerialException as e:
            logger.error(f"串口 {self.port} 通信错误，无法发送命令: {e}")
            logger.error(f"详细错误信息: {traceback.format_exc()}")
            return False
        except Exception as e:
            logger.error(f"向端口 {self.port} 发送命令时发生未知错误: {e}")
            logger.error(f"详细错误信息: {traceback.format_exc()}")
            return False

    def read_line(self) -> Optional[str]:
        """从串口读取一行数据，支持 #...# 帧格式"""
        try:
            with self.lock:
                if not self.serial_conn or not self.serial_conn.is_open:
                    return None
                if self.serial_conn.in_waiting <= 0:
                    return None

                # P1-4: 对 in_waiting 设置上限 4096，防止单次读取过多数据导致 OOM
                bytes_to_read = min(self.serial_conn.in_waiting, 4096)
                raw_bytes = self.serial_conn.read(bytes_to_read)
                if not raw_bytes:
                    return None

                # 尝试解码，使用errors='ignore'容错处理非UTF-8字节
                try:
                    decoded = raw_bytes.decode('utf-8', errors='ignore').strip()
                except Exception:
                    return None

                if not decoded:
                    return None

                logger.debug(f"从端口 {self.port} 读取到原始数据: {decoded}")
                return decoded
        except serial.SerialException as e:
            logger.error(f"串口 {self.port} 通信错误: {e}")
            logger.error(f"详细错误信息: {traceback.format_exc()}")
            return None
        except Exception as e:
            logger.error(f"读取端口 {self.port} 数据时发生未知错误: {e}")
            logger.error(f"详细错误信息: {traceback.format_exc()}")
            return None

    def is_connected(self, quick_check: bool = True) -> bool:
        """检查串口是否真正连接

        Args:
            quick_check: True=快速检查只判断is_open, False=实际通信检测
        """
        with self.lock:
            if not self.serial_conn or not self.serial_conn.is_open:
                return False

            if quick_check:
                # 快速检查：只判断串口对象是否打开
                return True

            # 深度检查：尝试读取数据验证连接有效性
            try:
                # 检查缓冲区是否有数据，这是连接有效的重要标志
                if self.serial_conn.in_waiting > 0:
                    return True

                # 尝试写入一个字节测试连接（静默操作，不影响正常通信）
                # 使用最低波特率测试，写入时间很短
                test_byte = b'\x00'
                old_timeout = self.serial_conn.timeout
                self.serial_conn.timeout = 0.1  # 短超时
                try:
                    # 只尝试写入，不等待响应
                    self.serial_conn.write(test_byte)
                    return True
                except:
                    pass
                finally:
                    self.serial_conn.timeout = old_timeout

                return True
            except Exception:
                return False

    def get_port_status(self) -> bool:
        """获取端口连接状态"""
        return self.is_connected()