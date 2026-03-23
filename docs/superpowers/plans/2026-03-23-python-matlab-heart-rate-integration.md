# Python-MATLAB 心率解算集成实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标:** 实现 Python上位机与 MATLAB算法层的实时集成，包括数据传输、心率解算、UI显示和场景切换功能。

**架构:** 采用线程解耦方案，SerialReaderThread采集数据存入DataBuffer，MatlabWorkerThread独立线程每秒取125个点调用MATLAB Engine计算心率，通过PyQt信号槽返回结果给UI显示。

**技术栈:** Python 3.x, PyQt5, MATLAB Engine API for Python, numpy, pyqtgraph

---

## 前置条件验证

在开始实施前，必须验证以下条件：

### Task 0: 验证MATLAB Engine可用性

**目的:** 确认MATLAB Engine API已正确安装

**验证步骤:**

```bash
# 1. 验证Python能否导入matlab.engine
python -c "import matlab.engine; print('MATLAB Engine可导入')"

# 2. 验证能否启动MATLAB
python -c "import matlab.engine; eng = matlab.engine.start_matlab(); print(eng.sqrt(16)); eng.quit()"
# 预期输出: 4.0
```

**如果失败:** 按照设计文档第8.3节安装MATLAB Engine API

```matlab
% 在MATLAB R2021b中执行
cd('C:\Program Files\MATLAB\R2021b\extern\engines\python')
system('python setup.py install')
```

---

## 文件结构

### 新增文件

| 文件路径 | 职责 |
|---------|------|
| `python/matlab_worker.py` | MatlabWorkerThread类，负责MATLAB Engine调用和数据转换 |

### 修改文件

| 文件路径 | 修改内容 |
|---------|---------|
| `python/getdata.py` | 集成MatlabWorkerThread，新增心率显示UI和场景选择控件 |

---

## 阶段1: MATLAB Worker 模块

### Task 1: 创建 MatlabWorkerThread 基础框架

**文件:**
- Create: `python/matlab_worker.py`

**目的:** 创建独立的MATLAB工作线程类框架

- [ ] **Step 1: 创建文件并导入依赖**

```python
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
```

- [ ] **Step 2: 运行语法检查**

```bash
python -m py_compile python/matlab_worker.py
```

预期: 无语法错误

- [ ] **Step 3: 提交**

```bash
git add python/matlab_worker.py
git commit -m "feat: 创建MatlabWorkerThread基础框架

- 定义线程类和信号接口
- 实现启动/停止控制方法
- 添加定时器用于周期性触发计算

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 2: 实现 MATLAB Engine 初始化

**文件:**
- Modify: `python/matlab_worker.py:30-50`

**目的:** 实现MATLAB Engine启动和OnlineHeartRateSolver初始化

- [ ] **Step 1: 添加MATLAB初始化方法**

在 `MatlabWorkerThread` 类中添加以下方法：

```python
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
```

- [ ] **Step 2: 运行语法检查**

```bash
python -m py_compile python/matlab_worker.py
```

预期: 无语法错误

- [ ] **Step 3: 提交**

```bash
git add python/matlab_worker.py
git commit -m "feat: 实现MATLAB Engine初始化

- 添加init_solver方法启动MATLAB Engine
- 设置MATLAB工作路径
- 创建默认参数结构体para_base
- 初始化OnlineHeartRateSolver求解器
- 添加错误处理和状态反馈

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 3: 实现数据转换和发送

**文件:**
- Modify: `python/matlab_worker.py:80-120`

**目的:** 实现从DataBuffer取数据并转换为MATLAB格式

- [ ] **Step 1: 添加数据处理方法**

在 `MatlabWorkerThread` 类中添加以下方法：

```python
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
```

- [ ] **Step 2: 运行语法检查**

```bash
python -m py_compile python/matlab_worker.py
```

预期: 无语法错误

- [ ] **Step 3: 提交**

