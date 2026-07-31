"""
数据模型模块
定义风速风向数据结构
"""

from datetime import datetime
from typing import Dict


class WindData:
    """风速风向数据模型类，负责表示风速风向数据结构"""
    def __init__(self, timestamp: str, port: str, wind_speed: float, wind_direction: float,
                 temperature: float = None, pressure: float = None, humidity: float = None):
        self.timestamp = timestamp
        self.port = port
        self.wind_speed = wind_speed
        self.wind_direction = wind_direction
        self.temperature = temperature
        self.pressure = pressure
        self.humidity = humidity

    def to_dict(self) -> dict:
        return {
            'timestamp': self.timestamp,
            'port': self.port,
            'wind_speed': self.wind_speed,
            'wind_direction': self.wind_direction,
            'temperature': self.temperature,
            'pressure': self.pressure,
            'humidity': self.humidity
        }

    @classmethod
    def from_dict(cls, data: dict):
        return cls(
            timestamp=data.get('timestamp'),
            port=data.get('port'),
            wind_speed=data.get('wind_speed'),
            wind_direction=data.get('wind_direction'),
            temperature=data.get('temperature'),
            pressure=data.get('pressure'),
            humidity=data.get('humidity')
        )