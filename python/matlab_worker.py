# python/matlab_worker.py
import sys
import threading
import time
from collections import deque
from PyQt5.QtCore import QThread, pyqtSignal, QTimer, QObject

class MatlabWorkerThread(QThread):
    """MATLAB计算工作线程，独立于主UI线程运行"""

    # 信号定义
    hr_ready = pyqtSignal(float, float, bool)  # HR_HF, HR_ACC, is_motion
    error_occurred = pyqtSignal(str)
    status_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.eng = None              # MATLAB Engine实例
        self.solver = None           # OnlineHeartRateSolver实例
        self.data_buffer = None      # 数据缓存引用
        self.data_lock = None        # 线程锁
        self.is_calculating = False  # 计算状态标志
        self.timeout_count = 0       # 超时计数器
        self.current_scenario = ''   # 当前场景名

    def run(self):
        """线程主循环"""
        # 启动计算定时器
        self.timer = QTimer()
        self.timer.timeout.connect(self._process_step)
        self.timer.start(1000)  # 每1秒触发一次
        self.exec()

    def set_data_buffer(self, buffer: deque, lock: threading.Lock):
        """设置数据缓存引用"""
        self.data_buffer = buffer
        self.data_lock = lock

    def start_calculation(self):
        """启动计算"""
        self.is_calculating = True
        self.status_changed.emit("MATLAB计算已启动")

    def stop_calculation(self):
        """停止计算"""
        self.is_calculating = False
        self.status_changed.emit("MATLAB计算已停止")