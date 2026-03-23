# tests/test_matlab_integration.py
"""
MATLAB集成测试 - 验证OnlineHeartRateSolver可以正常工作
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'python')))

import matlab.engine

def test_matlab_solver():
    """测试MATLAB OnlineHeartRateSolver"""
    print("=" * 50)
    print("MATLAB 集成测试")
    print("=" * 50)

    try:
        # 启动MATLAB Engine
        print("\n启动 MATLAB Engine...")
        eng = matlab.engine.start_matlab()
        print("PASS: MATLAB Engine 启动成功")

        # 设置工作路径
        matlab_path = r'D:\data\PPG_HeartRate\Algorithm\ALL\matlab'
        eng.cd(matlab_path)
        print(f"PASS: 工作路径设置为 {matlab_path}")

        # 检查参数文件是否存在
        print("\n检查参数文件...")
        scenarios = ['tiaosheng', 'bobi', 'kaihe']
        for scenario in scenarios:
            param_file = f'Best_Params_Result_{scenario}.mat'
            try:
                # 使用MATLAB的exist函数检查文件
                result = eng.exist(param_file)
                if result > 0:
                    print(f"  PASS: {param_file} 存在")
                else:
                    print(f"  FAIL: {param_file} 不存在")
            except Exception as e:
                print(f"  ERROR: 检查 {param_file} 失败 - {e}")

        # 测试求解器初始化
        print("\n测试求解器初始化...")
        try:
            # 创建默认参数结构体 (使用MATLAB eval)
            para = eng.eval("struct('Fs_Target',100,'HR_Range_Hz',[0.67,3.0],'Smooth_Win_Len',5,'Calib_Time',60)")

            # 初始化求解器
            solver = eng.OnlineHeartRateSolver('tiaosheng', para)
            print("PASS: OnlineHeartRateSolver 初始化成功")

            # 创建测试数据 (125个点, 7列)
            print("\n测试数据输入...")
            import numpy as np
            test_data = np.random.rand(125, 7) * 1000
            mat_data = matlab.double(test_data.tolist())

            # 调用process_step (使用feval调用MATLAB对象方法)
            # feval语法: feval('function_name', obj, args..., nargout=N)
            result = eng.feval('process_step', solver, mat_data, nargout=2)
            is_ready = bool(result[1])
            hr_results = result[0]
            print(f"PASS: process_step 执行成功 (is_ready={is_ready})")

            if is_ready:
                hr_hf = float(hr_results['Final_HR_HF']) * 60
                hr_acc = float(hr_results['Final_HR_ACC']) * 60
                print(f"  结果: HF={hr_hf:.1f} BPM, ACC={hr_acc:.1f} BPM")
            else:
                print("  结果: 数据校准中...")

        except Exception as e:
            print(f"FAIL: 求解器测试失败 - {e}")
            import traceback
            traceback.print_exc()

        # 清理
        print("\n清理资源...")
        eng.quit()
        print("PASS: MATLAB Engine 已关闭")

    except ImportError:
        print("SKIP: MATLAB Engine API未安装")
        print("请先运行: python setup_ppg_prj.ps1")
    except Exception as e:
        print(f"ERROR: 测试失败 - {e}")
        import traceback
        traceback.print_exc()

    print()
    print("=" * 50)
    print("集成测试完成")
    print("=" * 50)

if __name__ == '__main__':
    test_matlab_solver()
