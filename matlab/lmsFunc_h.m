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

% K = 0;
% 如果传入了有效的 w_init 则继承，否则初始化为 0（支持跨窗口权重持久化）
if nargin < 6 || isempty(w_init)
    w=zeros(M+K,1); %This is a vertical column
else
    % 确保权重维度匹配（如果滤波器阶数变化则截断或补零）
    if length(w_init) >= M+K
        w = w_init(1:M+K);
    else
        w = [w_init; zeros(M+K-length(w_init), 1)];
    end
end

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