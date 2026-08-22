# -*- coding: utf-8 -*-
"""
分片数据重组回归测试
模拟网络/串口拥塞导致的数据分片到达场景，验证 _reassemble_frames
能把跨多次读取到达的半帧数据正确重组并记录。
无需硬件，直接构造 SerialWindDataReader 实例，替换其依赖为桩对象。

运行: ../venv/Scripts/python.exe test_reassembly.py
"""
import sys
import os
import io
import logging

# 将 web 目录加入路径（test 位于 web/tests 下）
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_THIS_DIR, os.pardir))


# ---------- 桩对象 ----------
class StubParser:
    def parse_data(self, raw_data):
        # 与真实 DataParser 一致：处理 #...# 定界后按空格分隔解析
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

    def append_data(self, obj):
        self.data_buffer.append(obj)

    def write_data_to_file(self, obj):
        self.rows.append(obj)

    def save_and_close_data_file(self):
        pass

    def get_latest_data(self):
        return self.data_buffer[-1] if self.data_buffer else None


class StubCommunicator:
    def is_connected(self, quick_check=True):
        return True

    def get_port_status(self):
        return True


from modules.serial_reader import SerialWindDataReader, MAX_FRAME_LENGTH


def make_reader():
    r = SerialWindDataReader.__new__(SerialWindDataReader)
    r.port = "TEST"
    r.parser = StubParser()
    r.storage = StubStorage()
    r.communicator = StubCommunicator()
    r.is_running = False
    r.on_data_callback = None
    r.data_buffer = ""
    import threading
    r._buffer_lock = threading.Lock()
    r._stats_lock = threading.Lock()
    r.stats = {
        'read_chunks': 0,
        'reassembled_frames': 0,
        'discarded_frames': 0,
        'buffer_truncations': 0,
        'last_reassembly': None,
    }
    return r


# ---------- 测试 ----------
def test_split_hash_frame():
    """场景1: #...# 帧分片到达（上半截+下半截）"""
    r = make_reader()
    # 上半截
    r.data_buffer += "#12.3 045"
    with r._buffer_lock:
        r._reassemble_frames()
    assert r.data_buffer == "#12.3 045", "未到齐的帧应留在缓冲区等待"
    # 下半截
    r.data_buffer += " 100.0 0011.5 60#"
    with r._buffer_lock:
        r._reassemble_frames()
    assert r.data_buffer == "", "重组后缓冲区应清空"
    assert len(r.storage.data_buffer) == 1, "应重组出一帧数据"
    assert r.storage.data_buffer[0].wind_speed == 12.3
    assert r.storage.data_buffer[0].wind_direction == 45.0
    print("✓ 分片 #...# 帧重组成功")


def test_split_line_frame():
    """场景2: 换行定界帧分片到达（上半截+下半截）"""
    r = make_reader()
    r.data_buffer += "12.3 045 001.0 1013.2 55"
    with r._buffer_lock:
        r._reassemble_frames()
    assert r.data_buffer == "12.3 045 001.0 1013.2 55", "未到行尾的帧应留在缓冲区"
    r.data_buffer += " 66 000 0000.0 CE*3B\r\n"
    with r._buffer_lock:
        r._reassemble_frames()
    assert r.data_buffer == ""
    assert len(r.storage.data_buffer) == 1
    assert r.storage.data_buffer[0].wind_speed == 12.3
    print("✓ 分片换行帧重组成功")


def test_multiple_chunks_one_reader():
    """场景3: 一行数据被切成3块到达"""
    r = make_reader()
    for chunk in ["12.3 0", "45 001.0 1013", ".2 55\r\n"]:
        r.data_buffer += chunk
        with r._buffer_lock:
            r._reassemble_frames()
    assert r.data_buffer == ""
    assert len(r.storage.data_buffer) == 1
    assert r.storage.data_buffer[0].wind_speed == 12.3
    print("✓ 3块分片重组成功")


def test_multiple_frames_one_chunk():
    """场景4: 一次读到多帧（含换行）"""
    r = make_reader()
    r.data_buffer += "11.1 001\r\n22.2 002\r\n"
    with r._buffer_lock:
        r._reassemble_frames()
    assert r.data_buffer == ""
    assert len(r.storage.data_buffer) == 2
    print("✓ 单块多帧处理成功")


def test_incomplete_frame_waits():
    """场景5: 只有起始#没有结束#，等待后到数据"""
    r = make_reader()
    r.data_buffer += "#33.3 090 0000.0"
    with r._buffer_lock:
        r._reassemble_frames()
    assert r.data_buffer == "#33.3 090 0000.0"
    assert len(r.storage.data_buffer) == 0, "未到齐不应产生数据"
    # 数据补齐后
    r.data_buffer += " 0020.5 45#"
    with r._buffer_lock:
        r._reassemble_frames()
    assert len(r.storage.data_buffer) == 1
    assert r.storage.data_buffer[0].wind_speed == 33.3
    print("✓ 不完整帧等待后到数据")


