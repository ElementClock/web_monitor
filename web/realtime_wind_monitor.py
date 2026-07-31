"""
实时风速监控系统
主程序入口
"""

import logging
import logging.handlers
import sys
import os
import webbrowser
import threading
import atexit
import signal
from datetime import datetime, timedelta
from modules.monitor_manager import WindMonitorManager


# 配置日志
def setup_logging():
    """设置日志配置"""
    # 创建日志格式
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'

    # 日志目录
    log_dir = 'logs'
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    log_file = os.path.join(log_dir, 'wind_monitor.log')

    # 创建按天分割的日志处理器
    file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=log_file,
        when='midnight',      # 每天午夜分割
        interval=1,           # 每天一个文件
        backupCount=30,       # 保留30天日志
        encoding='utf-8',
        delay=False
    )
    file_handler.suffix = '%Y%m%d'  # 分割后的文件名格式: wind_monitor.log.20250614

    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(log_format, datefmt=date_format))

    # 控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(log_format, datefmt=date_format))

    # 配置根日志记录器
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    # 清理过期日志
    _cleanup_old_logs(log_dir, keep_days=30)


def _cleanup_old_logs(log_dir: str, keep_days: int = 30):
    """清理超过保留天数的日志文件"""
    try:
        import glob
        if not os.path.exists(log_dir):
            return

        cutoff_date = datetime.now() - timedelta(days=keep_days)
        log_pattern = os.path.join(log_dir, 'wind_monitor.log.*')

        for log_file in glob.glob(log_pattern):
            try:
                basename = os.path.basename(log_file)
                parts = basename.split('.')
                if len(parts) >= 2:
                    date_part = parts[-1]
                    if len(date_part) == 8 and date_part.isdigit():
                        file_date = datetime.strptime(date_part, '%Y%m%d')
                        if file_date < cutoff_date:
                            os.remove(log_file)
                            logging.info(f"已删除过期日志: {log_file}")
            except Exception:
                pass
    except Exception:
        pass


def merge_logs(log_dir: str = 'logs', output_file: str = None) -> str:
    """
    合并指定目录下的所有日志文件
    返回合并后的文件路径
    """
    import glob

    if not os.path.exists(log_dir):
        return ""

    log_pattern = os.path.join(log_dir, 'wind_monitor.log.*')
    log_files = glob.glob(log_pattern)

    if not log_files:
        # 尝试合并主日志文件
        main_log = os.path.join(log_dir, 'wind_monitor.log')
        if os.path.exists(main_log):
            log_files = [main_log]

    if not log_files:
        return ""

    # 按日期排序
    def extract_date(f):
        basename = os.path.basename(f)
        parts = basename.split('.')
        if len(parts) >= 2:
            date_str = parts[-1]
            if len(date_str) == 8 and date_str.isdigit():
                return date_str
        return "00000000"

    log_files.sort(key=extract_date)

    if output_file is None:
        output_file = os.path.join(log_dir, f"merged_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

    with open(output_file, 'w', encoding='utf-8') as outfile:
        outfile.write(f"# 合并日志 - 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        outfile.write(f"# 源文件: {len(log_files)} 个\n")
        outfile.write(f"# {'='*60}\n\n")

        for log_file in log_files:
            try:
                outfile.write(f"\n# ===== {os.path.basename(log_file)} =====\n")
                with open(log_file, 'r', encoding='utf-8', errors='ignore') as infile:
                    outfile.write(infile.read())
            except Exception as e:
                logging.warning(f"读取日志失败 {log_file}: {e}")

    logging.info(f"日志合并完成: {output_file}")
    return output_file


def open_browser():
    """在新线程中打开浏览器，延迟2秒以确保服务器已启动"""
    def delayed_open():
        import time
        time.sleep(2)
        webbrowser.open('http://127.0.0.1:5000')
        logger = logging.getLogger(__name__)
        logger.info("已自动打开浏览器访问 http://127.0.0.1:5000")

    thread = threading.Thread(target=delayed_open)
    thread.daemon = True
    thread.start()


_cleanup_state = {'monitor': None}


def _cleanup():
    """atexit 兜底清理"""
    monitor = _cleanup_state['monitor']
    if monitor:
        try:
            logger = logging.getLogger(__name__)
            logger.info("atexit 触发: 执行兜底清理")
            monitor.stop_all_readers()
        except Exception:
            pass


def _signal_handler(signum, frame):
    """信号处理器"""
    logger = logging.getLogger(__name__)
    logger.info(f"收到信号 {signum}，正在关闭程序...")
    sys.exit(0)


def main():
    """主函数"""
    setup_logging()

    logger = logging.getLogger(__name__)
    logger.info("="*50)
    logger.info("开始启动风速监控系统")
    logger.info(f"当前工作目录: {os.getcwd()}")
    logger.info(f"Python版本: {sys.version}")
    logger.info(f"系统平台: {sys.platform}")

    wind_monitor = None

    atexit.register(_cleanup)
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        logger.info("创建监控管理器实例")
        wind_monitor = WindMonitorManager()
        _cleanup_state['monitor'] = wind_monitor

        logger.info("启动Web服务器")
        open_browser()
        wind_monitor.start_server(host='127.0.0.1', port=5000)

    except KeyboardInterrupt:
        logger.info("收到键盘中断信号，正在关闭程序...")
    except SystemExit:
        logger.info("收到系统退出信号")
    except Exception as e:
        logger.error(f"程序运行时发生未处理的异常: {e}")
        logger.error(f"详细错误信息: {str(e)}")
        import traceback
        logger.error(f"完整堆栈跟踪:\n{traceback.format_exc()}")
    finally:
        logger.info("程序关闭，清理资源")
        if wind_monitor:
            logger.info("正在停止所有读取器并保存数据...")
            wind_monitor.stop_all_readers()
            logger.info("所有资源已清理完成")
        logger.info("="*50)


if __name__ == '__main__':
    main()