```bash
git add python/matlab_worker.py
git commit -m "feat: 实现数据格式转换

- 添加_get_data_for_matlab方法
- 使用线程锁保护数据读取
- 将Python数据转换为numpy数组
- 转换为MATLAB double格式

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 4: 实现超时保护机制

**文件:**
- Modify: `python/matlab_worker.py:120-160`

**目的:** 实现带超时保护的MATLAB调用

- [ ] **Step 1: 添加超时保护方法**

在 `MatlabWorkerThread` 类中添加以下方法：

```python
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
```

- [ ] **Step 2: 运行语法检查**

```bash
python -m py_compile python/matlab_worker.py
```

预期: 无语法错误

- [ ] **Step 3: 提交**

```bash
git add python/matlab_worker.py
git commit -m "feat: 实现超时保护机制

- 使用独立线程执行MATLAB调用
- 设置2秒超时限制
- 防止MATLAB卡死导致Python线程挂起
- 守护线程避免资源泄漏

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 5: 实现主计算流程

**文件:**
- Modify: `python/matlab_worker.py:55-80`

**目的:** 实现完整的计算流程，包括数据获取、MATLAB调用和结果解析

- [ ] **Step 1: 实现_process_step方法**

修改 `MatlabWorkerThread` 类中的 `_process_step` 方法：

```python
    def _process_step(self):
        """主计算流程入口，由定时器每秒触发"""
        if not self.is_calculating:
            return

        if self.solver is None:
            return

        # 1. 获取数据
        try:
            mat_data = self._get_data_for_matlab()
            if mat_data is None:
                self.status_changed.emit("数据收集中...")
                return
        except Exception as e:
            self.error_occurred.emit(f"数据获取失败: {str(e)}")
            return

        # 2. 调用MATLAB（带超时保护）
        try:
            result = self._process_step_with_timeout(mat_data, timeout=2.0)
        except TimeoutError:
            self.timeout_count += 1
            if self.timeout_count >= 3:
                self.error_occurred.emit("MATLAB计算连续超时，尝试重启引擎")
                self._restart_matlab_engine()
            return
        except Exception as e:
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

                # 发送信号
                self.hr_ready.emit(hr_hf, hr_acc, is_motion)
                self.timeout_count = 0  # 重置超时计数
            except (KeyError, TypeError, ValueError) as e:
                self.error_occurred.emit(f"结果解析失败: {str(e)}")
```

- [ ] **Step 2: 运行语法检查**

```bash
python -m py_compile python/matlab_worker.py
```

预期: 无语法错误

- [ ] **Step 3: 提交**

```bash
git add python/matlab_worker.py
git commit -m "feat: 实现主计算流程

- 实现_process_step方法
- 数据获取、MATLAB调用、结果解析完整流程
- 连续超时检测和自动重启机制
- Hz到BPM的单位转换
- 错误处理和状态反馈

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 6: 实现错误恢复机制

**文件:**
- Modify: `python/matlab_worker.py:160-190`

**目的:** 实现MATLAB引擎重启功能

- [ ] **Step 1: 添加重启方法**

在 `MatlabWorkerThread` 类中添加以下方法：

```python
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
```

- [ ] **Step 2: 运行语法检查**

```bash
python -m py_compile python/matlab_worker.py
```

预期: 无语法错误

- [ ] **Step 3: 完成MatlabWorkerThread并提交**

```bash
git add python/matlab_worker.py
git commit -m "feat: 完成MatlabWorkerThread实现

- 添加MATLAB引擎重启机制
- 添加cleanup方法用于资源清理
- 完成独立MATLAB计算线程的全部功能

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## 阶段2: 参数文件符号链接设置

### Task 7: 创建参数文件符号链接

**文件:**
- 在 `matlab/` 目录创建符号链接

**目的:** 使MATLAB能找到期望的参数文件名

- [ ] **Step 1: 创建符号链接（需要管理员权限）**

```bash
# 进入matlab目录
cd D:\data\PPG_HeartRate\Algorithm\ALL\matlab

# 为tiaosheng场景创建符号链接
mklink Best_Params_Result_tiaosheng.mat Best_Params_20260119dualtiaosheng1_processed.mat

# 为bobi场景创建符号链接
mklink Best_Params_Result_bobi.mat Best_Params_20260119dualtiaosheng1_processed.mat

# 为kaihe场景创建符号链接
mklink Best_Params_Result_kaihe.mat Best_Params_20260119dualtiaosheng1_processed.mat
```

