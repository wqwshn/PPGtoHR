# Python-MATLAB 心率解算集成设计文档

**日期**: 2026-03-23
**状态**: 已批准
**版本**: 1.2
**作者**: Claude Code

---

## 1. 概述

本文档描述PPG心率检测项目中Python上位机与MATLAB算法层的集成方案。目标实现：Python采集原始传感器数据 → MATLAB自适应滤波解算心率 → 返回结果给Python前端显示。

### 1.1 设计目标

- **实时性**: 心率更新延迟 < 1秒
- **解耦性**: MATLAB计算不阻塞UI响应
- **可扩展**: 支持多场景参数切换
- **健壮性**: MATLAB故障不影响主程序运行

### 1.2 现有系统

**Python上位机** (`getdata.py`):
- PyQt5界面，串口接收21字节帧格式数据
- 实时解析：ADC热膜(4路)、三轴加速度、PPG(绿光/红光/红外)、温度
- 采样率约125Hz

**MATLAB算法** (`OnlineHeartRateSolver.m`):
- 双路并行解算（HF路径 + ACC路径）
- 8秒滑动窗口，1秒步进更新
- 输入格式: `[PPG, HF1, HF2, HF3, ACCx, ACCy, ACCz]`

---

## 2. 架构设计

### 2.1 整体架构

采用**线程解耦方案（方案B）**：

```
SerialReaderThread → DataBuffer → MatlabWorkerThread → UI更新
   (串口125Hz)      (队列缓存)     (MATLAB Engine)    (心率显示)
```

### 2.2 数据流

```
硬件传感器 → 串口(21字节) → Python解析 → DataBuffer(deque)
                                                            ↓
                                                    每1秒取125个点
                                                            ↓
                                    MatlabWorkerThread(独立线程)
                                                            ↓
                                                    MATLAB Engine API
                                                            ↓
                                            OnlineHeartRateSolver.process_step()
                                                            ↓
                            ┌───────────────────────────┴─────────────────────┐
                            ↓                                                   ↓
                    HR_HF, HR_ACC(Hz)                              Motion_Flag
                            ↓                                                   ↓
                    转换为BPM                                           状态字符串
                            ↓                                                   ↓
                    ┌───────────────────────────────────────────────────────┐
                            ↓                                               ↓
                    UI更新: 数值标签 + 波形图                     CSV记录扩展
```

### 2.3 组件职责

| 组件 | 职责 | 线程 |
|------|------|------|
| `SerialReaderThread` | 串口接收与数据解析 | 独立线程 |
| `DataBuffer` | 缓存最近8秒原始数据 | 主线程共享 |
| `MatlabWorkerThread` | MATLAB调用与结果处理 | 独立线程 |
| `MainWindow` | UI更新与用户交互 | 主线程 |

---

## 3. 核心组件设计

### 3.1 DataBuffer（数据缓存）

**实现**: `collections.deque(maxlen=1000)` + `threading.Lock()`

**数据结构**: 每个元素为7元组
```python
(ppg_green, ut1, ut2, 0, accx, accy, accz)
#            PPG   HF1  HF2  HF3  ACCx ACCy ACCz
#            第三路HF置零，满足MATLAB接口要求
```

**线程安全**:
- `deque`的`append`和`popleft`操作是原子的
- 使用`Lock`保护`list(deque)`转换操作
- 写操作(SerialReaderThread)与读操作(MatlabWorkerThread)隔离

### 3.2 MatlabWorkerThread（MATLAB工作线程）

**职责**:
1. 启动MATLAB Engine并设置工作路径
2. 创建默认参数结构体
3. 初始化`OnlineHeartRateSolver`
4. 每1秒触发一次计算
5. 通过信号槽返回结果
6. 错误恢复机制

**核心接口**:
```python
class MatlabWorkerThread(QThread):
    # 信号定义
    hr_ready = pyqtSignal(float, float, bool)  # HR_HF, HR_ACC, is_motion
    error_occurred = pyqtSignal(str)
    status_changed = pyqtSignal(str)  # 状态信息

    # 初始化MATLAB求解器
    def init_solver(self, scenario_name: str)

    # 设置数据缓存引用
    def set_data_buffer(self, buffer: deque, lock: threading.Lock)

    # 启动/停止计算
    def start_calculation(self)
    def stop_calculation(self)

    # 错误恢复
    def restart_matlab_engine(self)
```

