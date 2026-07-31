
"""
数据解析模块
负责解析原始数据为风速风向数据
"""

import json
import logging
from datetime import datetime
from typing import Optional
import traceback

from .data_model import WindData


logger = logging.getLogger(__name__)


class DataParser:
    """数据解析类，专门负责解析原始数据为风速风向数据"""
    def __init__(self, port: str):
        self.port = port

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
            try:
                return float(value_str)
            except ValueError as e:
                logger.warning(f"端口 {self.port} 数据转换失败: {value_str}, 错误: {e}")
                return None
        return None

    def _make_wind_data(self, wind_speed, wind_direction,
                        temperature=None, pressure=None, humidity=None) -> Optional[WindData]:
        """统一构造 WindData，出口处做风速范围校验。

        P2-6: P1-3 的风速范围校验（0~100 m/s）此前只作用于"纯数字"分支，
        hex/JSON/CSV/空格分隔格式的设备故障帧（如 999.9 m/s）会绕过校验。
        现将校验前移到统一构造出口，所有格式一律生效。
        P2-14: 额外校验有限性——float('nan') 与任何数的比较恒为 False，
        `speed < 0 or speed > 100` 对 NaN 会整体为 False 而放行，导致
        NaN 风速写入 CSV（nan 字符串）和 JSON（非法 JSON）。inf 虽被
        范围校验拦截，但用 math.isfinite 一并排除更严谨。
        """
        if wind_speed is None:
            return None
        import math
        # P1-3: 风速范围校验 (0~100 m/s)，防止异常值
        if not math.isfinite(wind_speed) or wind_speed < 0 or wind_speed > 100:
            logger.warning(f"端口 {self.port} 风速值异常: {wind_speed} m/s，已忽略")
            return None
        # 风向合理性校验：有效风向应在 0~360 度（NaN 同样会被 isfinite 拦截）
        if wind_direction is not None:
            if not math.isfinite(wind_direction) or wind_direction < 0 or wind_direction > 360:
                logger.warning(f"端口 {self.port} 风向值异常: {wind_direction}°，已忽略该帧")
                return None
        return WindData(
            timestamp=datetime.now().isoformat(),
            port=self.port,
            wind_speed=wind_speed,
            wind_direction=wind_direction if wind_direction is not None else 0.0,
            temperature=temperature,
            pressure=pressure,
            humidity=humidity
        )

    def _parse_hex_wind_data(self, raw_data: str) -> Optional[WindData]:
        """
        解析十六进制格式的风速风向数据
        格式示例: '00.8 222 +07.1 1014.9 +08.0 76 005 0000.0 CE*35'
        其中 '00.8' 是风速，'222' 是风向
        """
        try:
            logger.debug(f"端口 {self.port} 尝试解析十六进制格式数据：{raw_data}")
                        
            # 处理以#开头和结尾的格式
            data_to_parse = raw_data.strip()
            if data_to_parse.startswith('#') and data_to_parse.endswith('#'):
                # 移除首尾的#号
                data_to_parse = data_to_parse[1:-1].strip()
                logger.debug(f"端口 {self.port} 移除#号后的数据：{data_to_parse}")
                        
            # 分割数据，至少需要两个字段
            parts = data_to_parse.strip().split()
            if len(parts) < 2:
                logger.warning(f"端口 {self.port} 数据格式不正确，字段数量不足: {raw_data}")
                return None

            # 提取风速和风向
            wind_speed_str = parts[0]
            wind_direction_str = parts[1]

            # 转换为浮点数
            wind_speed = self._safe_float_conversion(wind_speed_str)
            wind_direction = self._safe_float_conversion(wind_direction_str)

            if wind_speed is None:
                logger.warning(f"端口 {self.port} 数据中风速值无效: {wind_speed_str}")
                return None

            # 提取温度、气压、湿度（如果存在）
            temperature = self._safe_float_conversion(parts[2]) if len(parts) > 2 else None
            pressure = self._safe_float_conversion(parts[3]) if len(parts) > 3 else None
            humidity = self._safe_float_conversion(parts[5]) if len(parts) > 5 else None

            logger.info(f"端口 {self.port} 成功解析数据 - 风速: {wind_speed}, 风向: {wind_direction}, 温度: {temperature}, 气压: {pressure}, 湿度: {humidity}")
            return self._make_wind_data(wind_speed, wind_direction, temperature, pressure, humidity)
        except Exception as e:
            logger.error(f"解析端口 {self.port} 十六进制风速风向数据失败: {raw_data}, 错误: {e}")
            logger.error(f"详细错误信息: {traceback.format_exc()}")
            return None

    def _is_hex_data(self, raw_data: str) -> bool:
        """
        判断数据是否可能是十六进制格式
        """
        try:
            raw_data = raw_data.strip()
            if not raw_data:
                return False
            # P2-7: 十六进制字节长度必须为偶数，且判断应在去除空白后计算。
            # 此前直接对含空格/换行的原始长度取模，空格分隔的 hex 帧
            # （如 "0A 0B 0C 0D\r\n"，总长含空白为奇数）会被误判为非 hex。
            hex_clean = raw_data.replace(' ', '')
            if len(hex_clean) % 2 != 0:
                return False
            # 检查是否包含典型的十六进制字符并且长度合理
            hex_chars = set('0123456789ABCDEFabcdef ')
            data_chars = set(raw_data)
            # 需要有足够的字符，并且只包含十六进制相关的字符
            is_hex = (len(hex_clean) > 10 and
                     data_chars.issubset(hex_chars) and
                     all(c in hex_chars for c in data_chars))
            if is_hex:
                logger.debug(f"端口 {self.port} 数据被识别为十六进制格式: {raw_data[:50]}...")
            return is_hex
        except Exception as e:
            logger.error(f"检查端口 {self.port} 数据是否为十六进制格式时出错: {e}")
            logger.error(f"详细错误信息: {traceback.format_exc()}")
            return False

    def _convert_hex_to_ascii(self, hex_data: str) -> str:
        """
        将十六进制字符串转换为ASCII字符串
        """
        try:
            logger.debug(f"端口 {self.port} 尝试转换十六进制数据为ASCII: {hex_data[:50]}...")
            # 移除空格并确保偶数长度
            hex_clean = hex_data.replace(' ', '')
            if len(hex_clean) % 2 != 0:
                hex_clean = hex_clean[:-1]  # 移除最后一个字符使长度为偶数
                
            # 转换为字节然后解码为ASCII
            bytes_data = bytes.fromhex(hex_clean)
            ascii_str = bytes_data.decode('ascii', errors='ignore')
            logger.debug(f"端口 {self.port} 十六进制转ASCII成功: {ascii_str}")
            return ascii_str.strip()
        except ValueError as e:
            logger.warning(f"端口 {self.port} 十六进制数据格式无效: {e}")
            return hex_data
        except Exception as e:
            logger.error(f"端口 {self.port} 十六进制转ASCII失败: {e}")
            logger.error(f"详细错误信息: {traceback.format_exc()}")
            return hex_data

    def parse_data(self, raw_data: str) -> Optional[WindData]:
        """解析数据"""
        try:
            logger.debug(f"端口 {self.port} 开始解析原始数据: {raw_data}")
            
            # 首先尝试解析十六进制数据
            if self._is_hex_data(raw_data):
                logger.info(f"端口 {self.port} 识别为十六进制格式数据")
                ascii_data = self._convert_hex_to_ascii(raw_data)
                parsed_data = self._parse_hex_wind_data(ascii_data)
                if parsed_data:
                    logger.info(f"端口 {self.port} 十六进制数据解析成功")
                    return parsed_data
                else:
                    logger.warning(f"端口 {self.port} 十六进制数据解析失败")

            # 尝试JSON格式
            if raw_data.startswith('{') and raw_data.endswith('}'):
                logger.info(f"端口 {self.port} 识别为JSON格式数据")
                try:
                    data = json.loads(raw_data)
                    wind_speed = self._safe_float_conversion(str(data.get('wind_speed', 0)))
                    wind_direction = self._safe_float_conversion(str(data.get('wind_direction', 0))) if data.get('wind_direction') is not None else None
                    
                    if wind_speed is None:
                        logger.warning(f"端口 {self.port} JSON数据中风速值无效: {data.get('wind_speed')}")
                        return None
                        
                    logger.info(f"端口 {self.port} JSON格式数据解析成功 - 风速: {wind_speed}, 风向: {wind_direction}")
                    return self._make_wind_data(wind_speed, wind_direction)
                except json.JSONDecodeError as e:
                    logger.warning(f"端口 {self.port} JSON数据格式错误: {e}")
            
            # 尝试CSV格式
            elif ',' in raw_data:
                logger.info(f"端口 {self.port} 识别为CSV格式数据")
                parts = raw_data.split(',')
                wind_speed = self._safe_float_conversion(parts[0]) if len(parts) > 0 else None
                wind_direction = self._safe_float_conversion(parts[1]) if len(parts) > 1 else None

                if wind_speed is None:
                    logger.warning(f"端口 {self.port} CSV数据中风速值无效: {parts[0] if len(parts) > 0 else '无数据'}")
                    return None

                # 提取温度、气压、湿度（如果存在）
                temperature = self._safe_float_conversion(parts[2]) if len(parts) > 2 else None
                pressure = self._safe_float_conversion(parts[3]) if len(parts) > 3 else None
                humidity = self._safe_float_conversion(parts[5]) if len(parts) > 5 else None

                logger.info(f"端口 {self.port} CSV格式数据解析成功 - 风速: {wind_speed}, 风向：{wind_direction}, 温度：{temperature}, 气压：{pressure}, 湿度：{humidity}")
                return self._make_wind_data(wind_speed, wind_direction, temperature, pressure, humidity)

            # 尝试空格分隔格式
            elif ' ' in raw_data:
                logger.info(f"端口 {self.port} 识别为空格分隔格式数据")

                # 处理以#开头和结尾的格式
                data_to_parse = raw_data.strip()
                if data_to_parse.startswith('#') and data_to_parse.endswith('#'):
                    # 移除首尾的#号
                    data_to_parse = data_to_parse[1:-1].strip()
                    logger.debug(f"端口 {self.port} 空格分隔格式移除#号后的数据：{data_to_parse}")

                parts = data_to_parse.split()
                wind_speed = self._safe_float_conversion(parts[0]) if len(parts) > 0 else None
                wind_direction = self._safe_float_conversion(parts[1]) if len(parts) > 1 else None

                if wind_speed is None:
                    logger.warning(f"端口 {self.port} 空格分隔数据中风速值无效：{parts[0] if len(parts) > 0 else '无数据'}")
                    return None

                # 提取温度、气压、湿度（如果存在）
                # 格式: 风速 风向 温度 气压 另一个温度? 湿度 ...
                # 示例: 01.1 112 +29.2 0993.9 +29.4 60 319 0000.0 CE*3B
                temperature = self._safe_float_conversion(parts[2]) if len(parts) > 2 else None
                pressure = self._safe_float_conversion(parts[3]) if len(parts) > 3 else None
                humidity = self._safe_float_conversion(parts[5]) if len(parts) > 5 else None

                logger.info(f"端口 {self.port} 空格分隔格式数据解析成功 - 风速：{wind_speed}, 风向：{wind_direction}, 温度：{temperature}, 气压：{pressure}, 湿度：{humidity}")
                return self._make_wind_data(wind_speed, wind_direction, temperature, pressure, humidity)

            # 尝试纯数字格式
            else:
                logger.info(f"端口 {self.port} 识别为纯数字格式数据")
                wind_speed = self._safe_float_conversion(raw_data)
                if wind_speed is None:
                    logger.warning(f"端口 {self.port} 纯数字数据中风速值无效: {raw_data}")
                    return None

                # P2-14: 改走统一构造出口，范围 + isfinite 校验（NaN/inf 一并拦截）
                logger.info(f"端口 {self.port} 纯数字格式数据解析成功 - 风速: {wind_speed}")
                return self._make_wind_data(wind_speed, 0.0)
                
        except Exception as e:
            logger.error(f"解析端口 {self.port} 数据时发生未知错误: {e}")
            logger.error(f"原始数据: {raw_data}")
            logger.error(f"详细错误信息: {traceback.format_exc()}")
            return None