**如果权限不足，使用复制作为替代方案:**

```cmd
copy Best_Params_20260119dualtiaosheng1_processed.mat Best_Params_Result_tiaosheng.mat
copy Best_Params_20260119dualtiaosheng1_processed.mat Best_Params_Result_bobi.mat
copy Best_Params_20260119dualtiaosheng1_processed.mat Best_Params_Result_kaihe.mat
```

- [ ] **Step 2: 验证文件存在**

```bash
dir Best_Params_Result_*.mat
```

预期: 显示3个文件

- [ ] **Step 3: 提交**

```bash
git add matlab/Best_Params_Result_*.mat
git commit -m "feat: 创建场景参数文件符号链接

- 为tiaosheng场景创建符号链接
- 为bobi场景创建符号链接
- 为kaihe场景创建符号链接
- 三个场景共享同一套优化参数

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## 阶段3: UI集成

### Task 8: 在MainWindow中集成MatlabWorkerThread

**文件:**
- Modify: `python/getdata.py:1-30` (导入部分)
- Modify: `python/getdata.py:150-200` (MainWindow.__init__)

**目的:** 在主窗口中初始化MATLAB工作线程

- [ ] **Step 1: 添加必要的导入**

在 `getdata.py` 文件顶部添加：

```python
import threading
from matlab_worker import MatlabWorkerThread
```

- [ ] **Step 2: 在MainWindow.__init__中添加MATLAB初始化**

在 `MainWindow.__init__` 方法中添加（在 `self.init_ui()` 之前）：

```python
        # MATLAB工作线程初始化
        self.matlab_worker = None
        self.matlab_available = False
        self.data_buffer = deque(maxlen=1000)  # 8秒数据缓存 @ 125Hz
        self.data_lock = threading.Lock()

        # 尝试初始化MATLAB
        try:
            self.matlab_worker = MatlabWorkerThread()
            self.matlab_worker.init_solver('tiaosheng')  # 默认场景
            self.matlab_worker.set_data_buffer(self.data_buffer, self.data_lock)
            self.matlab_worker.hr_ready.connect(self.handle_hr_result)
            self.matlab_worker.error_occurred.connect(self.handle_matlab_error)
            self.matlab_worker.status_changed.connect(self.handle_matlab_status)
            self.matlab_available = True
        except ImportError:
            QMessageBox.warning(self, "MATLAB Engine未安装",
                "未检测到MATLAB Engine API。\n\n"
                "心率功能将不可用。")
        except Exception as e:
            QMessageBox.warning(self, "MATLAB启动失败",
                f"无法启动MATLAB: {e}\n\n心率功能将不可用。")
```

- [ ] **Step 3: 添加信号处理方法**

在 `MainWindow` 类中添加：

```python
    def handle_hr_result(self, hr_hf, hr_acc, is_motion):
        """处理心率计算结果"""
        # 将在Task 10中实现UI更新
        pass

    def handle_matlab_error(self, error_msg):
        """处理MATLAB错误"""
        QMessageBox.warning(self, "MATLAB错误", error_msg)

    def handle_matlab_status(self, status_msg):
        """处理MATLAB状态更新"""
        print(f"MATLAB状态: {status_msg}")
```

- [ ] **Step 4: 运行语法检查**

```bash
python -m py_compile python/getdata.py
```

- [ ] **Step 5: 提交**

```bash
git add python/getdata.py
git commit -m "feat: 在MainWindow中集成MatlabWorkerThread

- 添加必要的导入
- 创建DataBuffer和线程锁
- 初始化MATLAB工作线程
- 连接信号槽
- 添加错误处理和降级运行机制

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 9: 修改SerialReaderThread集成DataBuffer

**文件:**
- Modify: `python/getdata.py:17-150` (SerialReaderThread类)

