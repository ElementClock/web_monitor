"""
串口配置管理模块
负责管理和验证串口通信参数，防止参数不匹配导致的通信问题
"""

import logging
import json
import os
import sys
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
import traceback

logger = logging.getLogger(__name__)


@dataclass
class SerialConfig:
    """串口配置数据类"""
    port: str
    baudrate: int
    bytesize: int
    parity: str
    stopbits: int
    timeout: float = 1.0
    name: str = ""
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict):
        return cls(**data)


class SerialConfigValidator:
    """串口配置验证器"""
    
    # 标准波特率列表
    STANDARD_BAUDRATES = [
        110, 300, 600, 1200, 2400, 4800, 9600, 14400, 
        19200, 38400, 57600, 115200, 128000, 256000
    ]
    
    # 有效的校验位选项
    VALID_PARITY = ['N', 'E', 'O', 'M', 'S']
    
    # 有效的数据位选项
    VALID_BYTESIZE = [5, 6, 7, 8]
    
    # 有效的停止位选项
    VALID_STOPBITS = [1, 1.5, 2]
    
    @classmethod
    def validate_config(cls, config: SerialConfig) -> Tuple[bool, List[str]]:
        """
        验证串口配置的有效性
        返回: (是否有效, 错误信息列表)
        """
        errors = []
        
        # 验证端口
        if not config.port or not isinstance(config.port, str):
            errors.append("端口名称不能为空且必须是字符串")
        
        # 验证波特率
        if config.baudrate not in cls.STANDARD_BAUDRATES:
            errors.append(f"波特率 {config.baudrate} 不在标准范围内，建议使用: {cls.STANDARD_BAUDRATES}")
        
        # 验证数据位
        if config.bytesize not in cls.VALID_BYTESIZE:
            errors.append(f"数据位 {config.bytesize} 无效，有效值为: {cls.VALID_BYTESIZE}")
        
        # 验证校验位
        if config.parity not in cls.VALID_PARITY:
            errors.append(f"校验位 '{config.parity}' 无效，有效值为: {cls.VALID_PARITY}")
        
        # 验证停止位
        if config.stopbits not in cls.VALID_STOPBITS:
            errors.append(f"停止位 {config.stopbits} 无效，有效值为: {cls.VALID_STOPBITS}")
        
        # 验证超时设置
        if config.timeout <= 0:
            errors.append("超时时间必须大于0")
        
        is_valid = len(errors) == 0
        if is_valid:
            logger.info(f"串口配置验证通过: {config.port}")
        else:
            logger.warning(f"串口配置验证失败: {config.port}, 错误: {errors}")
            
        return is_valid, errors