**MATLAB初始化**:
```python
def init_solver(self, scenario_name: str):
    import matlab.engine

    # 设置工作路径（必须）
    matlab_path = r'D:\data\PPG_HeartRate\Algorithm\ALL\matlab'
    self.eng.cd(matlab_path)

    # 创建默认参数结构体
    para_base = self.eng.struct()
    para_base.Fs_Target = 100
    para_base.HR_Range_Hz = [0.67, 3.0]  # 40-180 BPM
    para_base.Smooth_Win_Len = 5
    para_base.Calib_Time = 60
    para_base.Motion_Th_Scale = 3
    para_base.Spec_Penalty_Enable = 1
    para_base.Spec_Penalty_Weight = 0.2
    para_base.Spec_Penalty_Width = 0.1
    para_base.Max_Order = 100
    para_base.Slew_Limit_BPM = 20
    para_base.Slew_Step_BPM = 10
    para_base.Slew_Limit_Rest = 15
    para_base.Slew_Step_Rest = 5
    para_base.HR_Range_Rest = [0.67, 3.0]

    # 调用构造函数
    self.solver = self.eng.OnlineHeartRateSolver(scenario_name, para_base)
    self.current_scenario = scenario_name
```

**计算流程**:
```python
def _process_step(self):
    # 1. 使用锁保护数据读取
    with self.data_lock:
        if len(self.data_buffer) < 125:
            self.status_changed.emit("数据收集中...")
            return
        data = list(self.data_buffer)[-125:]

    # 2. 转换为numpy数组再传给MATLAB
    import numpy as np
    mat_data = matlab.double(np.array(data).tolist())

    # 3. 调用MATLAB（带超时保护）
    try:
        results = self._process_step_with_timeout(mat_data, timeout=2.0)
    except TimeoutError:
        self.timeout_count += 1
        if self.timeout_count >= 3:
            self.error_occurred.emit("MATLAB计算连续超时，尝试重启引擎")
            self.restart_matlab_engine()
        return

    # 4. 解析结果（MATLAB返回标量可直接访问）
    if results is not None and results.get('is_ready', False):
        hr_hf = float(results['Final_HR_HF']) * 60  # Hz → BPM
        hr_acc = float(results['Final_HR_ACC']) * 60
        is_motion = bool(results['Motion_Flag_HF_Path'])
        self.hr_ready.emit(hr_hf, hr_acc, is_motion)
        self.timeout_count = 0  # 重置超时计数
```

**超时保护实现**:
```python
def _process_step_with_timeout(self, data, timeout=2.0):
    result = [None]
    exception = [None]
    is_ready = [False]

    def worker():
        try:
            hr_results, ready = self.solver.process_step(data)
            if ready:
                result[0] = hr_results
                is_ready[0] = True
        except Exception as e:
            exception[0] = e

    thread = threading.Thread(target=worker)
    thread.daemon = True
    thread.start()
    thread.join(timeout)

    if thread.is_alive():
        raise TimeoutError("MATLAB计算超时")
    if exception[0]:
        raise exception[0]

    return {'results': result[0], 'is_ready': is_ready[0]}
```

**错误恢复机制**:
```python
def restart_matlab_engine(self):
    try:
        if self.eng:
            self.eng.quit()
    except:
        pass

    # 重新启动
    import matlab.engine
    self.eng = matlab.engine.start_matlab()
    matlab_path = r'D:\data\PPG_HeartRate\Algorithm\ALL\matlab'
    self.eng.cd(matlab_path)
    self.init_solver(self.current_scenario)
    self.status_changed.emit("MATLAB引擎已重启")
```

### 3.3 UI新增：心率显示区域

**布局位置**: 控制面板下方，独立GroupBox