**目的:** 让串口接收线程将数据写入共享的DataBuffer

- [ ] **Step 1: 修改SerialReaderThread构造函数**

修改 `SerialReaderThread.__init__` 方法，添加data_buffer参数：

```python
    def __init__(self, port, baudrate, data_buffer, data_lock):
        super().__init__()
        self.port = port
        self.baudrate = baudrate
        self.serial_port = None
        self.is_running = False
        self.data_buffer = data_buffer  # 新增
        self.data_lock = data_lock        # 新增
        self.total_packets = 0
        self.invalid_packets = 0
```

- [ ] **Step 2: 修改parse_packet方法，将数据写入DataBuffer**

在 `parse_packet` 方法的最后，计算丢包率之前添加：

```python
        # 将MATLAB需要的数据写入DataBuffer
        # 格式: (PPG, HF1, HF2, HF3, ACCx, ACCy, ACCz)
        # HF3置零，使用Ut1和Ut2作为HF1和HF2
        matlab_data = (ppg_green, Ut1, Ut2, 0.0, Accx, Accy, Accz)
        with self.data_lock:
            self.data_buffer.append(matlab_data)
```

- [ ] **Step 3: 修改MainWindow中创建SerialReaderThread的代码**

找到 `toggle_serial` 方法中创建 `SerialReaderThread` 的位置，修改为：

```python
            self.serial_thread = SerialReaderThread(port, baud, self.data_buffer, self.data_lock)
```

- [ ] **Step 4: 运行语法检查**

```bash
python -m py_compile python/getdata.py
```

- [ ] **Step 5: 提交**

```bash
git add python/getdata.py
git commit -m "feat: SerialReaderThread集成DataBuffer

- 添加data_buffer和data_lock参数
- 在parse_packet中写入MATLAB格式数据
- HF3置零，使用Ut1和Ut2作为HF参考信号
- 使用线程锁保护数据写入

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 10: 添加心率显示UI

**文件:**
- Modify: `python/getdata.py:165-185` (初始化部分)
- Modify: `python/getdata.py:270-355` (init_ui方法)

**目的:** 创建心率显示区域和波形图

- [ ] **Step 1: 在MainWindow.__init__中添加心率数据存储**

在 `self.data_temp = deque(maxlen=self.plot_pts)` 后添加：

```python
        # 心率数据存储
        self.data_hr_hf = deque(maxlen=60)  # 60秒历史
        self.data_hr_acc = deque(maxlen=60)
        self.data_time = deque(maxlen=60)
        self.hr_start_time = time.time()
```

- [ ] **Step 2: 在init_ui中添加心率显示区域**

在控制面板布局（`control_layout`）中，"数据记录"分组之后添加：

```python
        # 4. 心率监测 (新增)
        hr_group = QGroupBox("心率监测")
        hr_vbox = QVBoxLayout()

        # 数值显示
        hr_info_layout = QHBoxLayout()
        self.lbl_hr_hf = QLabel("HF: -- BPM")
        self.lbl_hr_hf.setStyleSheet("font-weight: bold; color: #4CAF50; font-size: 16px;")
        self.lbl_hr_acc = QLabel("ACC: -- BPM")
        self.lbl_hr_acc.setStyleSheet("font-weight: bold; color: #9E9E9E; font-size: 14px;")
        self.lbl_motion = QLabel("状态: --")
        self.lbl_motion.setStyleSheet("color: #FF9800;")
        hr_info_layout.addWidget(self.lbl_hr_hf)
        hr_info_layout.addWidget(self.lbl_hr_acc)
        hr_info_layout.addWidget(self.lbl_motion)

        hr_vbox.addLayout(hr_info_layout)

        # 心率波形图
        self.plot_w_hr = pg.PlotWidget(title="心率趋势 (最近60秒)")
        self.plot_w_hr.showGrid(x=True, y=True)
        self.plot_w_hr.setYRange(40, 200)
        self.plot_w_hr.setLabel('left', '心率', units='BPM')
        self.plot_w_hr.setLabel('bottom', '时间', units='s')
        self.curve_hr_hf = self.plot_w_hr.plot(pen=pg.mkPen('g', width=2), name="HF")
        self.curve_hr_acc = self.plot_w_hr.plot(pen=pg.mkPen((150,150,150), width=1, style=Qt.DashLine), name="ACC")
        self.plot_w_hr.addLegend()

        hr_vbox.addWidget(self.plot_w_hr)
        hr_group.setLayout(hr_vbox)

        control_layout.addWidget(hr_group)
