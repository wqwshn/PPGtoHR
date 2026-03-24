function [e,w,ee]=lmsFunc_h(mu,M,K,u,d,w_init)
% Normalized LMS
% Call:
% [e,w]=nlms(mu,M,u,d,a);
%
% Input arguments:
% mu = step size, dim 1x1  步长
% M = filter length, dim 1x1 FIR阶数
% u = input signal, dim Nx1  加速度信号
% d = desired signal, dim Nx1   ppg信号
% K = constant, dim 1x1    一个常数
% w_init = initial filter coefficients (optional), dim (M+K)x1  初始权重
%
% Output arguments:
% e = estimation error, dim Nx1    d(n)-y(n)
% w = final filter coefficients, dim Mx1    最终的FIR系数

u = zscore(u);
d = zscore(d);

% =======================================================
% 核心修改：动态阶数安全对齐机制
% =======================================================
target_len = M + K;               % 当前帧所需的标准滤波器长度
w = zeros(target_len, 1);         % 预分配正确维度的零矩阵

% 如果传入了历史权重，则进行安全继承
if nargin >= 6 && ~isempty(w_init)
    w_init = w_init(:);           % 确保输入为列向量
    % 取当前所需长度与历史长度的最小值
    copy_len = min(length(w_init), target_len);

    % 安全赋值：若降阶则自动截断尾部，若升阶则尾部保持为 0
    w(1:copy_len) = w_init(1:copy_len);
end
% =======================================================

%input signal length
N=length(u);
%make sure that u and d are colon vectors
u=u(:);
d=d(:);
%NLMS
ee=zeros(1,N);
for n=M:N-K %Start at M (Filter Length) and Loop to N (Length of Sample)
    uvec=u(n+K:-1:n-M+1); %Array, start at n, decrement to n-m+1
    e(n)=d(n)-w'*uvec;
    w=w+2*mu*uvec*e(n);
    % y(n) = w'*uvec; %In ALE, this will be the narrowband noise.
end