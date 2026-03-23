# Python-MATLAB 心率解算集成设计文档

**日期**: 2026-03-23
**状态**: 已批准
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

**实现**: `collections.deque(maxlen=1000)`

**数据结构**: 每个元素为7元组
```python
(ppg_green, ut1, ut2, 0, accx, accy, accz)
#            PPG   HF1  HF2  HF3  ACCx ACCy ACCz
#            第三路HF置零，满足MATLAB接口要求
```

**线程安全**: `deque`是线程安全的，无需额外锁

### 3.2 MatlabWorkerThread（MATLAB工作线程）

**职责**:
1. 启动MATLAB Engine
2. 初始化`OnlineHeartRateSolver`
3. 每1秒触发一次计算
4. 通过信号槽返回结果

**核心接口**:
```python
class MatlabWorkerThread(QThread):
    # 信号定义
    hr_ready = pyqtSignal(float, float, bool)  # HR_HF, HR_ACC, is_motion
    error_occurred = pyqtSignal(str)

    # 初始化MATLAB求解器
    def init_solver(self, scenario_name: str)

    # 设置数据缓存引用
    def set_data_buffer(self, buffer: deque)

    # 启动/停止计算
    def start_calculation(self)
    def stop_calculation(self)
```

**计算流程**:
```python
def _process_step(self):
    # 1. 从DataBuffer取最新125个点
    data = list(self.data_buffer)[-125:]
    if len(data) < 125:
        return  # 数据不足

    # 2. 转换为MATLAB格式
    mat_data = matlab.double(data)

    # 3. 调用MATLAB
    results = self.solver.process_step(mat_data)

    # 4. 发送信号
    if is_ready:
        hr_hf = results['Final_HR_HF'] * 60  # Hz → BPM
        hr_acc = results['Final_HR_ACC'] * 60
        self.hr_ready.emit(hr_hf, hr_acc, is_motion)
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
| Python场景名 | MATLAB参数文件 |
|--------------|----------------|
| tiaosheng | `Best_Params_Result_tiaosheng.mat` |
| bobi | `Best_Params_Result_bobi.mat` |
| kaihe | `Best_Params_Result_kaihe.mat` |

**切换逻辑**:
```python
def load_scenario(self):
    scenario_name = self.cb_scenario.currentText()

    # 停止当前计算
    self.matlab_worker.stop_calculation()

    # 重新初始化求解器
    self.matlab_worker.init_solver(scenario_name)

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

**Python接收**:
```python
hr_hf = results['Final_HR_HF'][0][0] * 60  # Hz → BPM
hr_acc = results['Final_HR_ACC'][0][0] * 60
is_motion = results['Motion_Flag_HF_Path'][0][0]
```

### 4.3 CSV记录扩展

**新增列**:
```csv
Time(s), Mode, Uc1(mV), Uc2(mV), Ut1(mV), Ut2(mV), AccX, AccY, AccZ,
PPG_Green, PPG_Red, PPG_IR, Temp(C), HR_HF(BPM), HR_ACC(BPM), Motion_State
```

**记录策略**:
- 传感器数据: 按采样频率记录（125Hz）
- 心率数据: 按1秒频率记录，每秒重复写入
- 未就绪时: 心率列写入 `-1`

---

## 5. 错误处理

### 5.1 MATLAB Engine连接失败

**检测时机**: 程序启动时

**处理策略**:
```python
try:
    import matlab.engine
    self.eng = matlab.engine.start_matlab()
except Exception as e:
    QMessageBox.warning(self, "MATLAB未连接",
        f"无法启动MATLAB Engine: {e}\n心率功能将不可用")
    self.matlab_available = False
```

**降级运行**: 主程序继续运行，仅禁用心率功能

### 5.2 数据不足

**场景**: 启动前8秒或数据断流

**UI处理**:
```python
if len(self.data_buffer) < 125:
    self.lbl_hr_hf.setText("HF: -- BPM")
    self.lbl_hr_acc.setText("ACC: -- BPM")
    self.lbl_status.setText("心率校准中...")
```

### 5.3 计算超时

**检测**: 单次计算超过2秒

**处理策略**:
```python
def _process_step_with_timeout(self):
    try:
        # 设置2秒超时
        result = self.solver.process_step(data, timeout=2.0)
    except TimeoutError:
        self.timeout_count += 1
        if self.timeout_count >= 3:
            self.error_occurred.emit("MATLAB计算连续超时，请检查状态")
```

### 5.4 线程安全

**DataBuffer**: `collections.deque` 天然线程安全

**信号槽通信**: PyQt跨线程安全

**MATLAB Engine**: 单线程串行调用，无需额外同步

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
   - 单元测试

2. **阶段2**: UI集成
   - 新增心率显示区域
   - 新增场景选择控件
   - 信号槽连接

3. **阶段3**: 数据流集成
   - DataBuffer集成
   - CSV记录扩展
   - 端到端测试

4. **阶段4**: 错误处理与优化
   - 超时保护
   - 降级运行
   - 性能调优

### 7.3 测试计划

- **单元测试**: MatlabWorkerThread独立测试
- **集成测试**: 与串口数据联调
- **场景测试**: 三个运动场景参数切换
- **压力测试**: 长时间运行稳定性

---

## 8. 依赖项

### 8.1 Python依赖

```
matlab>=R2021b  # MATLAB Engine API
PyQt5>=5.15.0
pyqtgraph>=0.12.0
```

### 8.2 MATLAB依赖

- MATLAB R2021b或更高版本
- Signal Processing Toolbox
- Statistics and Machine Learning Toolbox

### 8.3 安装步骤

```bash
# 1. 在MATLAB中安装Python引擎
cd "matlabroot\extern\engines\python"
python setup.py install

# 2. 验证安装
python -c "import matlab.engine; print('OK')"
```

---

## 9. 附录

### 9.1 MATLAB参数文件格式

```matlab
Best_Params_Result_tiaosheng.mat
├── Best_Para_HF (struct)
└── Best_Para_ACC (struct)
```

### 9.2 关键常量

```python
SAMPLE_RATE = 125          # Hz
WINDOW_SIZE = 8            # 秒
STEP_SIZE = 1              # 秒
BUFFER_LEN = 1000          # 125 * 8
HR_UPDATE_INTERVAL = 1000  # 毫秒
HEART_RATE_RANGE = (40, 200)  # BPM
```

---

**文档版本**: 1.0
**最后更新**: 2026-03-23