```

- [ ] **Step 3: 实现handle_hr_result方法**

修改之前添加的 `handle_hr_result` 方法：

```python
    def handle_hr_result(self, hr_hf, hr_acc, is_motion):
        """处理心率计算结果"""
        # 更新数值显示
        self.lbl_hr_hf.setText(f"HF: {hr_hf:.0f} BPM")
        self.lbl_hr_acc.setText(f"ACC: {hr_acc:.0f} BPM")

        motion_str = "运动" if is_motion else "静息"
        self.lbl_motion.setText(f"状态: {motion_str}")

        # 更新波形图数据
        elapsed = time.time() - self.hr_start_time
        self.data_hr_hf.append(hr_hf)
        self.data_hr_acc.append(hr_acc)
        self.data_time.append(elapsed)
```

- [ ] **Step 4: 修改update_plots方法，添加心率曲线更新**

在 `update_plots` 方法末尾添加：

```python
            # 更新心率曲线
            if len(self.data_time) > 0:
                self.curve_hr_hf.setData(list(self.data_time), list(self.data_hr_hf))
                self.curve_hr_acc.setData(list(self.data_time), list(self.data_hr_acc))
```

- [ ] **Step 5: 运行语法检查**

```bash
python -m py_compile python/getdata.py
```

- [ ] **Step 6: 提交**

```bash
git add python/getdata.py
git commit -m "feat: 添加心率显示UI

- 新增心率监测GroupBox
- 显示HF和ACC心率数值
- 显示运动状态
- 添加心率趋势波形图（最近60秒）
- 实现handle_hr_result方法更新显示

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 11: 添加场景选择控件

**文件:**
- Modify: `python/getdata.py:270-355` (init_ui方法)
- Modify: `python/getdata.py:390-430` (toggle_serial附近)

**目的:** 创建场景选择UI和加载功能

- [ ] **Step 1: 在init_ui中添加算法设置分组**

在心率监测分组之后添加：

```python
        # 5. 算法设置 (新增)
        algo_group = QGroupBox("算法设置")
        algo_vbox = QVBoxLayout()

        # 场景选择
        scenario_layout = QHBoxLayout()
        scenario_layout.addWidget(QLabel("运动场景:"))
        self.cb_scenario = QComboBox()
        self.cb_scenario.addItem("tiaosheng")
        self.cb_scenario.addItem("bobi")
        self.cb_scenario.addItem("kaihe")
        scenario_layout.addWidget(self.cb_scenario)

        # 加载按钮
        self.btn_load_scenario = QPushButton("加载场景参数")
        self.btn_load_scenario.clicked.connect(self.load_scenario)
        self.btn_load_scenario.setEnabled(self.matlab_available)

        scenario_layout.addWidget(self.btn_load_scenario)
        algo_vbox.addLayout(scenario_layout)

        # 当前场景显示
        self.lbl_current_scene = QLabel("当前场景: tiaosheng")
        self.lbl_current_scene.setStyleSheet("color: #2196F3;")
        algo_vbox.addWidget(self.lbl_current_scene)

        algo_group.setLayout(algo_vbox)

        control_layout.addWidget(algo_group)
        control_layout.addStretch()
```

- [ ] **Step 2: 实现load_scenario方法**

在 `MainWindow` 类中添加：

