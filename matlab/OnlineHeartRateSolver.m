classdef OnlineHeartRateSolver < handle
    %% OnlineHeartRateSolver - 实时心率解算类 (双路并行版)
    % 功能：同时运行 HF 和 ACC 两种最优参数配置，支持持续 LMS 与运动状态按需切换。
    
    properties
        Para_HF         % 针对 HF 参考信号优化的参数
        Para_ACC        % 针对 ACC 参考信号优化的参数
        State           % 内部运行状态 (缓存、历史、平滑队列等)
    end
    
    methods
        function obj = OnlineHeartRateSolver(scenario_name, para_base)
            % 构造函数
            obj.Para_HF = para_base;
            obj.Para_ACC = para_base;

            % 1. 加载贝叶斯优化出的双路参数
            mat_filename = sprintf('Best_Params_Result_%s.mat', scenario_name);
            if isfile(mat_filename)
                load(mat_filename, 'Best_Para_HF', 'Best_Para_ACC');
                % 合并加载的参数与基础参数，确保所有字段都存在
                obj.Para_HF = obj.merge_params(para_base, Best_Para_HF);
                obj.Para_ACC = obj.merge_params(para_base, Best_Para_ACC);
                fprintf('成功加载 [%s] 场景的双路最优参数。\n', scenario_name);
            else
                warning('未找到 %s 参数文件，双路均将使用基础参数运行。', scenario_name);
            end
            
            % 2. 初始化全局状态 (基于 125Hz 原始输入)
            obj.State.Fs_Origin = 125; 
            obj.State.Win_Len_Sec = 8;
            obj.State.Win_Step_Sec = 1;
            obj.State.Max_Buffer_Len = obj.State.Win_Len_Sec * obj.State.Fs_Origin;
            obj.State.Step_Len = obj.State.Win_Step_Sec * obj.State.Fs_Origin;
            
            % 原始数据缓存 [PPG, HF1, HF2, HF3, ACCx, ACCy, ACCz]
            obj.State.RawBuffer = []; 
            obj.State.Times_Count = 0; 
            
            % 3. 初始化双路独立追踪状态
            % 历史心率
            obj.State.Hist_LMS_HF = 0; obj.State.Hist_FFT_HF = 0;
            obj.State.Hist_LMS_ACC = 0; obj.State.Hist_FFT_ACC = 0;
            
            % 独立运动阈值校准缓存
            obj.State.Calib_Buffer_HF = []; obj.State.Is_Calibrated_HF = false;
            obj.State.Calib_Buffer_ACC = []; obj.State.Is_Calibrated_ACC = false;
            obj.State.Th_ACC_for_HF = 0.5; % 默认安全阈值
            obj.State.Th_ACC_for_ACC = 0.5;
            
            % 独立平滑队列
            obj.State.Smooth_Queue_HF = [];
            obj.State.Smooth_Queue_ACC = [];
        end
        
        function [hr_results, is_ready] = process_step(obj, new_raw_data)
            % 接收流式数据并触发计算
            hr_results = struct();
            is_ready = false;
            
            if isempty(new_raw_data), return; end
            
            % 拼接到缓存
            obj.State.RawBuffer = [obj.State.RawBuffer; new_raw_data];
            
            % 当满足 8 秒窗口时触发解算
            if size(obj.State.RawBuffer, 1) >= obj.State.Max_Buffer_Len
                win_data = obj.State.RawBuffer(1:obj.State.Max_Buffer_Len, :);
                obj.State.Times_Count = obj.State.Times_Count + 1;
                
                % ==========================================
                % 分别执行两条独立的最优参数路径
                % ==========================================
                % 路径 1: 基于 HF 参数的解算
                out_HF = obj.compute_single_path(win_data, obj.Para_HF, 'HF');
                
                % 路径 2: 基于 ACC 参数的解算
                out_ACC = obj.compute_single_path(win_data, obj.Para_ACC, 'ACC');
                
                % ==========================================
                % 平滑后处理
                % ==========================================
                obj.State.Smooth_Queue_HF = [obj.State.Smooth_Queue_HF, out_HF.Fusion_HR];
                obj.State.Smooth_Queue_ACC = [obj.State.Smooth_Queue_ACC, out_ACC.Fusion_HR];
                
                if length(obj.State.Smooth_Queue_HF) > obj.Para_HF.Smooth_Win_Len
                    obj.State.Smooth_Queue_HF(1) = [];
                end
                if length(obj.State.Smooth_Queue_ACC) > obj.Para_ACC.Smooth_Win_Len
                    obj.State.Smooth_Queue_ACC(1) = [];
                end
                
                % 打包最终输出
                hr_results.Final_HR_HF  = median(obj.State.Smooth_Queue_HF);
                hr_results.Final_HR_ACC = median(obj.State.Smooth_Queue_ACC);
                hr_results.Motion_Flag_HF_Path = out_HF.Is_Motion;
                hr_results.Motion_Flag_ACC_Path = out_ACC.Is_Motion;
                
                is_ready = true;
                
                % 滑动窗口：剔除最旧的 1 秒数据
                obj.State.RawBuffer(1:obj.State.Step_Len, :) = [];
            end
        end
    end

    methods (Access = private)
        function merged = merge_params(obj, base_params, loaded_params)
            % 合并参数：使用loaded_params的值，但保留base_params中缺失的字段
            merged = base_params;
            loaded_fields = fieldnames(loaded_params);
            for i = 1:length(loaded_fields)
                field = loaded_fields{i};
                merged.(field) = loaded_params.(field);
            end
        end

        function out = compute_single_path(obj, raw_win, para, path_type)
            % 核心解算：针对特定的参数集合执行完整算法流程
            Fs = para.Fs_Target;
            Fs_Ori = obj.State.Fs_Origin;
            
            % 1. 独立重采样与滤波 (保证即便 Fs_Target 不同也能完美复刻)
            % 确保数据是列向量格式
            ppg_col   = double(raw_win(:, 1));
            hotf1_col = double(raw_win(:, 2));
            hotf2_col = double(raw_win(:, 3));
            hotf3_col = double(raw_win(:, 4));
            accx_col  = double(raw_win(:, 5));
            accy_col  = double(raw_win(:, 6));
            accz_col  = double(raw_win(:, 7));

            % 确保是列向量（即使输入是行向量）
            if size(ppg_col, 2) > 1, ppg_col = ppg_col(:); end
            if size(hotf1_col, 2) > 1, hotf1_col = hotf1_col(:); end
            if size(hotf2_col, 2) > 1, hotf2_col = hotf2_col(:); end
            if size(hotf3_col, 2) > 1, hotf3_col = hotf3_col(:); end
            if size(accx_col, 2) > 1, accx_col = accx_col(:); end
            if size(accy_col, 2) > 1, accy_col = accy_col(:); end
            if size(accz_col, 2) > 1, accz_col = accz_col(:); end

            % 处理离群值（使用安全的填充方式）
            try
                ppg_cleaned = filloutliers(ppg_col, 'previous', 'mean');
            catch
                ppg_cleaned = ppg_col;  % 如果filloutliers失败，使用原始数据
            end

            ppg_ori   = resample(ppg_cleaned, Fs, Fs_Ori);
            hotf1_ori = resample(hotf1_col, Fs, Fs_Ori);
            hotf2_ori = resample(hotf2_col, Fs, Fs_Ori);
            hotf3_ori = resample(hotf3_col, Fs, Fs_Ori);
            accx_ori  = resample(accx_col,  Fs, Fs_Ori);
            accy_ori  = resample(accy_col,  Fs, Fs_Ori);
            accz_ori  = resample(accz_col,  Fs, Fs_Ori);
            
            % 动态生成对应 Fs_Target 的滤波器
            [b_but, a_but] = butter(4, [0.5 5]/(Fs/2), 'bandpass');
            ppg   = filtfilt(b_but, a_but, ppg_ori);
            hotf1 = filtfilt(b_but, a_but, hotf1_ori);
            hotf2 = filtfilt(b_but, a_but, hotf2_ori);
            hotf3 = filtfilt(b_but, a_but, hotf3_ori);
            accx  = filtfilt(b_but, a_but, accx_ori);
            accy  = filtfilt(b_but, a_but, accy_ori);
            accz  = filtfilt(b_but, a_but, accz_ori);
            
            Sig_h = {hotf1, hotf2, hotf3};
            Sig_a = {accx, accy, accz};
            acc_mag = sqrt(accx.^2 + accy.^2 + accz.^2);
            
            % 2. 动态运动阈值校准 (隔离状态)
            is_calib = obj.State.(['Is_Calibrated_' path_type]);
            if ~is_calib
                chunk_len = round(Fs / obj.State.Win_Step_Sec);
                obj.State.(['Calib_Buffer_' path_type]) = [obj.State.(['Calib_Buffer_' path_type]); acc_mag(1:chunk_len)];
                % 更新阈值
                current_std = std(obj.State.(['Calib_Buffer_' path_type]));
                obj.State.(['Th_ACC_for_' path_type]) = para.Motion_Th_Scale * current_std;
                % 检查是否满 60 秒
                if length(obj.State.(['Calib_Buffer_' path_type])) >= (para.Calib_Time * Fs)
                    obj.State.(['Is_Calibrated_' path_type]) = true;
                end
            end
            
            is_motion = std(acc_mag) > obj.State.(['Th_ACC_for_' path_type]);
            
            % 3. 互相关与时延计算
            [mh1,mh2,mh3,ma1,ma2,ma3,time_delay_h,time_delay_a] = ...
                ChooseDelay1218(Fs, 1, ppg, accx, accy, accz, hotf1, hotf2, hotf3);
            
            LMS_Mu_Base = 0.01;
            times = obj.State.Times_Count;
            
            % 4. 连续执行 LMS 与 FFT (后台始终运行)
            if strcmp(path_type, 'HF')
                %% --- HF 路径解算 ---
                Sig_LMS = ppg;
                if time_delay_h(1) < 0, ord = floor(abs(time_delay_h(1))*1); else, ord = 1; end
                ord = min(max(ord, 1), para.Max_Order);
                
                mh_mat = sort([mh1,mh2,mh3], 'descend');
                best_idx = find([mh1,mh2,mh3] == mh_mat(1), 1); 
                
                for i = 1:2
                    curr_corr = mh_mat(i);
                    real_idx = find([mh1,mh2,mh3] == curr_corr, 1);
                    [Sig_LMS,~,~] = lmsFunc_h(LMS_Mu_Base - curr_corr/100, ord, 0, Sig_h{real_idx}, Sig_LMS);
                end
                
                Freq_LMS = Helper_Process_Spectrum(Sig_LMS, Sig_h{best_idx}, Fs, para, times, ...
                    obj.State.Hist_LMS_HF, true, para.HR_Range_Hz, para.Slew_Limit_BPM, para.Slew_Step_BPM);
                
                obj.State.Hist_LMS_HF = Freq_LMS; % 更新历史
                
            else
                %% --- ACC 路径解算 ---
                Sig_LMS = ppg;
                if time_delay_a < 0, ord = floor(abs(time_delay_a)*1.5); else, ord = 1; end
                ord = min(max(ord, 1), para.Max_Order);
                
                ma_mat = sort([ma1,ma2,ma3], 'descend');
                for i = 1:3
                    curr_corr = ma_mat(i);
                    real_idx = find([ma1,ma2,ma3] == curr_corr, 1); 
                    Ref_Sig = Sig_a{real_idx};
                    [Sig_LMS,~,~] = lmsFunc_h(LMS_Mu_Base - curr_corr/100, ord, 1, Ref_Sig, Sig_LMS);
                end
                
                Freq_LMS = Helper_Process_Spectrum(Sig_LMS, Sig_a{3}, Fs, para, times, ...
                    obj.State.Hist_LMS_ACC, true, para.HR_Range_Hz, para.Slew_Limit_BPM, para.Slew_Step_BPM);
                    
                obj.State.Hist_LMS_ACC = Freq_LMS; % 更新历史
            end
            
            %% --- 纯 FFT 路径解算 (带有惩罚对比) ---
            Sig_FFT = ppg - mean(ppg);
            Sig_FFT = Sig_FFT .* hamming(length(Sig_FFT));
            
            Freq_FFT = Helper_Process_Spectrum(Sig_FFT, Sig_a{3}, Fs, para, times, ...
                obj.State.(['Hist_FFT_' path_type]), true, para.HR_Range_Rest, para.Slew_Limit_Rest, para.Slew_Step_Rest);
                
            obj.State.(['Hist_FFT_' path_type]) = Freq_FFT; % 更新历史
            
            %% 5. 运动/静息融合开关
            if is_motion
                Fusion_HR = Freq_LMS; % 运动状态：切入自适应滤波
            else
                Fusion_HR = Freq_FFT; % 静息状态：切回纯频域追踪
            end
            
            out.Fusion_HR = Fusion_HR;
            out.Is_Motion = is_motion;
        end
    end
