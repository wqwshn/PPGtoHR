# PPG心率检测算法系统

## 项目概述

这是一个基于PPG（光电容积脉搏波）信号的实时心率检测系统，结合了Python上位机和MATLAB强大的信号处理算法。该系统采用多线程架构，实现了从原始数据采集到心率解算的完整流程，支持多种运动场景的自适应处理。

## 项目结构

```
D:\data\PPG_HeartRate\Algorithm\ALL\
├── README.md                          # 项目说明文档
├── .gitignore                         # Git忽略文件
├── setup_ppg_prj.ps1                 # Windows环境配置脚本
├── python/                            # Python上位机代码
│   ├── __pycache__/                  # Python缓存
│   ├── environment.yml                # Conda环境配置
│   ├── getdata.py                    # 主程序（PyQt5界面）
│   └── matlab_worker.py              # MATLAB计算工作线程
├── matlab/                            # MATLAB算法代码
│   ├── OnlineHeartRateSolver.m       # 主算法类（已实现）
│   ├── ChooseDelay1218.m            # 时延估计与互相关
│   ├── FFT_Peaks.m                  # 频域转换与谱峰提取
│   ├── Find_maxpeak.m                # 寻峰算法
│   ├── Find_nearBiggest.m            # 历史轨迹就近寻峰追踪
│   ├── Find_realHR.m                # 最终心率计算
│   ├── lmsFunc_h.m                  # LMS自适应滤波器核心
│   ├── PpgPeace.m                   # PPG信号质量评估
│   ├── Best_Params_*.mat            # 场景参数文件
│   └── OnlineHeartRateSolver.m说明文档.md  # 算法说明
├── tests/                            # 测试代码
│   ├── test_matlab_integration.py    # MATLAB集成测试
│   └── test_matlab_worker.py         # MatlabWorkerThread测试
└── docs/                             # 文档
    └── superpowers/                  # 开发计划文档
```

## 技术栈

### 硬件平台
- 传感器：支持21字节帧格式的PPG/ACC传感器
- 串口通信：115200波特率，RTS/CTS硬件流控

### 软件技术栈
- **Python 3.9.25**（使用Conda环境）
- **PyQt5 5.15.11** - GUI框架
- **pyqtgraph 0.13.7** - 实时绘图
- **numpy 2.0.2** - 数值计算
- **pyserial 3.5** - 串口通信
- **MATLAB Engine 9.11.0 (R2021b)** - 算法引擎

### MATLAB工具箱
- Signal Processing Toolbox
- Statistics and Machine Learning Toolbox

## 核心功能

### 1. 数据采集
- 实时采集PPG信号（绿光、红光、红外）
- 三轴加速度计数据
- 4路热膜传感器数据
- 温度传感器数据
- 采样率：约125Hz

### 2. 心率解算算法

**双路并行处理架构**：
- **HF路径**：基于HF参考信号的自适应滤波
- **ACC路径**：基于加速度信号的运动检测

**核心特性**：
- 8秒滑动窗口，1秒步进更新
- 自动运动状态检测（静息/运动）
- LMS自适应滤波 + FFT频谱分析动态切换
- 贝叶斯参数寻优支持
- 多场景参数配置

### 3. 可视化界面
- **实时波形显示**：PPG、ACC、HF信号
- **心率监测**：HF和ACC双路径心率显示
- **运动状态指示**：静息/运动状态
- **心率趋势图**：最近60秒心率变化
- **场景选择**：tiaosheng（跳绳）、bobi（波比跳）、kaihe（开合跳）

### 4. 数据记录
- **双文件CSV记录**：
  - `SensorData_*.csv`：125Hz原始传感器数据
  - `HeartRate_*.csv`：1Hz心率计算结果
- 时间戳同步，便于后续分析

## 数据流向

```
硬件传感器 → 串口(125Hz) → Python解析 → DataBuffer(8秒缓存)
                                                 ↓
                                        MatlabWorkerThread(1秒触发)
                                                 ↓
                                        MATLAB Engine API
                                                 ↓
                              OnlineHeartRateSolver.process_step()
                                                 ↓
                         ┌───────────────────────────┴─────────────────────┐
                         ↓                                                   ↓
                 HR_HF, HR_ACC(Hz)                               Motion_Flag
                         ↓                                                   ↓
                         ┌───────────────────────────────────────────────────────┐
                         ↓                                               ↓
                UI数值显示更新                                    CSV记录
                (HF, ACC, 状态)                                (HeartRate_*.csv)
                         ↓
                 波形图更新
                 (pyqtgraph PlotWidget)
```

## 安装与配置

### 1. 环境准备
```bash
# 创建Conda环境
conda create -n ppg_prj python=3.9
conda activate ppg_prj

# 安装Python依赖
pip install pyqt5 pyqtgraph numpy pyserial
```