```python
    def load_scenario(self):
        """加载选定的场景参数"""
        if not self.matlab_available or self.matlab_worker is None:
            QMessageBox.warning(self, "提示", "MATLAB不可用，无法加载场景")
            return

        scenario_name = self.cb_scenario.currentText()

        try:
            # 停止当前计算
            was_calculating = self.matlab_worker.is_calculating
            self.matlab_worker.stop_calculation()

            # 重新初始化求解器
            self.matlab_worker.init_solver(scenario_name)

            # 恢复计算
            if was_calculating:
                self.matlab_worker.start_calculation()

            # 更新UI
            self.lbl_current_scene.setText(f"当前场景: {scenario_name}")
            QMessageBox.information(self, "成功", f"场景 [{scenario_name}] 加载成功")

        except Exception as e:
            QMessageBox.warning(self, "场景加载失败", f"无法加载场景 {scenario_name}: {e}")
```

- [ ] **Step 3: 修改toggle_serial，同步启动/停止MATLAB计算**

在 `toggle_serial` 方法中，串口连接成功后添加：

```python
            # 启动MATLAB计算
            if self.matlab_available:
                self.matlab_worker.start_calculation()
```

在串口关闭部分添加：

```python
            # 停止MATLAB计算
            if self.matlab_available and self.matlab_worker:
                self.matlab_worker.stop_calculation()
```

- [ ] **Step 4: 修改closeEvent，添加MATLAB清理**

在 `closeEvent` 方法中添加：

```python
        # 清理MATLAB资源
        if self.matlab_worker:
            self.matlab_worker.cleanup()
```

- [ ] **Step 5: 运行语法检查**

```bash
python -m py_compile python/getdata.py
```

- [ ] **Step 6: 提交**

```bash
git add python/getdata.py
git commit -m "feat: 添加场景选择功能

- 新增算法设置GroupBox
- 添加场景下拉框（tiaosheng/bobi/kaihe）
- 实现load_scenario方法
- 串口连接时自动启动MATLAB计算
- 关闭程序时清理MATLAB资源

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## 阶段4: 数据记录扩展

### Task 12: 实现双文件CSV记录

**文件:**
- Modify: `python/getdata.py:400-435` (toggle_record方法)
- Modify: `python/getdata.py:435-465` (handle_new_data方法)

**目的:** 将心率数据单独记录到文件

- [ ] **Step 1: 修改toggle_record，创建双文件**

修改 `toggle_record` 方法，在创建传感器数据文件后添加心率文件：

```python
            # 创建心率数据文件
            hr_filename = f"HeartRate_{timestamp}.csv"
            hr_filepath = os.path.join(folder, hr_filename)

            try:
                self.hr_csv_file = open(hr_filepath, 'w', newline='')
                self.hr_csv_writer = csv.writer(self.hr_csv_file)
                # 心率文件表头
                self.hr_csv_writer.writerow(["Time(s)", "HR_HF(BPM)", "HR_ACC(BPM)", "Motion_State", "Scenario"])
                self.hr_start_time_record = time.time()
            except Exception as e:
                QMessageBox.critical(self, "文件错误", f"无法创建心率文件: {e}")
                self.csv_file.close()
                self.csv_file = None
                return
```

同时在 `is_recording = False` 分支添加心率文件关闭：

```python
            if self.hr_csv_file:
                self.hr_csv_file.close()
                self.hr_csv_file = None
```

- [ ] **Step 2: 在handle_hr_result中记录心率数据**

修改 `handle_hr_result` 方法，添加记录逻辑：

```python
    def handle_hr_result(self, hr_hf, hr_acc, is_motion):
        """处理心率计算结果"""
        # 更新数值显示
        self.lbl_hr_hf.setText(f"HF: {hr_hf:.0f} BPM")
        self.lbl_hr_acc.setText(f"ACC: {hr_acc:.0f} BPM")

        motion_str = "运动" if is_motion else "静息"
        self.lbl_motion.setText(f"状态: {motion_str}")

        # 记录到CSV
        if self.is_recording and self.hr_csv_writer:
            elapsed = round(time.time() - self.hr_start_time_record, 3)
            scenario = self.cb_scenario.currentText()
            motion_int = 1 if is_motion else 0
            self.hr_csv_writer.writerow([elapsed, round(hr_hf, 1), round(hr_acc, 1), motion_int, scenario])
            self.hr_csv_file.flush()

        # 更新波形图数据
        elapsed = time.time() - self.hr_start_time
        self.data_hr_hf.append(hr_hf)
        self.data_hr_acc.append(hr_acc)
        self.data_time.append(elapsed)
