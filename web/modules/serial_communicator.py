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
                        # 软超时不视为连接失效，保留句柄供下次重试
                        return False
                    except (serial.SerialException, OSError) as write_error:
                        # P2-1: 硬性串口错误（设备移除/损坏）——关闭失效句柄，触发重连
                        logger.error(f"端口 {self.port} 写入命令失败，关闭失效连接: {write_error}")
                        logger.error(f"详细错误信息: {traceback.format_exc()}")
                        self._invalidate_connection(reason=str(write_error))
                        return False
                    except Exception as write_error:
                        logger.error(f"端口 {self.port} 写入命令失败: {write_error}")
                        return False
                else:
                    logger.warning(f"端口 {self.port} 未连接，无法发送命令")
                    return False
        except serial.SerialTimeoutException as e:
            logger.warning(f"串口 {self.port} 通信超时: {e}")
            return False
        except (serial.SerialException, OSError) as e:
            # P2-1: 硬性串口错误——关闭失效句柄，触发重连
            logger.error(f"串口 {self.port} 通信错误，无法发送命令: {e}")
            logger.error(f"详细错误信息: {traceback.format_exc()}")
            self._invalidate_connection(reason=str(e))
            return False
        except Exception as e:
            logger.error(f"向端口 {self.port} 发送命令时发生未知错误: {e}")
            logger.error(f"详细错误信息: {traceback.format_exc()}")
            return False

    def _invalidate_connection(self, reason: str):
        """关闭失效串口句柄，使 is_open 翻 False 以触发重连。

        只在已持锁状态下调用。Windows 拔线后 is_open 不会自动变 False，
        必须主动 close 才能让上层重连门控 get_port_status() 感知断开。
        """
        try:
            if self.serial_conn is not None:
                try:
                    self.serial_conn.close()
                except Exception:
                    pass
                self.serial_conn = None
                logger.warning(f"端口 {self.port} 连接已失效并关闭: {reason}")
        except Exception as e:
            logger.error(f"端口 {self.port} 关闭失效连接出错: {e}")

    def read_line(self) -> Optional[str]:
        """从串口读取原始字节数据
        保留原始数据（含换行符），由上层按帧定界符(换行/#)重组
        """
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
                    decoded = raw_bytes.decode('utf-8', errors='ignore')
                except Exception:
                    return None

                if not decoded:
                    return None

                # P3-1: 不再逐帧记录成功读取的原始数据（磁盘写放大主源）。
                # 解析失败时 parse_data/_handle_frame 的 WARNING 已带原始帧，排查能力不受损。
                return decoded
        except serial.SerialTimeoutException as e:
            # 软超时：设备只是暂时无响应，不影响连接状态
            logger.debug(f"串口 {self.port} 读取超时: {e}")
            return None
        except (serial.SerialException, OSError) as e:
            # P2-1: 硬性串口错误（设备移除/损坏）——关闭失效句柄，触发重连
            logger.error(f"串口 {self.port} 通信错误，关闭失效连接: {e}")
            logger.error(f"详细错误信息: {traceback.format_exc()}")
            self._invalidate_connection(reason=str(e))
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

                # P2-2: 不再向设备写入探测字节（0x00 可能被气象设备解释为
                # 命令/唤醒字节，污染数据线）。改用带短超时的非阻塞读，
                # 若底层句柄已失效，read 会抛硬错误并由 _invalidate_connection
                # 关闭句柄，使上层感知断开。
                old_timeout = self.serial_conn.timeout
                self.serial_conn.timeout = 0.05  # 短超时，仅试探连接活性
                try:
                    self.serial_conn.read(0)
                except serial.SerialTimeoutException:
                    pass  # 软超时 = 连接存活但无数据
                finally:
                    self.serial_conn.timeout = old_timeout

                return True
            except (serial.SerialException, OSError) as e:
                # 句柄已失效：关闭以触发重连
                logger.warning(f"端口 {self.port} 深度检测发现连接失效: {e}")
                self._invalidate_connection(reason=str(e))
                return False
            except Exception:
                return False

    def get_port_status(self) -> bool:
        """获取端口连接状态"""
        return self.is_connected()