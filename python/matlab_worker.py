# python/matlab_worker.py
import sys
import threading
import time
from collections import deque
from PyQt5.QtCore import QThread, pyqtSignal, QTimer, QObject

# MATLAB Engine导入（延迟导入，避免在未安装时阻塞程序启动）
try:
    import matlab.engine
except ImportError:
    pass

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

    def init_solver(self, scenario_name: str):
        """
        初始化MATLAB求解器

        Args:
            scenario_name: 场景名称 (tiaosheng, bobi, kaihe)

        Raises:
            ImportError: MATLAB Engine未安装
            Exception: MATLAB启动或求解器初始化失败
        """
        try:
            import matlab.engine
        except ImportError:
            raise ImportError(
                "MATLAB Engine API未安装。\n\n"
                "请按以下步骤安装：\n"
                "1. 打开MATLAB R2021b\n"
                "2. 执行: cd('matlabroot/extern/engines/python')\n"
                "3. 执行: system('python setup.py install')"
            )

        try:
            # 启动MATLAB Engine
            if self.eng is None:
                self.eng = matlab.engine.start_matlab()
                self.status_changed.emit("MATLAB Engine已启动")

            # 设置工作路径（必须）
            matlab_path = r'D:\data\PPG_HeartRate\Algorithm\ALL\matlab'
            self.eng.cd(matlab_path)

            # 创建默认参数结构体
            para_base = self._create_default_params()

            # 调用构造函数
            self.solver = self.eng.OnlineHeartRateSolver(scenario_name, para_base)
            self.current_scenario = scenario_name
            self.status_changed.emit(f"场景 [{scenario_name}] 加载成功")

        except Exception as e:
            raise Exception(f"MATLAB初始化失败: {str(e)}")

    def _create_default_params(self):
        """创建MATLAB算法的默认参数结构体"""
        para = self.eng.struct()
        para.Fs_Target = 100
        para.HR_Range_Hz = [0.67, 3.0]  # 40-180 BPM
        para.Smooth_Win_Len = 5
        para.Calib_Time = 60
        para.Motion_Th_Scale = 3
        para.Spec_Penalty_Enable = 1
        para.Spec_Penalty_Weight = 0.2
        para.Spec_Penalty_Width = 0.1
        para.Max_Order = 100
        para.Slew_Limit_BPM = 20
        para.Slew_Step_BPM = 10
        para.Slew_Limit_Rest = 15
        para.Slew_Step_Rest = 5
        para.HR_Range_Rest = [0.67, 3.0]
        return para

    def _get_data_for_matlab(self):
        """
        从DataBuffer获取最新125个数据点并转换为MATLAB格式

        Returns:
            matlab.double: 125x7矩阵，每行格式为 [PPG, HF1, HF2, HF3, ACCx, ACCy, ACCz]
            None: 数据不足
        """
        if self.data_buffer is None or len(self.data_buffer) < 125:
            return None

        # 使用锁保护数据读取
        with self.data_lock:
            # 取最新125个点
            raw_data = list(self.data_buffer)[-125:]

        # 转换为numpy数组
        import numpy as np
        data_array = np.array(raw_data, dtype=np.float64)

        # 转换为MATLAB格式
        import matlab.engine
        mat_data = matlab.double(data_array.tolist())

        return mat_data

    def _process_step_with_timeout(self, mat_data, timeout=2.0):
        """
        带超时保护的MATLAB计算调用

        Args:
            mat_data: MATLAB格式输入数据
            timeout: 超时时间（秒）

        Returns:
            dict: {'results': hr_results, 'is_ready': bool}

        Raises:
            TimeoutError: 计算超时
            Exception: MATLAB计算异常
        """
        result = [None]
        exception = [None]
        is_ready = [False]

        def worker():
            try:
                hr_results, ready = self.solver.process_step(mat_data)
                if ready:
                    result[0] = hr_results
                    is_ready[0] = True
            except Exception as e:
                exception[0] = e

        # 创建守护线程执行MATLAB调用
        thread = threading.Thread(target=worker)
        thread.daemon = True
        thread.start()
        thread.join(timeout)

        # 检查超时
        if thread.is_alive():
            raise TimeoutError("MATLAB计算超时")

        # 检查异常
        if exception[0]:
            raise exception[0]

        return {'results': result[0], 'is_ready': is_ready[0]}