"""
监控管理器模块
负责管理多个串口读取器和Web服务器
"""

import sys
import os
import re
import threading
import logging
from typing import Dict, List
from datetime import datetime
import traceback
from flask import Flask
from flask_socketio import SocketIO


logger = logging.getLogger(__name__)

# P2-9: 模块级单例引用，供诊断模块查询当前活动端口（避免循环导入）。
wind_monitor_manager = None


class WindMonitorManager:
    def __init__(self):
        global wind_monitor_manager
        wind_monitor_manager = self  # P2-9: 登记模块级单例，供诊断模块查询
        self.readers: Dict[str, 'SerialWindDataReader'] = {}
        self.data_lock = threading.RLock()  # 使用可重入锁
        self.web_port = 5000  # 默认端口，start_server 时更新
        # 兼容PyInstaller打包路径：打包后使用sys._MEIPASS作为基路径
        if getattr(sys, 'frozen', False):
            base_path = sys._MEIPASS
        else:
            base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        template_dir = os.path.join(base_path, 'templates')
        logger.info(f"模板目录路径: {template_dir}")
        logger.info(f"模板目录是否存在: {os.path.isdir(template_dir)}")
        # P2-10: 显式指定 static_folder。默认会相对包含本类的目录（modules/）
        # 解析成 modules/static，故必须指向 base_path/static 以提供本地前端库。
        static_dir = os.path.join(base_path, 'static')
        self.app = Flask(__name__, template_folder=template_dir, static_folder=static_dir, static_url_path='/static')
        # P2-5: CORS 收紧为同源（此前为 *，配合 Origin 校验可防跨站请求）。
        # 同时允许 127.0.0.1 与 localhost 两种本机访问方式。
        self.socketio = SocketIO(
            self.app,
            cors_allowed_origins=[
                "http://127.0.0.1:5000",
                "http://localhost:5000",
            ],
            async_mode='threading'
        )
        # 延迟导入WebRoutes以避免循环导入
        from .web_routes import WebRoutes
        self.web_routes = WebRoutes(self)
        # 延迟导入ConfigRoutes以避免循环导入
        from .config_routes import ConfigRoutes
        self.config_routes = ConfigRoutes(self)
        logger.info("风速监控管理器初始化完成")

    def emit_data(self, port: str, wind_data_obj):
        """通过WebSocket推送数据到前端"""
        try:
            data = wind_data_obj.to_dict()
            data['port'] = port
            self.socketio.emit('wind_data', data)
            logger.debug(f"端口 {port} 数据已通过WebSocket推送: {data}")
        except Exception as e:
            logger.error(f"端口 {port} WebSocket推送数据失败: {e}")

    def add_reader(self, port: str, baudrate: int = 9600, bytesize: int = 8, parity: str = 'N', stopbits: int = 1):
        """添加串口读取器并自动启动数据读取
        返回值:
            - True: 添加成功
            - 'already_connected': 端口已存在且已连接
            - False: 添加失败
        """
        try:
            old_reader = None
            with self.data_lock:
                if port in self.readers:
                    reader = self.readers[port]
                    # 检查现有 reader 的串口是否真正连接（使用深度检测）
                    if reader.communicator.is_connected(quick_check=False):
                        logger.warning(f"端口 {port} 已存在且已连接，无需重复添加")
                        return 'already_connected'
                    else:
                        # reader 存在但串口实际未连接，标记待移除后重新添加
                        logger.warning(f"端口 {port} 已存在但串口未连接，移除旧 reader 后重新添加")
                        old_reader = reader
                        del self.readers[port]

            # 在锁外执行 disconnect，避免长时间持锁阻塞其他操作
            # disconnect 内部会 join 线程（最多2秒）和关闭文件，不应在锁内进行
            if old_reader is not None:
                try:
                    old_reader.disconnect()
                except Exception as e:
                    logger.warning(f"断开旧 reader 时出错（可忽略）: {e}")

            with self.data_lock:
                from .serial_reader import SerialWindDataReader
                # 注：reader 构造（含 DataStorage 打开当日文件）在锁内进行，
                # 是一次性操作（<10ms），故保持现状；与 remove_reader 的
                # "长操作放锁外"并不冲突。
                reader = SerialWindDataReader(port, baudrate, bytesize, parity, stopbits, on_data_callback=self.emit_data)
                self.readers[port] = reader
                logger.info(f"成功添加端口 {port} 读取器")

                # 自动启动数据读取（这会触发连接和命令发送）
                logger.info(f"开始启动端口 {port} 数据读取")
                reader.start_reading()
                logger.info(f"端口 {port} 数据读取已启动")

                return True
        except Exception as e:
            logger.error(f"添加端口 {port} 读取器时发生错误: {e}")
            logger.error(f"详细错误信息: {traceback.format_exc()}")
            return False

    def remove_reader(self, port: str) -> bool:
        """移除串口读取器"""
        try:
            reader = None
            with self.data_lock:
                if port not in self.readers:
                    logger.warning(f"端口 {port} 不存在，无法移除")
                    return False

                reader = self.readers.pop(port)

            # P2-3: 在锁外执行 disconnect（join 线程最多约6秒 + 文件/串口关闭），
            # 避免长时间持锁阻塞其他端口的 status/data 操作。对齐 add_reader 的
            # "长操作放锁外"模式。
            if reader is not None:
                try:
                    reader.disconnect()  # 断开连接并清理资源
                except Exception as e:
                    logger.error(f"断开端口 {port} 读取器时出错: {e}")
                    logger.error(f"详细错误信息: {traceback.format_exc()}")
            logger.info(f"成功移除端口 {port} 读取器")
            return True
        except Exception as e:
            logger.error(f"移除端口 {port} 读取器时发生错误: {e}")
            logger.error(f"详细错误信息: {traceback.format_exc()}")
            return False

    def get_reader(self, port: str):
        """获取指定端口的读取器"""
        try:
            with self.data_lock:
                reader = self.readers.get(port)
                if reader:
                    logger.debug(f"获取端口 {port} 读取器成功")
                else:
                    logger.debug(f"端口 {port} 读取器不存在")
                return reader
        except Exception as e:
            logger.error(f"获取端口 {port} 读取器时发生错误: {e}")
            logger.error(f"详细错误信息: {traceback.format_exc()}")
            return None

    def get_all_readers_status(self) -> Dict:
        """获取所有读取器状态"""
        try:
            # P2-3: 锁内仅快照 readers，锁外逐条查询（查询含锁内 I/O/序列化，
            # 不应持 data_lock）。对快照后已被并发移除的 reader 查询是安全的：
            # is_connected 返回 False、storage 方法有守卫。
            with self.data_lock:
                items = list(self.readers.items())
            status = {}
            for port, reader in items:
                try:
                    status[port] = {
                        'port': port,
                        'connected': reader.communicator.is_connected(),
                        'running': reader.is_running,
                        'data_count': len(reader.storage.data_buffer),
                        'latest_data': reader.get_latest_data(),
                        'robustness_stats': reader.get_stats()
                    }
                except Exception as e:
                    logger.error(f"获取端口 {port} 状态时发生错误: {e}")
                    status[port] = {
                        'port': port,
                        'error': str(e)
                    }
            logger.debug(f"获取所有读取器状态成功，共 {len(status)} 个端口")
            return status
        except Exception as e:
            logger.error(f"获取所有读取器状态时发生错误: {e}")
            logger.error(f"详细错误信息: {traceback.format_exc()}")
            return {}

    def send_command_to_port(self, port: str, command=None) -> bool:
        """向指定端口发送命令"""
        try:
            reader = self.get_reader(port)
            if not reader:
                logger.warning(f"端口 {port} 不存在，无法发送命令")
                return False
            
            if command is None:
                from .serial_reader import HEX_COMMAND
                command = HEX_COMMAND
            
            # 类型检查和转换：确保command为bytes类型
            if isinstance(command, str):
                try:
                    command = bytes.fromhex(command)
                except ValueError:
                    command = command.encode('utf-8')
            
            result = reader.send_command(command)
            if result:
                logger.info(f"成功向端口 {port} 发送命令")
            else:
                logger.error(f"向端口 {port} 发送命令失败")
            return result
        except Exception as e:
            logger.error(f"向端口 {port} 发送命令时发生错误: {e}")
            logger.error(f"详细错误信息: {traceback.format_exc()}")
            return False

    def save_all_data(self) -> List[str]:
        """保存所有数据"""
        try:
            # P2-3: 锁内仅快照 readers，锁外执行 shutil.copy2（大文件复制
            # 可能耗时数秒，不应持 data_lock 阻塞其他端口操作）
            with self.data_lock:
                items = list(self.readers.items())

            saved_files = []
            for port, reader in items:
                try:
                    # P0-2: 净化 custom_name，避免路径遍历风险
                    safe_name = re.sub(r'[\\/:*?"<>|]', '_', reader.custom_name) if reader.custom_name else port
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    filename = f"wind_data_{port}_{safe_name}_{timestamp}.csv"
                    reader.save_data_to_csv(filename)
                    saved_files.append(filename)
                    logger.info(f"端口 {port} 数据已保存到 {filename}")
                except Exception as e:
                    logger.error(f"保存端口 {port} 数据时发生错误: {e}")
                    logger.error(f"详细错误信息: {traceback.format_exc()}")
            logger.info(f"数据保存完成，共保存 {len(saved_files)} 个文件")
            return saved_files
        except Exception as e:
            logger.error(f"保存所有数据时发生错误: {e}")
            logger.error(f"详细错误信息: {traceback.format_exc()}")
            return []

    def start_server(self, host: str = '127.0.0.1', port: int = 5000):
        """启动Web服务器"""
        try:
            logger.info(f"启动Web服务器 http://{host}:{port}")
            # P2-5: 记录实际端口供 Origin/Host 校验使用；若端口非默认，
            # 同步收紧 CORS 同源配置
            self.web_port = port
            if self.socketio is not None:
                try:
                    self.socketio.server.eio.cors_allowed_origins = [
                        f'http://127.0.0.1:{port}',
                        f'http://localhost:{port}',
                    ]
                except Exception:
                    pass
            self.socketio.run(self.app, host=host, port=port, debug=False, allow_unsafe_werkzeug=True)
        except OSError as e:
            # P1-7: 端口占用时给用户更明确的信息
            if "10048" in str(e) or "address already in use" in str(e).lower():
                logger.error(f"端口 {port} 已被占用，请修改端口配置或关闭占用程序")
            else:
                logger.error(f"启动Web服务器时发生错误: {e}")
            raise
        except Exception as e:
            logger.error(f"启动Web服务器时发生错误: {e}")
            logger.error(f"详细错误信息: {traceback.format_exc()}")
            raise

    def set_reader_custom_name(self, port: str, custom_name: str) -> bool:
        """设置读取器的自定义名称"""
        try:
            with self.data_lock:
                if port in self.readers:
                    self.readers[port].custom_name = custom_name
                    logger.info(f"端口 {port} 自定义名称已设置为: {custom_name}")
                    return True
                else:
                    logger.warning(f"端口 {port} 不存在，无法设置自定义名称")
                    return False
        except Exception as e:
            logger.error(f"设置端口 {port} 自定义名称时发生错误: {e}")
            logger.error(f"详细错误信息: {traceback.format_exc()}")
            return False

    def stop_all_readers(self):
        """停止所有读取器"""
        try:
            logger.info("开始停止所有读取器")
            with self.data_lock:
                ports_to_remove = list(self.readers.keys())

            for port in ports_to_remove:
                try:
                    self.remove_reader(port)
                except Exception as e:
                    logger.error(f"停止端口 {port} 读取器时发生错误: {e}")
                    logger.error(f"详细错误信息: {traceback.format_exc()}")

            logger.info(f"所有读取器已停止，共停止 {len(ports_to_remove)} 个")
        except Exception as e:
            logger.error(f"停止所有读取器时发生错误: {e}")
            logger.error(f"详细错误信息: {traceback.format_exc()}")