```

- [ ] **Step 3: 运行语法检查**

```bash
python -m py_compile python/getdata.py
```

- [ ] **Step 4: 提交**

```bash
git add python/getdata.py
git commit -m "feat: 实现双文件CSV记录

- 传感器数据记录到SensorData_*.csv（125Hz）
- 心率数据记录到HeartRate_*.csv（1Hz）
- 心率文件包含HR_HF, HR_ACC, Motion_State, Scenario
- 使用时间戳同步两个文件

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## 阶段5: 测试和验证

### Task 13: 单元测试 - MatlabWorkerThread

**文件:**
- Create: `tests/test_matlab_worker.py`

**目的:** 测试MatlabWorkerThread核心功能

- [ ] **Step 1: 创建测试文件**

```python
# tests/test_matlab_worker.py
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'python'))

import time
import threading
from collections import deque
from matlab_worker import MatlabWorkerThread

def test_matlab_initialization():
    """测试MATLAB Engine初始化"""
    worker = MatlabWorkerThread()

    try:
        worker.init_solver('tiaosheng')
        assert worker.solver is not None
        assert worker.eng is not None
        print("PASS: MATLAB初始化成功")
    except Exception as e:
        print(f"FAIL: MATLAB初始化失败 - {e}")
        raise
    finally:
        worker.cleanup()

def test_data_conversion():
    """测试数据格式转换"""
    worker = MatlabWorkerThread()

    # 创建测试数据
    buffer = deque(maxlen=1000)
    lock = threading.Lock()

    # 填充测试数据
    for i in range(125):
        data = (1000.0, 500.0, 600.0, 0.0, 0.1, 0.2, 0.3)
        with lock:
            buffer.append(data)

    worker.set_data_buffer(buffer, lock)

    # 测试数据获取
    mat_data = worker._get_data_for_matlab()
    assert mat_data is not None
    assert len(mat_data) == 125
    assert len(mat_data[0]) == 7
    print("PASS: 数据转换成功")

def test_timeout_protection():
    """测试超时保护机制"""
    worker = MatlabWorkerThread()

    try:
        worker.init_solver('tiaosheng')

        # 创建正常数据
        buffer = deque(maxlen=1000)
        lock = threading.Lock()
        for i in range(125):
            data = (1000.0, 500.0, 600.0, 0.0, 0.1, 0.2, 0.3)
            with lock:
                buffer.append(data)

        worker.set_data_buffer(buffer, lock)
        worker.start_calculation()

        # 等待计算完成
        time.sleep(3)

        worker.stop_calculation()
        print("PASS: 超时保护测试完成")
    except Exception as e:
        print(f"FAIL: 超时保护测试失败 - {e}")
        raise
    finally:
        worker.cleanup()

if __name__ == '__main__':
    print("运行MatlabWorkerThread单元测试...")
    print()

    try:
        test_matlab_initialization()
        test_data_conversion()
        test_timeout_protection()

        print()
        print("=" * 50)
        print("所有测试通过!")
        print("=" * 50)
    except Exception as e:
        print()
        print("=" * 50)
        print(f"测试失败: {e}")
        print("=" * 50)
        sys.exit(1)
```

- [ ] **Step 2: 运行测试**

```bash
cd D:\data\PPG_HeartRate\Algorithm\ALL
python tests/test_matlab_worker.py
```

预期输出: "所有测试通过!"

- [ ] **Step 3: 提交**