**组件构成**:
```
┌─────────────────────────────────────────────────┐
│  心率监测 (Heart Rate Monitor)                   │
├─────────────────────────────────────────────────┤
│  HF: 75 BPM   ACC: 73 BPM   运动状态: 静息      │
│  ┌───────────────────────────────────────────┐  │
│  │  [心率波形图 - 最近60秒]                   │  │
│  │     ___---___---___---___                  │  │
│  │  HF: 绿色曲线, ACC: 灰色虚线               │  │
│  └───────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

**显示逻辑**:
- 心率波形图使用`pyqtgraph.PlotWidget`
- X轴: 最近60秒，Y轴: 40-200 BPM
- 数值标签实时更新（1秒刷新）
- 数据不足时显示"心率校准中..."

### 3.4 UI新增：场景选择

**布局位置**: 控制面板"数据记录"下方

**组件构成**:
```
┌─────────────────────────────────┐
│  算法设置                        │
├─────────────────────────────────┤
│  运动场景:                       │
│  [tiaosheng ▼]                  │
│    - tiaosheng (跳绳)            │
│    - bobi (波比跳)               │
│    - kaihe (开合跳)              │
│                                 │
│  [加载场景参数] 按钮             │
│                                 │
│  当前场景: tiaosheng             │
└─────────────────────────────────┘
```

**场景映射**:

| Python场景名 | MATLAB场景名 | 期望参数文件 | 实际指向 |
|--------------|-------------|-------------|---------|
| tiaosheng | tiaosheng | `Best_Params_Result_tiaosheng.mat` | 符号链接 → `Best_Params_20260119dualtiaosheng1_processed.mat` |
| bobi | bobi | `Best_Params_Result_bobi.mat` | 符号链接 → `Best_Params_20260119dualtiaosheng1_processed.mat` |
| kaihe | kaihe | `Best_Params_Result_kaihe.mat` | 符号链接 → `Best_Params_20260119dualtiaosheng1_processed.mat` |

**注意**:
1. 三个场景暂时共享同一套优化参数
2. 需要在matlab目录中创建符号链接（见附录9.1）
3. 后续生成场景专用参数后，只需更新符号链接目标

**切换逻辑**:
```python
def load_scenario(self):
    scenario_name = self.cb_scenario.currentText()

    # 停止当前计算
    self.matlab_worker.stop_calculation()

    # 重新初始化求解器
    try:
        self.matlab_worker.init_solver(scenario_name)
        self.matlab_worker.status_changed.connect(self.update_matlab_status)
    except Exception as e:
        QMessageBox.warning(self, "场景加载失败", f"无法加载场景 {scenario_name}: {e}")
        return

    # 恢复计算
    if self.serial_thread and self.serial_thread.is_running:
        self.matlab_worker.start_calculation()

    # 更新UI
    self.lbl_current_scene.setText(f"当前场景: {scenario_name}")
```

---

## 4. 数据格式

### 4.1 Python → MATLAB

**输入矩阵**: `N×7 double`

| 列索引 | 字段 | 来源 | 说明 |
|--------|------|------|------|
| 1 | PPG | `ppg_green` | 绿光PPG信号 |
| 2 | HF1 | `ut1` | 桥顶电压1 |
| 3 | HF2 | `ut2` | 桥顶电压2 |
| 4 | HF3 | `0` | 填充0（占位） |
| 5 | ACCx | `accx` | 加速度X |
| 6 | ACCy | `accy` | 加速度Y |
| 7 | ACCz | `accz` | 加速度Z |

**发送频率**: 每1秒发送125行数据

### 4.2 MATLAB → Python

**输出结构体**:
```matlab
results.Final_HR_HF          % scalar, Hz
results.Final_HR_ACC         % scalar, Hz
results.Motion_Flag_HF_Path  % boolean
results.Motion_Flag_ACC_Path % boolean
```

**Python接收**（MATLAB Engine返回标量时直接访问）:
```python
# MATLAB Engine返回的标量可直接转换为Python类型
hr_hf = float(results['Final_HR_HF']) * 60  # Hz → BPM
hr_acc = float(results['Final_HR_ACC']) * 60
is_motion = bool(results['Motion_Flag_HF_Path'])
```

### 4.3 CSV记录扩展（双文件方案）

为了避免混用不同采样率的数据，采用**双文件记录**方案：

**文件1: 传感器数据** (`SensorData_YYYYmmdd_HHMMSS.csv`)
```csv
Time(s), Mode, Uc1(mV), Uc2(mV), Ut1(mV), Ut2(mV), AccX, AccY, AccZ, PPG_Green, PPG_Red, PPG_IR, Temp(C)
```
- 采样频率: 125Hz
- 每个串口数据包写入一行

**文件2: 心率数据** (`HeartRate_YYYYmmdd_HHMMSS.csv`)
```csv
Time(s), HR_HF(BPM), HR_ACC(BPM), Motion_State, Scenario
```
- 采样频率: 1Hz
- 每次MATLAB计算结果写入一行
- 使用相同的时间基准（相对于记录开始时间）

**未就绪处理**: 心率未就绪时，心率列写入 `-1`，运动状态写入 `-1`

---

## 5. 错误处理

### 5.1 MATLAB Engine连接失败

**检测时机**: 程序启动时

**处理策略**:
```python
def __init__(self):
    self.matlab_available = False
    self.matlab_worker = None

    try:
        import matlab.engine
        self.matlab_worker = MatlabWorkerThread()
        self.matlab_worker.init_solver('tiaosheng')
        self.matlab_available = True
    except ImportError:
        QMessageBox.warning(self, "MATLAB Engine未安装",
            "未检测到MATLAB Engine API。\n\n"
            "请按以下步骤安装：\n"
            "1. 打开MATLAB R2021b\n"
            "2. 执行: cd('matlabroot/extern/engines/python')\n"
            "3. 执行: system('python setup.py install')\n\n"
            "心率功能将不可用。")
    except Exception as e:
        QMessageBox.warning(self, "MATLAB启动失败",
            f"无法启动MATLAB Engine: {e}\n\n心率功能将不可用。")