class SerialConfigManager:
    """串口配置管理器"""
    
    def __init__(self, config_file: str = None):
        # 兼容 PyInstaller 打包和从任意工作目录启动
        # 不依赖 os.getcwd()，避免运行时在根目录自动创建副本
        if config_file is None:
            if getattr(sys, 'frozen', False):
                base_path = sys._MEIPASS
            else:
                base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            config_file = os.path.join(base_path, "serial_configs.json")
        self.config_file = config_file
        self.configs: Dict[str, SerialConfig] = {}
        self.validator = SerialConfigValidator()
        self.load_configs()
        
    def load_configs(self):
        """从文件加载配置"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for port, config_data in data.items():
                        self.configs[port] = SerialConfig.from_dict(config_data)
                logger.info(f"成功加载 {len(self.configs)} 个串口配置")
            else:
                logger.info("配置文件不存在，使用默认配置")
                self.create_default_configs()
        except json.JSONDecodeError as e:
            # P1-5: JSON格式错误时记录错误并备份损坏文件
            logger.error(f"配置文件 {self.config_file} JSON 格式错误: {e}")
            try:
                import shutil
                backup_path = self.config_file + ".corrupt"
                shutil.copy2(self.config_file, backup_path)
                logger.warning(f"已损坏的配置文件已备份至: {backup_path}")
            except Exception:
                pass
            self.create_default_configs()
        except Exception as e:
            logger.error(f"加载配置文件失败: {e}")
            logger.error(f"详细错误信息: {traceback.format_exc()}")
            self.create_default_configs()
    
    def save_configs(self):
        """保存配置到文件（原子写入：先写临时文件再原子替换）"""
        try:
            import tempfile
            config_dict = {}
            for port, config in self.configs.items():
                config_dict[port] = config.to_dict()

            # P1-6: 先写临时文件再原子替换，避免写入过程中崩溃导致配置损坏
            target_dir = os.path.dirname(os.path.abspath(self.config_file))
            tmp_fd, tmp_path = tempfile.mkstemp(dir=target_dir, suffix='.tmp')
            try:
                with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
                    json.dump(config_dict, f, indent=2, ensure_ascii=False)
                os.replace(tmp_path, self.config_file)
                logger.info(f"配置已保存到 {self.config_file}")
            except Exception:
                if os.path.exists(tmp_path):
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass
                raise
        except Exception as e:
            logger.error(f"保存配置文件失败: {e}")
            logger.error(f"详细错误信息: {traceback.format_exc()}")
    
    def create_default_configs(self):
        """创建默认配置"""
        # 常见的串口配置预设
        default_presets = [
            SerialConfig("COM1", 9600, 8, 'N', 1, name="默认配置1"),
            SerialConfig("COM2", 115200, 8, 'N', 1, name="高速配置"),
            SerialConfig("COM3", 9600, 8, 'E', 1, name="带校验配置"),
            SerialConfig("/dev/ttyUSB0", 9600, 8, 'N', 1, name="Linux USB串口"),
        ]
        
        for config in default_presets:
            self.configs[config.port] = config
            
        self.save_configs()
        logger.info("已创建默认串口配置")
    
    def add_config(self, config: SerialConfig) -> Tuple[bool, List[str]]:
        """添加新的串口配置"""
        # 验证配置
        is_valid, errors = self.validator.validate_config(config)
        if not is_valid:
            return False, errors
        
        # 检查是否已存在
        if config.port in self.configs:
            errors.append(f"端口 {config.port} 的配置已存在")
            return False, errors
        
        self.configs[config.port] = config
        self.save_configs()
        logger.info(f"已添加串口配置: {config.port}")
        return True, []
    
    def update_config(self, config: SerialConfig) -> Tuple[bool, List[str]]:
        """更新串口配置"""
        # 验证配置
        is_valid, errors = self.validator.validate_config(config)
        if not is_valid:
            return False, errors
        
        # 检查是否存在
        if config.port not in self.configs:
            errors.append(f"端口 {config.port} 的配置不存在")
            return False, errors
        
        self.configs[config.port] = config
        self.save_configs()
        logger.info(f"已更新串口配置: {config.port}")
        return True, []
    
    def remove_config(self, port: str) -> bool:
        """删除串口配置"""
        if port in self.configs:
            del self.configs[port]
            self.save_configs()
            logger.info(f"已删除串口配置: {port}")
            return True
        else:
            logger.warning(f"尝试删除不存在的串口配置: {port}")
            return False
    
    def get_config(self, port: str) -> Optional[SerialConfig]:
        """获取指定端口的配置"""
        return self.configs.get(port)
    
    def get_all_configs(self) -> Dict[str, SerialConfig]:
        """获取所有配置"""
        return self.configs.copy()
    
    def get_config_names(self) -> List[str]:
        """获取所有配置的端口名称"""
        return list(self.configs.keys())
    
    def validate_existing_config(self, port: str) -> Tuple[bool, List[str]]:
        """验证现有配置的有效性"""
        config = self.get_config(port)
        if not config:
            return False, [f"端口 {port} 的配置不存在"]
        
        return self.validator.validate_config(config)
    
    def get_recommended_configs(self) -> List[SerialConfig]:
        """获取推荐的常用配置"""
        return [
            SerialConfig("COM1", 9600, 8, 'N', 1, name="标准配置"),
            SerialConfig("COM2", 115200, 8, 'N', 1, name="高速配置"),
            SerialConfig("COM3", 57600, 8, 'E', 1, name="校验配置"),
            SerialConfig("/dev/ttyUSB0", 9600, 8, 'N', 1, name="USB转串口"),
        ]


# 全局配置管理器实例
config_manager = SerialConfigManager()


class SerialParameterHelper:
    """串口参数助手类"""
    
    @staticmethod
    def get_baudrate_options() -> List[int]:
        """获取可用的波特率选项"""
        return SerialConfigValidator.STANDARD_BAUDRATES
    
    @staticmethod
    def get_parity_options() -> List[str]:
        """获取可用的校验位选项"""
        return [(p, SerialParameterHelper.get_parity_description(p)) 
                for p in SerialConfigValidator.VALID_PARITY]
    
    @staticmethod
    def get_parity_description(parity: str) -> str:
        """获取校验位描述"""
        descriptions = {
            'N': '无校验',
            'E': '偶校验',
            'O': '奇校验',
            'M': '标志校验',
            'S': '空校验'
        }
        return descriptions.get(parity, '未知')
    
    @staticmethod
    def get_bytesize_options() -> List[Tuple[int, str]]:
        """获取数据位选项"""
        return [(b, f"{b} 位") for b in SerialConfigValidator.VALID_BYTESIZE]
    
    @staticmethod
    def get_stopbits_options() -> List[Tuple[float, str]]:
        """获取停止位选项"""
        return [(s, f"{s} 位") for s in SerialConfigValidator.VALID_STOPBITS]
    
    @staticmethod
    def format_config_display(config: SerialConfig) -> str:
        """格式化配置显示"""
        parity_desc = SerialParameterHelper.get_parity_description(config.parity)
        return (f"{config.port} - {config.baudrate}bps, "
                f"{config.bytesize}数据位, {parity_desc}, "
                f"{config.stopbits}停止位")


# 全局参数助手实例
param_helper = SerialParameterHelper()