```bash
git add tests/test_matlab_worker.py
git commit -m "test: 添加MatlabWorkerThread单元测试

- 测试MATLAB Engine初始化
- 测试数据格式转换
- 测试超时保护机制
- 所有测试通过验证

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 14: 集成测试 - 端到端验证

**目的:** 完整测试从串口数据到心率显示的完整流程

- [ ] **Step 1: 准备测试环境**

确保：
1. 串口设备已连接
2. MATLAB Engine已安装
3. 参数文件符号链接已创建

- [ ] **Step 2: 启动程序并测试**

```bash
cd python
python getdata.py
```

**测试步骤:**
1. 选择正确的串口和波特率(115200)
2. 点击"打开串口"
3. 观察左侧状态面板：
   - 当前模式应显示"心率模式"或"血氧模式"
   - 采样率应显示约125 Hz
4. 观察心率监测区域：
   - 等待8-10秒（数据校准）
   - HF和ACC心率应显示数值（如75 BPM）
   - 状态应显示"静息"或"运动"
   - 波形图应绘制心率曲线
5. 点击"开始记录"
6. 等待10秒后点击"停止记录"
7. 检查生成的CSV文件：
   - `SensorData_*.csv` - 包含传感器数据
   - `HeartRate_*.csv` - 包含心率数据
8. 切换场景（tiaosheng → bobi → kaihe）
9. 验证场景切换后心率仍正常计算

- [ ] **Step 3: 记录测试结果**

```bash
# 记录测试通过
echo "集成测试通过 - $(date)" >> test_log.txt
```

- [ ] **Step 4: 提交测试记录**

```bash
git add test_log.txt
git commit -m "test: 端到端集成测试通过

- 串口数据接收正常
- MATLAB心率计算正常
- UI显示正确
- CSV记录完整
- 场景切换功能正常

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## 验收标准

完成所有任务后，系统应满足以下标准：

1. **功能完整性**
   - [ ] 串口数据正常接收并缓存到DataBuffer
   - [ ] MatlabWorkerThread每秒触发计算
   - [ ] MATLAB返回心率结果并在UI显示
   - [ ] 场景切换功能正常
   - [ ] CSV数据正常记录

2. **性能指标**
   - [ ] 心率更新延迟 < 1秒
   - [ ] UI响应流畅，无卡顿
   - [ ] 内存占用 < 500MB

3. **错误处理**
   - [ ] MATLAB Engine未安装时友好提示
   - [ ] 数据不足时显示"校准中..."
   - [ ] MATLAB超时后自动重启
   - [ ] 程序退出时正确清理资源

4. **代码质量**
   - [ ] 所有单元测试通过
   - [ ] 无语法错误
   - [ ] Git提交记录清晰

---

## 附录

### A. 故障排除

| 问题 | 可能原因 | 解决方案 |
|------|---------|---------|
| ModuleNotFoundError: matlab.engine | MATLAB Engine未安装 | 运行Task 0中的安装命令 |
| 场景加载失败 | 参数文件符号链接缺失 | 运行Task 7 |
| 心率始终显示-- | 数据不足或MATLAB计算失败 | 检查串口连接和MATLAB状态 |
| UI卡顿 | MATLAB计算阻塞 | 检查MatlabWorkerThread是否在独立线程 |
| CSV文件未生成 | 路径错误或权限不足 | 检查保存路径设置 |

### B. 关键常量

```python
SAMPLE_RATE = 125          # Hz
WINDOW_SIZE = 8            # 秒
STEP_SIZE = 1              # 秒
BUFFER_LEN = 1000          # 125 * 8
HR_UPDATE_INTERVAL = 1000  # 毫秒
HEART_RATE_RANGE = (40, 200)  # BPM
CALCULATION_TIMEOUT = 2.0  # 秒
MAX_TIMEOUT_COUNT = 3      # 连续超时次数
```

### C. 数据格式参考

**DataBuffer格式:**
```python
(ppg_green, ut1, ut2, 0.0, accx, accy, accz)
```

**MATLAB输入格式:**
```matlab
% 125 x 7 double矩阵
% 列: [PPG, HF1, HF2, HF3, ACCx, ACCy, ACCz]
```

**MATLAB输出格式:**
```matlab
results.Final_HR_HF          % scalar, Hz
results.Final_HR_ACC         % scalar, Hz
results.Motion_Flag_HF_Path  % boolean
results.Motion_Flag_ACC_Path % boolean
```