```

**降级运行**: 主程序继续运行，仅禁用心率相关UI和功能

### 5.2 数据不足

**场景**: 启动前8秒或数据断流

**UI处理**:
```python
def update_hr_display(self, hr_hf, hr_acc, is_motion):
    if hr_hf < 0:  # 未就绪标记
        self.lbl_hr_hf.setText("HF: -- BPM")
        self.lbl_hr_acc.setText("ACC: -- BPM")
        self.lbl_status.setText("心率校准中...")
    else:
        self.lbl_hr_hf.setText(f"HF: {hr_hf:.0f} BPM")
        self.lbl_hr_acc.setText(f"ACC: {hr_acc:.0f} BPM")
        motion_str = "运动" if is_motion else "静息"
        self.lbl_status.setText(f"运动状态: {motion_str}")
```

### 5.3 计算超时

**检测**: 单次计算超过2秒（使用线程实现超时）

**处理策略**:
- 单次超时: 跳过本次计算，保持历史值
- 连续3次超时: 触发MATLAB引擎重启
- 重启失败: 通知用户并禁用心率功能

### 5.4 线程安全

**DataBuffer**:
```python
# 写操作（SerialReaderThread）
self.data_buffer.append(new_data)  # 原子操作，无需锁

# 读操作（MatlabWorkerThread）
with self.data_lock:
    data = list(self.data_buffer)[-125:]  # 需要锁保护
