"""
Web路由模块
负责处理Web路由
"""

import logging
import sys
import os

from flask import render_template, jsonify, request


logger = logging.getLogger(__name__)


class WebRoutes:
    """Web路由类，专门处理Web路由"""
    def __init__(self, manager):
        self.manager = manager
        self.app = manager.app
        self.socketio = manager.socketio
        self._setup_routes()

    def _setup_routes(self):
        """设置Flask路由"""
        
        @self.app.route('/')
        def index():
            return render_template('index.html')
        
        @self.app.route('/api/status')
        def get_status():
            status = self.manager.get_all_readers_status()
            return jsonify(status)
        
        @self.app.route('/api/data/latest')
        def get_latest_data():
            latest_data = {}
            with self.manager.data_lock:
                for port, reader in self.manager.readers.items():
                    data = reader.get_latest_data()
                    if data:
                        latest_data[port] = data
            return jsonify(latest_data)
        
        @self.app.route('/api/add_port', methods=['POST'])
        def add_port():
            try:
                data = request.get_json()
                port = data.get('port')
                # 获取可选的串口参数
                baudrate = data.get('baudrate', 9600)
                bytesize = data.get('bytesize', 8)
                parity = data.get('parity', 'N')
                stopbits = data.get('stopbits', 1)

                if not port:
                    return jsonify({'success': False, 'message': '端口参数缺失'})

                result = self.manager.add_reader(port, baudrate, bytesize, parity, stopbits)
                if result is True:
                    return jsonify({'success': True, 'message': f'端口 {port} 已添加，参数: 波特率={baudrate}, 数据位={bytesize}, 校验位={parity}, 停止位={stopbits}'})
                elif result == 'already_connected':
                    return jsonify({'success': False, 'message': f'端口 {port} 已连接，无需重复添加', 'code': 'already_connected'})
                else:
                    return jsonify({'success': False, 'message': f'端口 {port} 添加失败'})
            except Exception as e:
                logger.error(f"添加端口时发生异常: {e}")
                return jsonify({'success': False, 'message': str(e)})
        
        @self.app.route('/api/remove_port', methods=['POST'])
        def remove_port():
            try:
                data = request.get_json()
                port = data.get('port')
                
                if not port:
                    return jsonify({'success': False, 'message': '端口参数缺失'})
                
                if self.manager.remove_reader(port):
                    return jsonify({'success': True, 'message': f'端口 {port} 已移除'})
                else:
                    return jsonify({'success': False, 'message': f'端口 {port} 不存在'})
            except Exception as e:
                logger.error(f"移除端口时发生异常: {e}")
                return jsonify({'success': False, 'message': str(e)})
        
        @self.app.route('/api/send_command', methods=['POST'])
        def send_command():
            try:
                data = request.get_json()
                port = data.get('port')
                command = data.get('command', None)  # 可选的自定义命令
                
                if not port:
                    return jsonify({'success': False, 'message': '端口参数缺失'})
                
                if self.manager.send_command_to_port(port, command):
                    return jsonify({'success': True, 'message': f'命令已发送到端口 {port}'})
                else:
                    return jsonify({'success': False, 'message': f'向端口 {port} 发送命令失败'})
            except Exception as e:
                logger.error(f"发送命令时发生异常: {e}")
                return jsonify({'success': False, 'message': str(e)})
        
        @self.app.route('/api/start_program', methods=['POST'])
        def start_program():
            """启动程序（这里可以添加一些全局的启动逻辑）"""
            try:
                # 程序实际上已经启动，这里可以添加一些初始化逻辑
                return jsonify({'success': True, 'message': '程序已启动'})
            except Exception as e:
                logger.error(f"启动程序时发生异常: {e}")
                return jsonify({'success': False, 'message': str(e)})
        
        @self.app.route('/api/stop_program', methods=['POST'])
        def stop_program():
            """停止程序，断开所有连接但不退出主程序"""
            try:
                # 注意：此处先获取端口列表再逐个移除，存在TOCTOU竞态的微小风险，
                # 但在RLock场景下remove_reader已有端口不存在的容错处理，影响有限
                # 关闭所有端口连接
                with self.manager.data_lock:
                    ports_to_remove = list(self.manager.readers.keys())
                
                for port in ports_to_remove:
                    self.manager.remove_reader(port)
                
                # 通知前端程序已停止
                try:
                    self.socketio.emit('program_stopped', {'message': '程序已停止'})
                except Exception as e:
                    logger.error(f"发送停止消息失败: {e}")
                
                return jsonify({'success': True, 'message': '程序已停止'})
            except Exception as e:
                logger.error(f"停止程序时发生异常: {e}")
                return jsonify({'success': False, 'message': str(e)})
        
        @self.app.route('/api/save')
        def save_data():
            try:
                saved_files = self.manager.save_all_data()
                return jsonify({'success': True, 'message': f'数据保存成功，共保存{len(saved_files)}个文件', 'files': saved_files})
            except Exception as e:
                logger.error(f"保存数据时发生异常: {e}")
                return jsonify({'success': False, 'message': str(e)})
        
        @self.app.route('/api/exit', methods=['POST'])
        def exit_program():
            """退出程序"""
            try:
                # 先调用 stop_all_readers 清理所有串口资源（断开连接、关闭文件）
                self.manager.stop_all_readers()

                # 通知前端程序已退出
                try:
                    self.socketio.emit('program_exit', {'message': '程序已退出'})
                except Exception as e:
                    logger.error(f"发送退出消息失败: {e}")

                logger.info("程序正常退出")
                # 使用 os._exit 直接终止进程（sys.exit 在 Flask 路由内会被捕获，无法退出）
                os._exit(0)
            except Exception as e:
                logger.error(f"退出程序时发生异常: {e}")
                os._exit(1)

        # ========== 日志和数据管理 API ==========

        @self.app.route('/api/logs/list')
        def list_log_files():
            """获取日志文件列表"""
            try:
                import glob
                import os
                log_dir = 'logs'
                if not os.path.exists(log_dir):
                    return jsonify({'success': True, 'files': []})

                pattern = os.path.join(log_dir, 'wind_monitor.log*')
                files = glob.glob(pattern)

                file_list = []
                for f in files:
                    stat = os.stat(f)
                    file_list.append({
                        'name': os.path.basename(f),
                        'size': stat.st_size,
                        'modified': stat.st_mtime
                    })

                # 按修改时间排序，最新的在前
                file_list.sort(key=lambda x: x['modified'], reverse=True)

                return jsonify({'success': True, 'files': file_list})
            except Exception as e:
                logger.error(f"获取日志文件列表失败: {e}")
                return jsonify({'success': False, 'message': str(e)})

        @self.app.route('/api/logs/merge', methods=['POST'])
        def merge_logs():
            """合并所有日志文件"""
            try:
                from realtime_wind_monitor import merge_logs as do_merge_logs
                output_file = do_merge_logs(log_dir='logs')
                if output_file:
                    return jsonify({'success': True, 'message': f'日志合并完成', 'file': output_file})
                else:
                    return jsonify({'success': False, 'message': '没有日志文件可合并'})
            except Exception as e:
                logger.error(f"合并日志失败: {e}")
                return jsonify({'success': False, 'message': str(e)})

        @self.app.route('/api/data/list')
        def list_data_files():
            """获取数据文件列表"""
            try:
                import glob
                import os
                data_dir = 'wind_data'
                if not os.path.exists(data_dir):
                    return jsonify({'success': True, 'files': []})

                pattern = os.path.join(data_dir, 'wind_data_*.csv')
                files = glob.glob(pattern)

                file_list = []
                for f in files:
                    stat = os.stat(f)
                    basename = os.path.basename(f)
                    # 提取端口和日期
                    parts = basename.replace('wind_data_', '').replace('.csv', '').split('_')
                    port = parts[0] if len(parts) > 0 else 'unknown'
                    date = parts[-1] if len(parts) > 1 else 'unknown'

                    file_list.append({
                        'name': basename,
                        'port': port,
                        'date': date,
                        'size': stat.st_size,
                        'modified': stat.st_mtime
                    })

                file_list.sort(key=lambda x: x['modified'], reverse=True)
                return jsonify({'success': True, 'files': file_list})
            except Exception as e:
                logger.error(f"获取数据文件列表失败: {e}")
                return jsonify({'success': False, 'message': str(e)})

        @self.app.route('/api/data/merge', methods=['POST'])
        def merge_all_data():
            """合并所有端口的所有数据文件"""
            try:
                from .data_storage import merge_all_port_data
                output_file = merge_all_port_data(data_dir='wind_data')
                if output_file:
                    return jsonify({'success': True, 'message': f'数据合并完成', 'file': output_file})
                else:
                    return jsonify({'success': False, 'message': '没有数据文件可合并'})
            except Exception as e:
                logger.error(f"合并数据失败: {e}")
                return jsonify({'success': False, 'message': str(e)})

        @self.app.route('/api/data/merge_port', methods=['POST'])
        def merge_port_data():
            """合并指定端口的数据文件"""
            try:
                data = request.get_json()
                port = data.get('port')

                if not port:
                    return jsonify({'success': False, 'message': '端口参数缺失'})

                # 查找该端口的 reader
                reader = self.manager.get_reader(port)
                if not reader:
                    return jsonify({'success': False, 'message': f'端口 {port} 不存在'})

                output_file = reader.storage.merge_daily_files()
                if output_file:
                    return jsonify({'success': True, 'message': f'端口 {port} 数据合并完成', 'file': output_file})
                else:
                    return jsonify({'success': False, 'message': '没有数据文件可合并'})
            except Exception as e:
                logger.error(f"合并端口数据失败: {e}")
                return jsonify({'success': False, 'message': str(e)})

        @self.app.route('/api/cleanup', methods=['POST'])
        def cleanup_old_files():
            """清理过期的日志和数据文件"""
            try:
                data = request.get_json() or {}
                keep_days = data.get('keep_days', 30)  # 默认保留30天

                cleaned_files = []

                # 清理日志
                try:
                    from realtime_wind_monitor import _cleanup_old_logs
                    import glob
                    log_dir = 'logs'
                    if os.path.exists(log_dir):
                        from datetime import datetime, timedelta
                        cutoff = datetime.now() - timedelta(days=keep_days)
                        for f in glob.glob(os.path.join(log_dir, 'wind_monitor.log.*')):
                            try:
                                basename = os.path.basename(f)
                                date_part = basename.split('.')[-1]
                                if len(date_part) == 8 and date_part.isdigit():
                                    file_date = datetime.strptime(date_part, '%Y%m%d')
                                    if file_date < cutoff:
                                        os.remove(f)
                                        cleaned_files.append(f'日志: {basename}')
                            except Exception:
                                pass
                except Exception as e:
                    logger.warning(f"清理日志失败: {e}")

                # 清理数据
                try:
                    from .data_storage import cleanup_old_data
                    cleanup_old_data(data_dir='wind_data', keep_days=keep_days)
                    cleaned_files.append(f'数据: 已清理 {keep_days} 天前的文件')
                except Exception as e:
                    logger.warning(f"清理数据失败: {e}")

                return jsonify({
                    'success': True,
                    'message': f'清理完成，保留最近 {keep_days} 天的文件',
                    'cleaned': cleaned_files
                })
            except Exception as e:
                logger.error(f"清理失败: {e}")
                return jsonify({'success': False, 'message': str(e)})
        
