# -*- coding: utf-8 -*-
"""
P2 修复回归测试
验证本轮修复的关键行为：
  1. 串口硬错误（设备移除）即关闭失效句柄，使 is_open 翻 False，触发重连
  2. disconnect 幂等：连续调用不抛异常、不二次关闭
  3. 数据解析：风速范围校验前移到所有格式（hex/JSON/CSV/空格分隔）
  4. hex 检测边界：空格分隔 hex 帧不再被误判
无需硬件，直接构造实例 + 桩对象。

运行: ../venv/Scripts/python.exe test_regressions.py
"""
import sys
import os
import io
import logging

# 将 web 目录加入路径（test 位于 web/tests 下）
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_THIS_DIR, os.pardir))


# ---------- 桩对象 ----------
class FakeSerialConn:
    """模拟 pyserial 句柄：可选在 read 时抛硬错误"""
    def __init__(self, fail_read=False, in_waiting_val=0):
        self.is_open = True
        self.fail_read = fail_read
        self.timeout = 1
        self.closed = False
        self._in_waiting = in_waiting_val

    def read(self, n):
        if self.fail_read:
            raise OSError("设备未正常工作 (winerror 31)")
        return b''

    def write(self, data):
        if self.fail_read:
            raise OSError("设备未正常工作 (winerror 31)")
        return len(data)

    def flush(self):
        pass

    def close(self):
        self.is_open = False
        self.closed = True

    @property
    def in_waiting(self):
        return self._in_waiting


class StubParser:
    def parse_data(self, raw_data):
        data = raw_data.strip()
        if data.startswith('#') and data.endswith('#'):
            data = data[1:-1].strip()
        parts = data.split()
        if len(parts) >= 2:
            try:
                speed = float(parts[0])
            except ValueError:
                return None
            from modules.data_model import WindData
            return WindData(timestamp="t", port="TEST", wind_speed=speed,
                            wind_direction=float(parts[1]))
        return None


class StubStorage:
    def __init__(self):
        self.data_buffer = []
        self.rows = []
        self.closed = 0

    def append_data(self, obj):
        self.data_buffer.append(obj)

    def write_data_to_file(self, obj):
        self.rows.append(obj)

    def save_and_close_data_file(self):
        self.closed += 1

    def get_latest_data(self):
        return self.data_buffer[-1] if self.data_buffer else None


# ---------- 测试 1: 串口硬错误即关句柄（拔线重连前提） ----------
def test_hard_error_invalidates_connection():
    from modules.serial_communicator import SerialCommunicator

    comm = SerialCommunicator("TEST", 9600)
    comm.serial_conn = FakeSerialConn(fail_read=True, in_waiting_val=10)

    # read_line 遇到硬错误后应关闭句柄、返回 None
    result = comm.read_line()
    assert result is None, "硬错误时应返回 None"
    assert comm.serial_conn is None, "硬错误后句柄应被置 None（触发重连）"
    # is_connected 现在应为 False（上层重连门控生效）
    assert comm.is_connected() is False, "is_open 已翻 False，is_connected 应为 False"
    print("✓ 硬错误即关句柄，触发重连门控")


def test_hard_error_on_send_invalidates_connection():
    from modules.serial_communicator import SerialCommunicator

    comm = SerialCommunicator("TEST", 9600)
    comm.serial_conn = FakeSerialConn(fail_read=True)

    result = comm.send_command(b"\x00")
    assert result is False, "硬错误时发送应返回 False"
    assert comm.serial_conn is None, "发送硬错误后句柄应被置 None"
    print("✓ 发送命令硬错误即关句柄")


def test_timeout_does_not_invalidate_connection():
    """软超时不应关闭句柄（设备只是暂时无响应）"""
    from serial import SerialTimeoutException
    from modules.serial_communicator import SerialCommunicator

    comm = SerialCommunicator("TEST", 9600)
    conn = FakeSerialConn(fail_read=False)

    class TimeoutConn(FakeSerialConn):
        def read(self, n):
            raise SerialTimeoutException("timeout")

    comm.serial_conn = TimeoutConn(fail_read=False, in_waiting_val=10)
    result = comm.read_line()
    assert result is None
    assert comm.serial_conn is not None, "软超时不应关闭句柄"
    print("✓ 软超时保留句柄")