### 2. MATLAB Engine安装
运行提供的PowerShell脚本：
```powershell
.\setup_ppg_prj.ps1
```
或在MATLAB中手动安装：
```matlab
cd('C:\Program Files\MATLAB\R2021b\extern\engines\python')
system('python setup.py install')
```

### 3. 验证安装
```python
# 测试Python程序
cd python
python getdata.py

# 运行测试
cd ..
python tests/test_matlab_integration.py
```

## 使用说明

### 1. 启动程序
```bash
conda activate ppg_prj
cd python
python getdata.py
```

### 2. 基本操作
1. **选择串口**：从下拉框选择正确的COM端口
2. **打开串口**：点击"打开串口"按钮开始数据采集
3. **观察数据**：
   - 左侧面板显示实时波形和状态信息
   - 采样率应显示约125Hz
   - 心率区域在数据积累8秒后开始显示结果
4. **选择场景**：从"算法设置"下拉框选择运动场景
5. **开始记录**：点击"开始记录"保存数据到CSV文件

### 3. 心率监测说明
- **HF路径**：绿色显示，主要心率输出
- **ACC路径**：灰色显示，运动状态参考
- **运动状态**：根据ACC方差动态切换
- **心率校准**：启动后需要约8秒的数据积累

### 4. 场景说明
- **tiaosheng**：跳绳场景
- **bobi**：波比跳场景
- **kaihe**：开合跳场景

每个场景有不同的参数配置，优化对应运动状态下的心率检测精度。

## 算法详解

### OnlineHeartRateSolver工作流程

1. **初始化**
   - 加载贝叶斯优化参数（根据场景）
   - 初始化8秒数据缓存
   - 设置双路独立处理状态

2. **数据接收**
   - 持续接收原始数据（125Hz）
   - 维护滑动窗口缓存

3. **双路解算**
   - HF路径：使用HF参考信号，LMS+FFT混合
   - ACC路径：使用加速度信号，检测运动状态

4. **决策融合**
   - 根据运动状态选择最优输出
   - 历史数据平滑滤波

5. **结果输出**
   - 双路心率结果（Hz）
   - 运动状态标志
   - 置信度评分

### 核心算法组件

- **LMS自适应滤波**：`lmsFunc_h.m`，处理运动伪影
- **频域分析**：`FFT_Peaks.m`，提取心率频率
- **时延估计**：`ChooseDelay1218.m`，信号对齐
- **寻峰算法**：`Find_maxpeak.m`，频率峰值检测
- **质量评估**：`PpgPeace.m`，信号质量评分

## 测试验证

### 单元测试
- `test_matlab_worker.py`：测试MatlabWorkerThread功能
- `test_matlab_integration.py`：测试MATLAB集成

### 集成测试
1. **MATLAB Engine启动测试**
   - 验证Engine正常启动
   - 参数文件加载验证
   - OnlineHeartRateSolver初始化

2. **数据流测试**
   - 串口数据接收
   - DataBuffer写入/读取
   - MATLAB调用与结果返回

3. **UI集成测试**
   - 心率数值显示更新
   - 波形图绘制
   - 场景切换功能

## 故障排除

### 常见问题

1. **MATLAB Engine未安装**
   - 错误提示：ModuleNotFoundError: No module named 'matlab.engine'
   - 解决：运行`setup_ppg_prj.ps1`脚本

2. **参数文件加载失败**
   - 错误提示：Warning: 未找到参数文件
   - 解决：确保`Best_Params_Result_*.mat`文件存在

3. **心率显示为"----"**
   - 原因：数据不足或MATLAB计算失败
   - 解决：检查串口连接，等待8秒数据积累

4. **程序卡顿**
   - 原因：MATLAB计算阻塞主线程
   - 解决：MatlabWorkerThread独立线程已实现

5. **CSV文件未生成**
   - 原因：保存路径权限问题
   - 解决：选择有写入权限的目录

### 性能指标

| 指标 | 目标值 | 实际值 |
|------|--------|--------|
| 心率更新延迟 | < 1秒 | 1秒（定时器触发） |
| UI响应性 | < 50ms | 优秀（独立线程） |
| 内存占用 | < 500MB | 约300MB |
| CPU占用 | < 30% | 约15-25% |

## 开发文档

详细的设计和开发文档位于`docs/superpowers/`目录：
- `2026-03-23-python-matlab-heart-rate-integration-design.md`：设计文档
- `2026-03-23-python-matlab-heart-rate-integration.md`：实施计划
- `2026-03-23-python-matlab-heart-rate-integration-implementation-summary.md`：实施总结

## 许可证

本项目仅用于学术研究和技术学习。

## 联系方式

如有问题或建议，请通过以下方式联系：
- 项目路径：`D:\data\PPG_HeartRate\Algorithm\ALL`
- Git仓库：https://github.com/wqwsnh/PPG_data_get

---

**最后更新**：2026-03-23
**版本**：v1.0
