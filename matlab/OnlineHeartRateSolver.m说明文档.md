# OnlineHeartRateSolver.m说明文档

## 1. 算法简介
本算法为 `OnlineHeartRateSolver` 类，旨在将基于贝叶斯参数寻优的离线心率解算代码转化为支持实时数据流入（Online 流式处理）的版本。
算法内部自动维护 8 秒滑动数据缓存，且**严格保留了双路并行对比功能**：在任意时刻，均会利用已优化的 HF 最佳参数组和 ACC 最佳参数组进行两次独立的解算，并根据内部运动阈值（实时 ACC 方差判断）动态切换自适应滤波（LMS）与快速傅里叶变换（FFT）的输出。

## 2. 依赖函数清单
请确保以下自定义函数放置于调用方相同的 MATLAB 路径中：
* `ChooseDelay1218.m` （时延估计与互相关）
* `lmsFunc_h.m` （LMS自适应滤波器核心）
* `FFT_Peaks.m` （频域转换与谱峰提取）
* `Find_maxpeak.m` （寻峰算法）
* `Find_nearBiggest.m` （历史轨迹就近寻峰追踪）

## 3. 使用流程示例

### 步骤一：配置基础参数并实例化对象
由于不同运动状态可能导致寻优失败，需提供一个 `para_base` 作为兜底（Fallback）。
实例化时，指定当前测试的运动场景，系统将自动加载当前目录下的 `.mat` 参数文件。

```matlab
% 1. 准备基础兜底参数
para_base.Calib_Time = 60;
para_base.Motion_Th_Scale = 3;
para_base.Spec_Penalty_Enable = 1; 
para_base.Spec_Penalty_Weight = 0.2;
% （建议按需填入其他离线搜索空间中的安全默认值）

% 2. 实例化求解器，尝试加载离线训练的最优参数
% 若场景名为 'tiaosheng'，需确保存在文件 'Best_Params_Result_tiaosheng.mat'
scenario_name = 'tiaosheng'; 
hr_solver = OnlineHeartRateSolver(scenario_name, para_base);
```

### 步骤二：流式数据送入与解算提取

推荐以上位机读取串口数据的频率（如每 1 秒，即 125 个采样点）调用一次接口。算法在未满 8 秒时不会返回值。

Matlab

```
% 模拟串口不断接收到的新数据 (矩阵必须严格为 N行 7列)
% 顺序：[PPG, HF1, HF2, HF3, ACCx, ACCy, ACCz]

% 循环模拟实时输入
while has_new_data
    new_data_chunk = read_from_serial(); % 获取新的一段数据
    
    % 送入算法引擎
    [results, is_ready] = hr_solver.process_step(new_data_chunk);
    
    if is_ready
        % 打印或绘制当前的心率（转换为 BPM）
        bpm_hf = results.Final_HR_HF * 60;
        bpm_acc = results.Final_HR_ACC * 60;
        
        fprintf('HF融合路径心率: %.1f BPM | 运动状态: %d\n', bpm_hf, results.Motion_Flag_HF_Path);
        fprintf('ACC融合路径心率: %.1f BPM | 运动状态: %d\n', bpm_acc, results.Motion_Flag_ACC_Path);
    end
end
```

## 4. 核心逻辑备注

- **LMS 预热与热切**：自适应滤波器会在后台不间断计算（不断更迭收敛权重）。仅在 ACC 方差突破标定阈值时，引擎才会提取 LMS 的估计频率作为输出；否则输出纯 FFT 频率。
- **双路完全解耦**：HF 路径和 ACC 路径使用独立的重采样器、平滑队列和追踪记录，相互之间毫无干扰。