```

**信号槽通信**: PyQt跨线程安全，自动调度

**MATLAB Engine**: 单线程串行调用，由MatlabWorkerThread内部管理

---

## 6. 性能指标

| 指标 | 目标值 | 说明 |
|------|--------|------|
| 心率更新延迟 | < 1s | 从数据采集到UI显示 |
| UI响应性 | < 50ms | MATLAB计算不阻塞主线程 |
| 内存占用 | < 500MB | Python + MATLAB Engine |
| CPU占用 | < 30% | MATLAB计算线程 |

---

## 7. 实施计划

### 7.1 文件变更

**新增文件**:
- `python/matlab_worker.py` - MatlabWorkerThread实现

**修改文件**:
- `python/getdata.py` - 集成MatlabWorkerThread，新增UI组件

### 7.2 开发阶段

1. **阶段1**: MATLAB Worker模块
   - 实现MatlabWorkerThread类
   - MATLAB Engine调用封装
   - 超时保护和错误恢复
   - 单元测试

2. **阶段2**: UI集成
   - 新增心率显示区域
   - 新增场景选择控件
   - 信号槽连接

3. **阶段3**: 数据流集成
   - DataBuffer与线程锁集成
   - CSV双文件记录
   - 端到端测试

4. **阶段4**: 错误处理与优化
   - 降级运行模式
   - MATLAB引擎重启机制
   - 性能调优

### 7.3 测试计划

- **单元测试**: MatlabWorkerThread独立测试
- **集成测试**: 与串口数据联调
- **场景测试**: 三个运动场景参数切换
- **压力测试**: 长时间运行稳定性
- **故障恢复**: MATLAB崩溃后自动恢复

---

## 8. 依赖项

### 8.1 Python依赖

```
matlab>=R2021b  # MATLAB Engine API
numpy>=1.21.0   # 数据转换
PyQt5>=5.15.0
pyqtgraph>=0.12.0
```

### 8.2 MATLAB依赖

- MATLAB R2021b或更高版本
- Signal Processing Toolbox
- Statistics and Machine Learning Toolbox

### 8.3 MATLAB Engine API 安装步骤

**方法1: 使用MATLAB命令**
```matlab
% 在MATLAB命令窗口中执行
cd('C:\Program Files\MATLAB\R2021b\extern\engines\python')
system('python setup.py install')
```

**方法2: 使用Windows命令行**
```cmd
cd "C:\Program Files\MATLAB\R2021b\extern\engines\python"
python setup.py install
```

**验证安装**:
```python
# 在Python中测试
import matlab.engine
eng = matlab.engine.start_matlab()
result = eng.sqrt(16)
print(result)  # 应输出 4.0
eng.quit()
```

**故障排除**:
- 如果找不到`matlabroot`，手动使用完整路径
- 确保Python版本与MATLAB兼容（3.7-3.9）
- 如果有多个Python环境，确保安装到正确的环境

---

## 9. 附录

### 9.1 MATLAB参数文件格式与处理

#### 实际参数文件

当前可用的参数文件：
```
Best_Params_20260119dualtiaosheng1_processed.mat
├── Best_Para_HF (struct)
└── Best_Para_ACC (struct)
```

#### 文件名格式问题

**问题说明**:
- `OnlineHeartRateSolver.m`第18行期望的文件名格式为：`Best_Params_Result_{scenario_name}.mat`
- 实际文件名格式为：`Best_Params_20260119dualtiaosheng1_processed.mat`
- 两者不匹配会导致参数加载失败，回退到默认`para_base`

#### 解决方案（推荐）

**方案1: 创建符号链接（Windows）**

在`matlab`目录中以管理员身份运行：
```cmd
mklink Best_Params_Result_tiaosheng.mat Best_Params_20260119dualtiaosheng1_processed.mat
```

**方案2: 复制文件**

```cmd
copy Best_Params_20260119dualtiaosheng1_processed.mat Best_Params_Result_tiaosheng.mat
```

**方案3: 修改MATLAB代码（不推荐）**

修改`OnlineHeartRateSolver.m`第18行的文件名生成逻辑以适应实际文件名格式。

#### 实施建议

为支持三个场景，建议创建以下文件：
```
matlab/
├── Best_Params_Result_tiaosheng.mat  → 符号链接到 Best_Params_20260119dualtiaosheng1_processed.mat
├── Best_Params_Result_bobi.mat       → 符号链接到 Best_Params_20260119dualtiaosheng1_processed.mat
└── Best_Params_Result_kaihe.mat      → 符号链接到 Best_Params_20260119dualtiaosheng1_processed.mat
```

这样三个场景可以共享同一套参数，后续生成专用参数后只需更新对应的符号链接。

### 9.2 关键常量

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

### 9.3 场景名称映射

**Python场景名 → MATLAB场景名 → 参数文件**

| Python下拉框选项 | MATLAB场景名 | 期望参数文件 | 实际指向 |
|-----------------|-------------|-------------|---------|
| tiaosheng (跳绳) | tiaosheng | `Best_Params_Result_tiaosheng.mat` | 符号链接 → `Best_Params_20260119dualtiaosheng1_processed.mat` |
| bobi (波比跳) | bobi | `Best_Params_Result_bobi.mat` | 符号链接 → `Best_Params_20260119dualtiaosheng1_processed.mat` |
| kaihe (开合跳) | kaihe | `Best_Params_Result_kaihe.mat` | 符号链接 → `Best_Params_20260119dualtiaosheng1_processed.mat` |

**说明**:
- Python场景名直接作为MATLAB的`scenario_name`参数传入
- MATLAB代码会自动查找`Best_Params_Result_{scenario_name}.mat`文件
- 通过符号链接，三个场景暂时共享同一套参数
- 后续生成场景专用参数后，只需更新符号链接目标

---

## 10. 设计修订记录

| 版本 | 日期 | 修订内容 |
|------|------|---------|
| 1.0 | 2026-03-23 | 初始版本 |
| 1.1 | 2026-03-23 | 修复审查发现的问题：参数文件名、MATLAB接口、线程安全、超时机制、CSV记录策略 |
| 1.2 | 2026-03-23 | 解决参数文件名格式不匹配问题：添加符号链接方案，更新场景映射表 |

---

**文档版本**: 1.2
**最后更新**: 2026-03-23