end

%% ====== 核心辅助子函数 ======
function est_freq = Helper_Process_Spectrum(sig_in, sig_penalty_ref, Fs, para, times, prev_hr, enable_penalty, range_hz, limit_bpm, step_bpm)
    [S_rls, S_rls_amp] = FFT_Peaks(sig_in, Fs, 0.3);
    
    if para.Spec_Penalty_Enable && enable_penalty
        [S_ref, S_ref_amp] = FFT_Peaks(sig_penalty_ref, Fs, 0.3);
        if ~isempty(S_ref)
            [~, midx] = max(S_ref_amp); 
            Motion_Freq = S_ref(midx);
            mask = (abs(S_rls - Motion_Freq) < para.Spec_Penalty_Width) | ...
                   (abs(S_rls - 2*Motion_Freq) < para.Spec_Penalty_Width);
            S_rls_amp(mask) = S_rls_amp(mask) * para.Spec_Penalty_Weight;
        end
    end
    
    Fre = Find_maxpeak(S_rls, S_rls, S_rls_amp);
    if isempty(Fre), Fre = 0; end
    curr_raw = Fre(1);
    
    if times == 1
        est_freq = curr_raw;
    else
        [calc_hr, ~] = Find_nearBiggest(Fre, prev_hr, range_hz, -range_hz);
        diff_hr = calc_hr - prev_hr;
        limit   = limit_bpm / 60;
        step    = step_bpm / 60; 
        
        if diff_hr > limit,      est_freq = prev_hr + step;
        elseif diff_hr < -limit, est_freq = prev_hr - step;
        else,                    est_freq = calc_hr;
        end
    end
end