# ---------- 测试 2: disconnect 幂等 ----------
def test_disconnect_idempotent():
    from modules.serial_reader import SerialWindDataReader
    import threading

    r = SerialWindDataReader.__new__(SerialWindDataReader)
    r.port = "TEST"
    r.parser = StubParser()
    r.storage = StubStorage()
    r.communicator = SerialCommunicatorStub()
    r.is_running = False
    r.on_data_callback = None
    r.data_buffer = ""
    r.stop_event = threading.Event()
    r._disconnected = False
    r.command_timer_thread = None
    r._read_thread = None
    r._reconnect_thread = None

    # 连续两次 disconnect 不应抛异常、不应重复关闭
    r.disconnect()
    r.disconnect()
    assert r.storage.closed == 1, "数据文件应只关闭一次（幂等）"
    print("✓ disconnect 幂等")


class SerialCommunicatorStub:
    """minimal communicator stub: disconnect 无副作用"""
    def __init__(self):
        self.disconnects = 0

    def disconnect(self):
        self.disconnects += 1


# ---------- 测试 3: 范围校验前移到所有格式 ----------
def test_speed_range_validation_all_formats():
    from modules.data_parser import DataParser

    parser = DataParser("TEST")

    # 空格分隔格式（此前绕过范围校验）
    bad_space = parser.parse_data("999.9 045 001.0 1013.2 55")
    assert bad_space is None, "空格分隔格式的风速超界应被拒绝"
    good_space = parser.parse_data("12.3 045 001.0 1013.2 55")
    assert good_space is not None and good_space.wind_speed == 12.3

    # CSV 格式
    bad_csv = parser.parse_data("999.9,045")
    assert bad_csv is None, "CSV 格式的风速超界应被拒绝"
    good_csv = parser.parse_data("12.3,045")
    assert good_csv is not None and good_csv.wind_speed == 12.3

    # JSON 格式
    bad_json = parser.parse_data('{"wind_speed": 999.9, "wind_direction": 45}')
    assert bad_json is None, "JSON 格式的风速超界应被拒绝"
    good_json = parser.parse_data('{"wind_speed": 12.3, "wind_direction": 45}')
    assert good_json is not None and good_json.wind_speed == 12.3

    # 纯数字格式（原本就有校验）
    assert parser.parse_data("999.9") is None
    assert parser.parse_data("12.3") is not None

    print("✓ 风速范围校验覆盖所有格式")


def test_nan_and_direction_validation():
    """P2-14: NaN 穿透范围校验 + 风向合理性校验"""
    from modules.data_parser import DataParser

    parser = DataParser("TEST")

    # NaN 风速：float('nan') 与任何数比较恒 False，此前会绕过 0~100 校验入库
    assert parser.parse_data("nan 045 001.0 1013.2 55") is None, "NaN 风速应被拒绝"
    assert parser.parse_data('{"wind_speed": "nan", "wind_direction": 45}') is None, "JSON NaN 风速应被拒绝"
    assert parser.parse_data("nan") is None, "纯数字 NaN 应被拒绝"

    # inf（此前已被范围校验拦截，保持拒绝）
    assert parser.parse_data("inf 045") is None
    assert parser.parse_data("-inf 045") is None

    # 风向合理性：0~360 度
    assert parser.parse_data("12.3 500") is None, "风向 >360 应被拒绝"
    assert parser.parse_data("12.3 -45") is None, "风向 <0 应被拒绝"
    assert parser.parse_data("12.3 360") is not None, "风向 360 合法"
    assert parser.parse_data("12.3 0") is not None, "风向 0 合法"

    print("✓ NaN/风向校验")


