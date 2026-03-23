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
        import sys
        sys.stdout.write("[MATLAB] MatlabWorkerThread已启动\n")
        sys.stdout.flush()
        # 启动计算定时器
        self.timer = QTimer()
        self.timer.timeout.connect(self._process_step)
        self.timer.start(1000)  # 每1秒触发一次
        sys.stdout.write("[MATLAB] 定时器已启动，每1秒触发一次计算\n")
        sys.stdout.flush()
        self.exec()

    def set_data_buffer(self, buffer: deque, lock: threading.Lock):
        """设置数据缓存引用"""
        self.data_buffer = buffer
        self.data_lock = lock

    def start_calculation(self):
        """启动计算"""
        import sys
        sys.stdout.write("[MATLAB] start_calculation()被调用\n")
        sys.stdout.flush()
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
        # 使用MATLAB eval创建结构体（MATLAB Engine API的struct()返回字典）
        import matlab.engine
        para = self.eng.eval(
            "struct("
            "'Fs_Target',100,"
            "'HR_Range_Hz',[0.67,3.0],"
            "'Smooth_Win_Len',5,"
            "'Calib_Time',60,"
            "'Motion_Th_Scale',3,"
            "'Spec_Penalty_Enable',1,"
            "'Spec_Penalty_Weight',0.2,"
            "'Spec_Penalty_Width',0.1,"
            "'Max_Order',100,"
            "'Slew_Limit_BPM',20,"
            "'Slew_Step_BPM',10,"
            "'Slew_Limit_Rest',15,"
            "'Slew_Step_Rest',5,"
            "'HR_Range_Rest',[0.67,3.0]"
            ")"
        )
        return para

    def _get_data_for_matlab(self):
        """
        从DataBuffer获取最新125个数据点并转换为MATLAB格式

        Returns:
            matlab.double: 125x7矩阵，每行格式为 [PPG, HF1, HF2, HF3, ACCx, ACCy, ACCz]
            None: 数据不足
        """
        import sys
        if self.data_buffer is None or len(self.data_buffer) < 125:
            return None

        # 使用锁保护数据读取
        with self.data_lock:
            # 取最新125个点
            raw_data = list(self.data_buffer)[-125:]

        # 转换为numpy数组
        import numpy as np
        data_array = np.array(raw_data, dtype=np.float64)

        # 调试：检查数据形状
        sys.stdout.write(f"[DEBUG] data_array.shape: {data_array.shape}\n")
        sys.stdout.flush()

        # 转换为MATLAB格式 - 确保是行优先格式（每行一个时间点）
        import matlab.engine
        # MATLAB Engine API 需要嵌套列表格式 [[row1], [row2], ...]
        data_list = data_array.tolist()
        mat_data = matlab.double(data_list)

        # 调试：检查MATLAB数据形状
        sys.stdout.write(f"[DEBUG] mat_data size: {len(mat_data)} x {len(mat_data[0]) if len(mat_data) > 0 else 0}\n")
        sys.stdout.write(f"[DEBUG] First sample: {data_list[0]}\n")
        sys.stdout.flush()

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
                # 使用feval调用MATLAB对象方法: feval('method_name', obj, args..., nargout=N)
                matlab_result = self.eng.feval('process_step', self.solver, mat_data, nargout=2)
                ready = matlab_result[1]
                if ready:
                    result[0] = matlab_result[0]
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

    def _process_step(self):
        """主计算流程入口，由定时器每秒触发"""
        import sys

        if not self.is_calculating:
            return

        if self.solver is None:
            sys.stdout.write("[MATLAB] Solver未初始化\n")
            sys.stdout.flush()
            return

        # 1. 获取数据
        try:
            mat_data = self._get_data_for_matlab()
            if mat_data is None:
                buffer_len = len(self.data_buffer) if self.data_buffer else 0
                sys.stdout.write(f"[MATLAB] 数据不足: 当前{buffer_len}点，需要125点\n")
                sys.stdout.flush()
                self.status_changed.emit("数据收集中...")
                return
            sys.stdout.write(f"[MATLAB] 数据已获取: {len(mat_data)} x {len(mat_data[0])}\n")
            sys.stdout.flush()
        except Exception as e:
            self.error_occurred.emit(f"数据获取失败: {str(e)}")
            return

        # 2. 调用MATLAB（带超时保护）
        try:
            sys.stdout.write("[MATLAB] 调用process_step...\n")
            sys.stdout.flush()
            result = self._process_step_with_timeout(mat_data, timeout=2.0)
            sys.stdout.write(f"[MATLAB] process_step完成: is_ready={result.get('is_ready', False)}\n")
            sys.stdout.flush()
        except TimeoutError:
            self.timeout_count += 1
            sys.stdout.write(f"[MATLAB] 计算超时 (次数: {self.timeout_count})\n")
            sys.stdout.flush()
            if self.timeout_count >= 3:
                self.error_occurred.emit("MATLAB计算连续超时，尝试重启引擎")
                self._restart_matlab_engine()
            return
        except Exception as e:
            sys.stdout.write(f"[MATLAB] 计算异常: {str(e)}\n")
            sys.stdout.flush()
            self.error_occurred.emit(f"MATLAB计算异常: {str(e)}")
            return

        # 3. 解析结果并发送信号
        if result is not None and result.get('is_ready', False):
            hr_results = result['results']
            try:
                # MATLAB Engine返回的标量可直接访问
                hr_hf = float(hr_results['Final_HR_HF']) * 60  # Hz → BPM
                hr_acc = float(hr_results['Final_HR_ACC']) * 60
                is_motion = bool(hr_results['Motion_Flag_HF_Path'])

                sys.stdout.write(f"[MATLAB] 心率结果: HF={hr_hf:.1f}, ACC={hr_acc:.1f}, Motion={is_motion}\n")
                sys.stdout.flush()

                # 发送信号
                self.hr_ready.emit(hr_hf, hr_acc, is_motion)
                self.timeout_count = 0  # 重置超时计数
            except (KeyError, TypeError, ValueError) as e:
                sys.stdout.write(f"[MATLAB] 结果解析失败: {str(e)}\n")
                sys.stdout.flush()
                self.error_occurred.emit(f"结果解析失败: {str(e)}")
        else:
            sys.stdout.write("[MATLAB] 结果未就绪 (is_ready=False)\n")
            sys.stdout.flush()

    def _restart_matlab_engine(self):
        """重启MATLAB Engine和求解器"""
        try:
            # 尝试优雅关闭
            if self.eng is not None:
                self.eng.quit()
        except:
            pass  # 忽略关闭错误

        try:
            # 重新启动
            import matlab.engine
            self.eng = matlab.engine.start_matlab()

            # 设置工作路径
            matlab_path = r'D:\data\PPG_HeartRate\Algorithm\ALL\matlab'
            self.eng.cd(matlab_path)

            # 重新初始化求解器
            self.init_solver(self.current_scenario)
            self.timeout_count = 0
            self.status_changed.emit("MATLAB引擎已重启")
        except Exception as e:
            self.error_occurred.emit(f"MATLAB重启失败: {str(e)}")

    def cleanup(self):
        """清理资源，在退出时调用"""
        self.stop_calculation()
        try:
            if self.eng is not None:
                self.eng.quit()
        except:
            pass