def test_garbage_before_hash_discarded():
    """场景6: # 之前有垃圾数据，应丢弃但保留有效帧"""
    r = make_reader()
    r.data_buffer += "junk%^&*#44.4 120#"
    with r._buffer_lock:
        r._reassemble_frames()
    assert r.data_buffer == ""
    assert len(r.storage.data_buffer) == 1
    assert r.storage.data_buffer[0].wind_speed == 44.4
    assert r.stats['discarded_frames'] >= 1
    print("✓ # 前垃圾数据被丢弃")


def test_oversized_frame_truncated():
    """场景7: 无结束#的超长帧应被截断，避免无限增长"""
    r = make_reader()
    # 构造超过 MAX_FRAME_LENGTH 的帧（超出上限，触发截断）
    r.data_buffer = "#" + ("0" * MAX_FRAME_LENGTH)
    assert len(r.data_buffer) > MAX_FRAME_LENGTH
    with r._buffer_lock:
        r._reassemble_frames()
    # 截断发生：丢弃 # 起始，剩余部分等待后续数据
    assert r.stats['buffer_truncations'] >= 1
    assert len(r.data_buffer) <= MAX_FRAME_LENGTH, "截断后缓冲必须被压缩到上限内"
    # 模拟后续继续读到数据：残余脏数据最终被无定界符清理
    r.data_buffer += "x"
    with r._buffer_lock:
        r._reassemble_frames()
    assert r.data_buffer == "", "残余脏数据应被清理"
    print("✓ 超长帧被截断")


def test_no_delimiter_garbage_cleared():
    """场景8: 无任何定界符的脏数据超长后整体丢弃"""
    r = make_reader()
    r.data_buffer = "x" * (MAX_FRAME_LENGTH + 1)
    with r._buffer_lock:
        r._reassemble_frames()
    assert r.data_buffer == ""
    assert r.stats['buffer_truncations'] >= 1
    print("✓ 无定界符脏数据被清除")


def test_combined_fragmented():
    """场景9: 连续多帧全部经历分片（网络持续拥塞）"""
    r = make_reader()
    frames = ["55.5 010", "66.6 020", "77.7 030"]
    # 每帧分两半，中间夹一个空读取周期
    for f in frames:
        mid = len(f) // 2
        r.data_buffer += f[:mid]
        with r._buffer_lock:
            r._reassemble_frames()
        r.data_buffer += f[mid:] + "\r\n"
        with r._buffer_lock:
            r._reassemble_frames()
    assert r.data_buffer == ""
    assert len(r.storage.data_buffer) == 3
    speeds = [d.wind_speed for d in r.storage.data_buffer]
    assert speeds == [55.5, 66.6, 77.7]
    print("✓ 持续拥塞下的多帧重组成功")


def test_split_cr_line_frame():
    """场景10: 纯 \r 定界帧分片到达（此前只识别 \n，会积压到超限被丢弃）"""
    r = make_reader()
    r.data_buffer += "12.3 045 001.0 1013.2 55"
    with r._buffer_lock:
        r._reassemble_frames()
    assert r.data_buffer == "12.3 045 001.0 1013.2 55", "未到行尾(\r)的帧应留在缓冲区"
    r.data_buffer += " 66 000 0000.0 CE*3B\r"
    with r._buffer_lock:
        r._reassemble_frames()
    assert r.data_buffer == ""
    assert len(r.storage.data_buffer) == 1
    assert r.storage.data_buffer[0].wind_speed == 12.3
    print("✓ 纯 \\r 定界分片重组成功")


def test_crlf_and_cr_mixed():
    """场景11: \r\n 与 \r 混合定界，不应因 \r\n 连排产生空帧计数"""
    r = make_reader()
    r.data_buffer += "11.1 001\r\n22.2 002\r"
    with r._buffer_lock:
        r._reassemble_frames()
    assert r.data_buffer == ""
    assert len(r.storage.data_buffer) == 2
    assert r.storage.data_buffer[0].wind_speed == 11.1
    assert r.storage.data_buffer[1].wind_speed == 22.2
    assert r.stats['discarded_frames'] == 0, "\\r\\n 连排不应计入空帧丢弃"
    print("✓ \\r\\n 与 \\r 混合定界无空帧")


def test_hash_frame_with_crlf():
    """场景12: #...# 帧带 \r\n 尾部（\r 优先切分，strip 已处理）"""
    r = make_reader()
    r.data_buffer += "#33.3 090 0000.0 0020.5 45#\r\n"
    with r._buffer_lock:
        r._reassemble_frames()
    assert r.data_buffer == ""
    assert len(r.storage.data_buffer) == 1
    assert r.storage.data_buffer[0].wind_speed == 33.3
    print("✓ #...# 帧带 \\r\\n 尾部重组成功")


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
