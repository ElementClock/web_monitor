"""
串口诊断模块
用于诊断和验证串口连接的真实性和数据流状态
"""

import serial
import time
import logging
import threading
from typing import Dict, List, Optional
import traceback

logger = logging.getLogger(__name__)


def _active_reader_ports():
    """获取当前正被监控使用的串口集合（无管理器时返回空集）。

    延迟导入 monitor_manager 的模块级单例，避免循环导入；
    找不到管理器或读取失败时静默返回空集（诊断照常进行）。
    """
    try:
        from .monitor_manager import wind_monitor_manager
        if wind_monitor_manager is None:
            return set()
        with wind_monitor_manager.data_lock:
            return set(wind_monitor_manager.readers.keys())
    except Exception:
        return set()


class SerialDiagnostics:
    """串口诊断类"""
    
    def __init__(self):
        self.test_data = b"TEST_DATA_123456789"
        self.response_timeout = 3.0  # 响应超时时间
        
    def diagnose_port_connection(self, port: str, baudrate: int = 9600) -> Dict:
        """
        诊断串口连接质量
        返回详细的连接状态和数据流信息
        """
        diagnosis_result = {
            'port': port,
            'baudrate': baudrate,
            'connection_status': False,
            'data_flow_status': False,
            'loopback_detected': False,
            'issues': [],
            'recommendations': []
        }

        # P2-9: 若端口正被活动 reader 监控，直接返回 in_use，避免与监控中的
        # 串口冲突（同时 open 同一端口会抛 PermissionError，且会打断数据流）。
        active_ports = _active_reader_ports()
        if port in active_ports:
            logger.warning(f"端口 {port} 正在被监控使用，跳过诊断")
            diagnosis_result['in_use'] = True
            diagnosis_result['connection_status'] = True
            diagnosis_result['message'] = "该端口正在被监控使用，诊断已跳过"
            return diagnosis_result

        try:
            # 1. 基础连接测试
            logger.info(f"开始诊断端口 {port}")
            
            # 尝试连接
            ser = serial.Serial(
                port=port,
                baudrate=baudrate,
                timeout=2,
                write_timeout=2
            )
            
            if not ser.is_open:
                diagnosis_result['issues'].append("串口无法正常打开")
                diagnosis_result['recommendations'].append("检查串口是否被其他程序占用")
                ser.close()
                return diagnosis_result
                
            diagnosis_result['connection_status'] = True
            logger.info(f"端口 {port} 基础连接成功")
            
            # 2. 清空输入缓冲区
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            
            # 3. 检查初始数据流状态
            initial_bytes = ser.in_waiting
            logger.debug(f"端口 {port} 初始等待字节数: {initial_bytes}")
            
            # 4. 发送测试数据并检查回环
            loopback_result = self._test_loopback_detection(ser)
            diagnosis_result['loopback_detected'] = loopback_result['detected']
            
            if loopback_result['detected']:
                diagnosis_result['issues'].append("检测到虚拟串口回环，可能是虚拟串口配对")
                diagnosis_result['recommendations'].append("确认连接的是真实的硬件设备而非虚拟串口")
            
            # 5. 检查真实数据流
            data_flow_result = self._test_real_data_flow(ser)
            diagnosis_result['data_flow_status'] = data_flow_result['status']
            
            if not data_flow_result['status']:
                diagnosis_result['issues'].append("未检测到外部设备的真实数据流")
                diagnosis_result['recommendations'].append("确认外部设备已正确连接并发送数据")
                diagnosis_result['recommendations'].append("检查设备电源和通信线路")
            
            # 6. 综合评估
            if diagnosis_result['loopback_detected']:
                diagnosis_result['overall_status'] = "WARNING"
                diagnosis_result['message'] = "检测到虚拟串口环境，建议连接真实硬件设备"
            elif not diagnosis_result['data_flow_status']:
                diagnosis_result['overall_status'] = "ERROR"
                diagnosis_result['message'] = "连接正常但无数据流，请检查外部设备"
            else:
                diagnosis_result['overall_status'] = "OK"
                diagnosis_result['message'] = "串口连接和数据流正常"
                
            ser.close()
            
        except serial.SerialException as e:
            diagnosis_result['issues'].append(f"串口通信错误: {str(e)}")
            diagnosis_result['recommendations'].append("检查串口线缆和设备连接")
            logger.error(f"诊断端口 {port} 时发生串口错误: {e}")
        except Exception as e:
            diagnosis_result['issues'].append(f"诊断过程异常: {str(e)}")
            logger.error(f"诊断端口 {port} 时发生未知错误: {e}")
            logger.error(f"详细错误信息: {traceback.format_exc()}")
            
        return diagnosis_result
    
    def _test_loopback_detection(self, ser: serial.Serial) -> Dict:
        """检测是否存在回环（虚拟串口特征）"""
        result = {'detected': False, 'confidence': 0}
        
        try:
            # 发送特殊测试数据
            test_pattern = b"VCP_TEST_" + str(int(time.time())).encode()
            ser.write(test_pattern)
            ser.flush()
            
            # 等待一小段时间让数据传输
            time.sleep(0.1)
            
            # 检查是否立即收到相同数据（回环特征）
            if ser.in_waiting > 0:
                received_data = ser.read(ser.in_waiting)
                if test_pattern in received_data:
                    result['detected'] = True
                    result['confidence'] = 90
                    logger.warning(f"检测到串口回环，很可能是虚拟串口: {ser.port}")
                    
        except Exception as e:
            logger.error(f"回环检测失败: {e}")
            
        return result
    
    def _test_real_data_flow(self, ser: serial.Serial) -> Dict:
        """测试真实数据流"""
        result = {'status': False, 'samples': []}
        
        try:
            # 监听一段时间看是否有外部数据
            observation_time = 5  # 观察5秒
            start_time = time.time()
            
            while time.time() - start_time < observation_time:
                if ser.in_waiting > 0:
                    try:
                        data = ser.readline().decode('utf-8', errors='ignore').strip()
                        if data and not data.startswith("VCP_TEST_"):  # 排除测试数据
                            result['samples'].append({
                                'timestamp': time.time(),
                                'data': data,
                                'length': len(data)
                            })
                            
                            # 如果收集到足够样本，认为有真实数据流
                            if len(result['samples']) >= 3:
                                result['status'] = True
                                logger.info(f"端口 {ser.port} 检测到真实数据流")
                                break
                    except Exception:
                        continue
                        
                time.sleep(0.1)
                
        except Exception as e:
            logger.error(f"数据流测试失败: {e}")
            
        return result
    
    def get_available_ports_with_diagnostics(self) -> List[Dict]:
        """获取所有可用串口及其诊断信息"""
        import serial.tools.list_ports
        
        results = []
        available_ports = list(serial.tools.list_ports.comports())
        
        for port_info in available_ports:
            diagnosis = self.diagnose_port_connection(port_info.device)
            results.append({
                'port': port_info.device,
                'description': port_info.description,
                'diagnosis': diagnosis
            })
            
        return results


# 全局诊断实例
serial_diagnostics = SerialDiagnostics()