# ---------- 测试 4: hex 检测边界 ----------
def test_hex_detection_whitespace():
    from modules.data_parser import DataParser

    parser = DataParser("TEST")

    # 空格分隔 hex 帧：总长含空格为奇数，此前被误判为非 hex
    # "0A 0B 0C 0D 0E 0F" -> 去空格后 "0A0B0C0D0E0F" (12 字节，偶数)，应识别为 hex
    hex_space = parser._is_hex_data("0A 0B 0C 0D 0E 0F")
    assert hex_space is True, "去空格后为偶数的 hex 应被识别"

    # 去空格后为奇数长度 → 不是合法 hex（偶数长度要求）
    hex_odd = parser._is_hex_data("0A 0B 0C 0D 0E 0F 0")
    assert hex_odd is False, "去空格后奇数长度的 hex 不应被识别"

    # 普通文本不应误判为 hex
    assert parser._is_hex_data("12.3 045 001.0 1013.2 55") is False, "空格分隔数值帧不应被识别为 hex"
    print("✓ hex 检测边界修复")


# ---------- 测试 5: 历史数据读取（read_history_data） ----------
def test_read_history_data():
    import codecs
    import csv as _csv
    import os
    import tempfile
    from datetime import datetime, timedelta
    from modules.data_storage import read_history_data

    now = datetime.now()
    with tempfile.TemporaryDirectory() as tmpdir:
        # 构造含中文表头的 CSV（utf-8-sig，带 BOM），时间戳为当前格式 %Y-%m-%d %H:%M:%S
        # P3-7: 文件名日期须用动态 today（此前硬编码 20260801，运行日期晚于该日时
        # read_history_data 的文件名预过滤会把它排除，导致测试恒失败）
        path = os.path.join(tmpdir, f"wind_data_3_{now.strftime('%Y%m%d')}.csv")
        with codecs.open(path, 'w', encoding='utf-8-sig') as f:
            f.write('时间,端口,风速,风向,温度,气压,湿度\n')
            # 窗口内（最近 10 分钟内）
            f.write(f"{now.strftime('%Y-%m-%d %H:%M:%S')},COM3,3.2,180.0,25.0,1013.2,60.0\n")
            f.write(f"{(now - timedelta(minutes=2)).strftime('%Y-%m-%d %H:%M:%S')},COM3,2.5,90.0,24.8,1013.0,61.0\n")
            # 窗口外（60 分钟前，超出 minutes=10 窗口）
            f.write(f"{(now - timedelta(minutes=60)).strftime('%Y-%m-%d %H:%M:%S')},COM3,9.9,0.0,24.0,1012.0,60.0\n")
            # 缺风速字段 → 应被跳过
            f.write(f"{now.strftime('%Y-%m-%d %H:%M:%S')},COM3,,180.0,25.0,1013.2,60.0\n")
            # 无法解析的时间戳行 → 应被跳过
            f.write("not-a-date,COM3,5.0,180.0,25.0,1013.2,60.0\n")

        points = read_history_data('COM3', minutes=10, data_dir=tmpdir)

        # 只返回窗口内的 2 行（缺风速行与坏时间戳行被跳过）
        assert len(points) == 2, f"期望 2 行窗口内数据，实际 {len(points)}: {points}"
        # 升序
        times = [p['timestamp'] for p in points]
        assert times == sorted(times), f"应升序返回: {times}"
        # 字段完整性与值
        assert points[0]['wind_speed'] == 2.5
        assert points[1]['wind_speed'] == 3.2
        assert points[1]['wind_direction'] == 180.0
        assert points[1]['temperature'] == 25.0
        assert points[1]['pressure'] == 1013.2
        assert points[1]['humidity'] == 60.0
        assert 'port' not in points[1], "返回字段不应包含 port"

        # 无数据文件 → 空列表
        assert read_history_data('COM999', minutes=10, data_dir=tmpdir) == []

    print("✓ 历史数据读取（read_history_data）")


if __name__ == "__main__":
    # Windows 控制台默认 GBK，强制 UTF-8 输出以支持 ✓ 等字符
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    logging.basicConfig(level=logging.ERROR)
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        passed += 1
    print(f"\n全部 {passed} 项测试通过")
