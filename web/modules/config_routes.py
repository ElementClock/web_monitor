"""
配置管理路由模块
负责串口参数配置、诊断等配置类Web路由
"""

import logging
import serial.tools.list_ports

from flask import jsonify, request
from .serial_diagnostics import serial_diagnostics
from .serial_config_manager import config_manager, param_helper, SerialConfig


logger = logging.getLogger(__name__)


class ConfigRoutes:
    """配置管理路由类，专门处理串口配置和诊断相关路由"""
    def __init__(self, manager):
        self.manager = manager
        self.app = manager.app
        self.socketio = manager.socketio
        self._setup_routes()

    def _setup_routes(self):
        """设置Flask路由"""

        @self.app.route('/api/get_available_ports')
        def get_available_ports():
            """获取系统中所有可用的串口"""
            try:
                ports = []
                available_ports = list(serial.tools.list_ports.comports())
                for port in available_ports:
                    ports.append({
                        'device': port.device,
                        'description': port.description
                    })
                return jsonify({'success': True, 'ports': ports})
            except Exception as e:
                logger.error(f"获取可用端口时发生异常: {e}")
                return jsonify({'success': False, 'message': str(e)})

        @self.app.route('/api/diagnose_port', methods=['POST'])
        def diagnose_port():
            """诊断指定串口的连接质量"""
            try:
                data = request.get_json()
                port = data.get('port')
                baudrate = data.get('baudrate', 9600)

                if not port:
                    return jsonify({'success': False, 'message': '端口参数缺失'})

                diagnosis_result = serial_diagnostics.diagnose_port_connection(port, baudrate)
                return jsonify({'success': True, 'diagnosis': diagnosis_result})
            except Exception as e:
                logger.error(f"诊断端口时发生异常: {e}")
                return jsonify({'success': False, 'message': str(e)})

        @self.app.route('/api/diagnose_all_ports')
        def diagnose_all_ports():
            """诊断所有可用串口"""
            try:
                diagnosis_results = serial_diagnostics.get_available_ports_with_diagnostics()
                return jsonify({'success': True, 'results': diagnosis_results})
            except Exception as e:
                logger.error(f"诊断所有端口时发生异常: {e}")
                return jsonify({'success': False, 'message': str(e)})

        @self.app.route('/api/config/options')
        def get_config_options():
            """获取串口配置选项"""
            try:
                options = {
                    'baudrates': param_helper.get_baudrate_options(),
                    'parities': param_helper.get_parity_options(),
                    'bytesizes': param_helper.get_bytesize_options(),
                    'stopbits': param_helper.get_stopbits_options()
                }
                return jsonify({'success': True, 'options': options})
            except Exception as e:
                logger.error(f"获取配置选项时发生异常: {e}")
                return jsonify({'success': False, 'message': str(e)})

        @self.app.route('/api/config/list')
        def list_configs():
            """列出所有串口配置"""
            try:
                configs = config_manager.get_all_configs()
                config_list = []
                for port, config in configs.items():
                    config_dict = config.to_dict()
                    config_dict['display_name'] = param_helper.format_config_display(config)
                    config_list.append(config_dict)
                return jsonify({'success': True, 'configs': config_list})
            except Exception as e:
                logger.error(f"列出配置时发生异常: {e}")
                return jsonify({'success': False, 'message': str(e)})

        @self.app.route('/api/config/add', methods=['POST'])
        def add_config():
            """添加串口配置"""
            try:
                data = request.get_json()
                config = SerialConfig(
                    port=data.get('port'),
                    baudrate=int(data.get('baudrate', 9600)),
                    bytesize=int(data.get('bytesize', 8)),
                    parity=data.get('parity', 'N'),
                    stopbits=float(data.get('stopbits', 1)),
                    timeout=float(data.get('timeout', 1.0)),
                    name=data.get('name', '')
                )

                success, errors = config_manager.add_config(config)
                if success:
                    return jsonify({'success': True, 'message': '配置添加成功'})
                else:
                    return jsonify({'success': False, 'message': '; '.join(errors)})
            except Exception as e:
                logger.error(f"添加配置时发生异常: {e}")
                return jsonify({'success': False, 'message': str(e)})

        @self.app.route('/api/config/validate', methods=['POST'])
        def validate_config():
            """验证串口配置"""
            try:
                data = request.get_json()
                config = SerialConfig(
                    port=data.get('port'),
                    baudrate=int(data.get('baudrate', 9600)),
                    bytesize=int(data.get('bytesize', 8)),
                    parity=data.get('parity', 'N'),
                    stopbits=float(data.get('stopbits', 1)),
                    timeout=float(data.get('timeout', 1.0))
                )

                from .serial_config_manager import SerialConfigValidator
                is_valid, errors = SerialConfigValidator.validate_config(config)

                return jsonify({
                    'success': True,
                    'valid': is_valid,
                    'errors': errors
                })
            except Exception as e:
                logger.error(f"验证配置时发生异常: {e}")
                return jsonify({'success': False, 'message': str(e)})

        @self.app.route('/api/config/recommend')
        def get_recommend_configs():
            """获取推荐配置"""
            try:
                recommended = config_manager.get_recommended_configs()
                configs = []
                for config in recommended:
                    config_dict = config.to_dict()
                    config_dict['display_name'] = param_helper.format_config_display(config)
                    configs.append(config_dict)
                return jsonify({'success': True, 'configs': configs})
            except Exception as e:
                logger.error(f"获取推荐配置时发生异常: {e}")
                return jsonify({'success': False, 'message': str(e)})

        @self.app.route('/api/set_port_name', methods=['POST'])
        def set_port_name():
            """设置端口自定义名称"""
            try:
                data = request.get_json()
                port = data.get('port')
                custom_name = data.get('custom_name', '')

                if not port:
                    return jsonify({'success': False, 'message': '端口参数缺失'})

                if self.manager.set_reader_custom_name(port, custom_name):
                    return jsonify({'success': True, 'message': f'端口 {port} 自定义名称已设置'})
                else:
                    return jsonify({'success': False, 'message': f'端口 {port} 不存在'})
            except Exception as e:
                logger.error(f"设置端口自定义名称时发生异常: {e}")
                return jsonify({'success': False, 'message': str(e)})
