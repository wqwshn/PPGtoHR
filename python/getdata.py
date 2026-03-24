import sys
import os
import time
import csv
import serial
import serial.tools.list_ports
from collections import deque
import threading
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QGridLayout, QLabel, QComboBox, QPushButton,
                             QLineEdit, QFileDialog, QGroupBox, QMessageBox)
from PyQt5.QtCore import QThread, pyqtSignal, QTimer, Qt
import pyqtgraph as pg
from matlab_worker import MatlabWorkerThread

# ---------------------------------------------------------
# 数据接收与解析线程 (后台)
# ---------------------------------------------------------
class SerialReaderThread(QThread):
    # 更新信号：增加绿光、红光、红外光、温度、当前模式字符串、丢包率
    data_received = pyqtSignal(float, float, float, float, float, float, float, float, float, float, float, str, float)
    error_occurred = pyqtSignal(str)

    def __init__(self, port, baudrate, data_buffer, data_lock):
        super().__init__()
        self.port = port
        self.baudrate = baudrate
        self.serial_port = None
        self.is_running = False
        self.data_buffer = data_buffer  # 新增
        self.data_lock = data_lock        # 新增
        # 丢包率统计
        self.total_packets = 0
        self.invalid_packets = 0

    def run(self):
        try:
            self.serial_port = serial.Serial(self.port, self.baudrate, timeout=0.5)
            self.is_running = True
            buffer = bytearray()

            while self.is_running:
                if self.serial_port.in_waiting:
                    buffer.extend(self.serial_port.read(self.serial_port.in_waiting))
                    
                    # 匹配新版 21 字节帧长度
                    while len(buffer) >= 21:
                        if buffer[0] == 0xAA and buffer[1] == 0xBB:
                            packet = buffer[:21]
                            self.total_packets += 1  # 检测到帧头，计数+1

                            # 帧尾在索引 20
                            if packet[20] == 0xCC:
                                calc_xor = 0
                                # 校验位在索引 19，参与校验的是索引 2 到 18 (共17字节)
                                for b in packet[2:19]:
                                    calc_xor ^= b

                                if calc_xor == packet[19]:
                                    self.parse_packet(packet)
                                else:
                                    self.invalid_packets += 1
                                    print("XOR校验失败")
                            else:
                                self.invalid_packets += 1
                                print("帧尾校验失败")

                            del buffer[:21]
                        else:
                            del buffer[0:1]
                else:
                    self.msleep(1)

        except Exception as e:
            self.error_occurred.emit(f"串口错误: {str(e)}")
            self.is_running = False

    def parse_packet(self, data):
        range_ppg_num = -2048.0 / 262144.0
        range_acc_num = 4.0 / 32767.0

        # --- 1. ADC 桥压转换 (保持不变) ---
        num_Ut2 = (data[2] << 16) + (data[3] << 8)
        Ut2 = ((num_Ut2 / 8388608.0) * 2.5) * 1000.0

        num_Ut1 = (data[4] << 16) + (data[5] << 8)
        Ut1 = ((num_Ut1 / 8388608.0) * 2.5) * 1000.0

        num_Uc2 = (data[6] << 16) + (data[7] << 8)
        Uc2 = ((num_Uc2 / 8388608.0) * 2.5) * 1000.0

        num_Uc1 = (data[8] << 16) + (data[9] << 8)
        Uc1 = ((num_Uc1 / 8388608.0) * 2.5) * 1000.0

        # --- 2. MIMU 加速度计算 (保持不变) ---
        num_Accx = data[10] << 8
        Accx = -(num_Accx - 65536) * range_acc_num if num_Accx >= 32768 else -num_Accx * range_acc_num

        num_Accy = data[11] << 8
        Accy = -(num_Accy - 65536) * range_acc_num if num_Accy >= 32768 else -num_Accy * range_acc_num

        num_Accz = data[12] << 8
        Accz = (num_Accz - 65536) * range_acc_num if num_Accz >= 32768 else num_Accz * range_acc_num

        # --- 3. 模式识别与多波长 PPG/温度 解算 ---
        # 根据第18字节标识位判断工作模式
        is_hr_mode = (data[18] == 0xFF)
        mode_str = "心率模式 (单绿光)" if is_hr_mode else "血氧模式 (红光+红外)"

        ppg_green = 0.0
        ppg_red = 0.0
        ppg_ir = 0.0
        temp_val = 0.0

        if is_hr_mode:
            # 心率模式解算：3字节绿光 + 1字节Count
            raw_sum = (data[13] << 16) + (data[14] << 8) + data[15]
            count = data[16] if data[16] != 0 else 1
            raw_green = raw_sum / count
            ppg_green = raw_green * range_ppg_num + 1000.0
        else:
            # 血氧模式解算：2字节Red + 2字节IR
            raw_red = (data[13] << 8) | data[14]
            raw_ir = (data[15] << 8) | data[16]
            
            # 使用与绿光一致的缩放比例进行物理转换
            ppg_red = raw_red * range_ppg_num + 1000.0
            ppg_ir = raw_ir * range_ppg_num + 1000.0

            # 温度数据解算 (有符号整数部分 + 无符号小数部分)
            die_temp_int = data[17]
            if die_temp_int > 127:
                die_temp_int -= 256  # 补码转有符号
            die_temp_frac = data[18]
            
            # 代入温度补偿公式 (+2.4为代码给出的LED温升预估补偿)
            temp_val = die_temp_int + (die_temp_frac * 0.0625) + 2.4

        # 将MATLAB需要的数据写入DataBuffer
        # 格式: (PPG, HF1, HF2, HF3, ACCx, ACCy, ACCz)
        # HF3置零，使用Ut1和Ut2作为HF1和HF2
        matlab_data = (ppg_green, Ut1, Ut2, 0.0, Accx, Accy, Accz)
        with self.data_lock:
            self.data_buffer.append(matlab_data)

        # 计算丢包率
        if self.total_packets > 0:
            loss_rate = (self.invalid_packets / self.total_packets) * 100
        else:
            loss_rate = 0.0

        self.data_received.emit(Uc1, Uc2, Ut1, Ut2, Accx, Accy, Accz, ppg_green, ppg_red, ppg_ir, temp_val, mode_str, loss_rate)

    def stop(self):
        self.is_running = False
        if self.serial_port and self.serial_port.is_open:
            self.serial_port.close()
        self.wait()


# ---------------------------------------------------------
# 上位机主窗口 (前端 UI)
# ---------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("多传感器采集系统 - 数据实时监测与存储")
        self.resize(1400, 950)

        self.serial_thread = None
        self.is_recording = False
        self.csv_file = None
        self.csv_writer = None
        self.recording_start_time = None  # 记录开始时间

        # 采样率统计变量
        self.packet_count = 0

        # 当前工作模式：'hr' = 心率模式, 'spo2' = 血氧模式
        self.current_mode = 'hr'  # 默认心率模式
        self.last_mode = 'hr'  # 上一次的模式

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
            self.matlab_worker.fatal_error.connect(self.handle_matlab_error)  # 致命错误也连接到同一个处理函数
            self.matlab_worker.status_changed.connect(self.handle_matlab_status)
            self.matlab_worker.calibration_status.connect(self.handle_calibration_status)  # 连接校准状态信号
            self.matlab_worker.start()  # 启动QThread，这会执行run()方法
            self.matlab_available = True
        except ImportError:
            QMessageBox.warning(self, "MATLAB Engine未安装",
                "未检测到MATLAB Engine API。\n\n"
                "心率功能将不可用。")
        except Exception as e:
            QMessageBox.warning(self, "MATLAB启动失败",
                f"无法启动MATLAB: {e}\n\n心率功能将不可用。")

        self.plot_pts = 1000
        self.data_Uc1 = deque(maxlen=self.plot_pts)
        self.data_Uc2 = deque(maxlen=self.plot_pts)
        self.data_Ut1 = deque(maxlen=self.plot_pts)
        self.data_Ut2 = deque(maxlen=self.plot_pts)
        self.data_Accx = deque(maxlen=self.plot_pts)
        self.data_Accy = deque(maxlen=self.plot_pts)
        self.data_Accz = deque(maxlen=self.plot_pts)

        # 扩展 PPG 和 温度 存储
        self.data_ppg_g = deque(maxlen=self.plot_pts)
        self.data_ppg_r = deque(maxlen=self.plot_pts)
        self.data_ppg_ir = deque(maxlen=self.plot_pts)
        self.data_temp = deque(maxlen=self.plot_pts)

        # 心率数据存储
        self.data_hr_hf = deque(maxlen=60)  # 60秒历史
        self.data_hr_acc = deque(maxlen=60)
        self.data_time = deque(maxlen=60)
        self.hr_start_time = time.time()

        self.init_ui()

        # 图表刷新定时器
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_plots)
        self.timer.start(50)

        # 采样率计算定时器 (1秒刷新1次)
        self.rate_timer = QTimer()
        self.rate_timer.timeout.connect(self.update_sample_rate)
        self.rate_timer.start(1000)

    def init_ui(self):
        main_widget = QWidget()
        main_layout = QHBoxLayout()
        main_widget.setLayout(main_layout)
        self.setCentralWidget(main_widget)

        # --- 左侧控制面板 ---
        control_layout = QVBoxLayout()
        control_layout.setContentsMargins(10, 10, 10, 10)
        
        # 1. 状态监控看板 (新增)
        status_group = QGroupBox("实时状态监控")
        status_vbox = QVBoxLayout()

        self.lbl_mode = QLabel("当前模式: 等待数据...")
        self.lbl_mode.setStyleSheet("font-weight: bold; color: #2196F3; font-size: 14px;")
        self.lbl_rate = QLabel("采样率: 0 Hz")
        self.lbl_rate.setStyleSheet("font-weight: bold; color: #FF9800; font-size: 14px;")
        self.lbl_loss = QLabel("丢包率: 0.00%")
        self.lbl_loss.setStyleSheet("font-weight: bold; color: #F44336; font-size: 14px;")
        
        status_vbox.addWidget(self.lbl_mode)
        status_vbox.addWidget(self.lbl_rate)
        status_vbox.addWidget(self.lbl_loss)
        status_group.setLayout(status_vbox)

        # 2. 通信设置
        serial_group = QGroupBox("通信设置")
        serial_vbox = QVBoxLayout()
        
        self.cb_ports = QComboBox()
        self.refresh_ports()
        btn_refresh = QPushButton("刷新串口")
        btn_refresh.clicked.connect(self.refresh_ports)
        
        self.cb_baudrate = QComboBox()
        self.cb_baudrate.addItems(["9600", "19200", "115200", "460800"])
        self.cb_baudrate.setCurrentText("115200")

        self.btn_connect = QPushButton("打开串口")
        self.btn_connect.clicked.connect(self.toggle_serial)

        serial_vbox.addWidget(QLabel("选择串口:"))
        serial_vbox.addWidget(self.cb_ports)
        serial_vbox.addWidget(btn_refresh)
        serial_vbox.addWidget(QLabel("波特率:"))
        serial_vbox.addWidget(self.cb_baudrate)
        serial_vbox.addWidget(self.btn_connect)
        serial_group.setLayout(serial_vbox)

        # 3. 数据记录
        record_group = QGroupBox("数据记录")
        record_vbox = QVBoxLayout()
        
        self.le_path = QLineEdit(os.path.join(os.path.expanduser("~"), "Desktop"))
        btn_path = QPushButton("浏览保存路径")
        btn_path.clicked.connect(self.select_directory)
        
        self.le_filename = QLineEdit("SensorData")
        
        self.btn_record = QPushButton("开始记录")
        self.btn_record.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        self.btn_record.clicked.connect(self.toggle_record)

        record_vbox.addWidget(QLabel("保存目录:"))
        record_vbox.addWidget(self.le_path)
        record_vbox.addWidget(btn_path)
        record_vbox.addWidget(QLabel("文件名前缀:"))
        record_vbox.addWidget(self.le_filename)
        record_vbox.addWidget(self.btn_record)
        record_group.setLayout(record_vbox)

        # 4. 静息校准状态 (新增)
        calib_group = QGroupBox("静息校准状态")
        calib_vbox = QVBoxLayout()

        # 校准提示标签
        self.lbl_calib_status = QLabel("状态: 等待启动")
        self.lbl_calib_status.setStyleSheet("font-weight: bold; color: #FF9800; font-size: 14px;")
        self.lbl_calib_progress = QLabel("进度: 0%")
        self.lbl_calib_progress.setStyleSheet("color: #2196F3; font-size: 14px;")
        self.lbl_calib_hint = QLabel("提示: 请保持静坐状态30秒进行校准")
        self.lbl_calib_hint.setStyleSheet("color: #757575; font-size: 12px;")

        calib_vbox.addWidget(self.lbl_calib_status)
        calib_vbox.addWidget(self.lbl_calib_progress)
        calib_vbox.addWidget(self.lbl_calib_hint)
        calib_group.setLayout(calib_vbox)

        # 5. 心率监测
        hr_group = QGroupBox("心率监测")
        hr_vbox = QVBoxLayout()

        # 心率计算开关
        self.btn_hr_toggle = QPushButton("启动心率计算")
        self.btn_hr_toggle.setStyleSheet("background-color: #FF9800; color: white; font-weight: bold;")
        self.btn_hr_toggle.clicked.connect(self.toggle_hr_calculation)
        self.btn_hr_toggle.setEnabled(self.matlab_available)
        hr_vbox.addWidget(self.btn_hr_toggle)

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
        hr_group.setLayout(hr_vbox)

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

        # 6. 界面控制
        ui_group = QGroupBox("界面控制")
        ui_vbox = QVBoxLayout()

        # 模式切换按钮
        self.btn_switch_mode = QPushButton("切换到血氧模式")
        self.btn_switch_mode.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold;")
        self.btn_switch_mode.clicked.connect(self.manual_switch_mode)
        ui_vbox.addWidget(self.btn_switch_mode)

        self.btn_clear_plots = QPushButton("清空曲线显示")
        self.btn_clear_plots.clicked.connect(self.clear_all_plots)
        ui_vbox.addWidget(self.btn_clear_plots)
        ui_group.setLayout(ui_vbox)

        control_layout.addWidget(status_group)
        control_layout.addWidget(serial_group)
        control_layout.addWidget(record_group)
        control_layout.addWidget(calib_group)
        control_layout.addWidget(hr_group)
        control_layout.addWidget(algo_group)
        control_layout.addWidget(ui_group)
        control_layout.addStretch()

        # --- 右侧波形显示面板 ---
        plot_layout = QVBoxLayout()

        pg.setConfigOption('background', 'w')
        pg.setConfigOption('foreground', 'k')

        # ==================== 心率模式布局 ====================
        self.hr_mode_widget = QWidget()
        hr_mode_layout = QHBoxLayout()  # 改为水平布局
        hr_mode_layout.setContentsMargins(0, 0, 0, 0)
        self.hr_mode_widget.setLayout(hr_mode_layout)

        # PPG绿光曲线（左侧）
        self.plot_w_ppg_g = pg.PlotWidget(title="PPG 绿光 (Green)")
        self.plot_w_ppg_g.showGrid(x=True, y=True)
        self.curve_ppg_g = self.plot_w_ppg_g.plot(pen=pg.mkPen('g', width=2))
        hr_mode_layout.addWidget(self.plot_w_ppg_g, 1)  # stretch=1，占一半空间

        # 心率曲线（右侧）
        self.plot_w_hr_mode = pg.PlotWidget(title="心率趋势 (最近60秒)")
        self.plot_w_hr_mode.showGrid(x=True, y=True)
        self.plot_w_hr_mode.setYRange(40, 200)
        self.plot_w_hr_mode.setLabel('left', '心率', units='BPM')
        self.plot_w_hr_mode.setLabel('bottom', '时间', units='s')
        self.curve_hr_hf_mode = self.plot_w_hr_mode.plot(pen=pg.mkPen('g', width=2), name="HF")
        self.curve_hr_acc_mode = self.plot_w_hr_mode.plot(pen=pg.mkPen((150,150,150), width=1, style=Qt.DashLine), name="ACC")
        self.plot_w_hr_mode.addLegend()
        hr_mode_layout.addWidget(self.plot_w_hr_mode, 1)  # stretch=1，占一半空间

        # ==================== 血氧模式布局 ====================
        self.spo2_mode_widget = QWidget()
        spo2_mode_layout = QHBoxLayout()  # 改为水平布局：左边PPG，右边温度+血氧
        spo2_mode_layout.setContentsMargins(0, 0, 0, 0)
        self.spo2_mode_widget.setLayout(spo2_mode_layout)

        # 左侧：红光和红外光上下排列
        ppg_ri_widget = QWidget()
        ppg_ri_layout = QVBoxLayout()
        ppg_ri_layout.setContentsMargins(0, 0, 0, 0)
        ppg_ri_widget.setLayout(ppg_ri_layout)

        self.plot_w_ppg_r = pg.PlotWidget(title="PPG 红光 (Red)")
        self.plot_w_ppg_r.showGrid(x=True, y=True)
        self.curve_ppg_r = self.plot_w_ppg_r.plot(pen=pg.mkPen('r', width=2))
        ppg_ri_layout.addWidget(self.plot_w_ppg_r)

        self.plot_w_ppg_ir = pg.PlotWidget(title="PPG 红外光 (IR)")
        self.plot_w_ppg_ir.showGrid(x=True, y=True)
        self.curve_ppg_ir = self.plot_w_ppg_ir.plot(pen=pg.mkPen('b', width=2))
        ppg_ri_layout.addWidget(self.plot_w_ppg_ir)

        spo2_mode_layout.addWidget(ppg_ri_widget, 1)  # 左侧占一半

        # 右侧：温度和血氧上下排列
        temp_spo2_widget = QWidget()
        temp_spo2_layout = QVBoxLayout()
        temp_spo2_layout.setContentsMargins(0, 0, 0, 0)
        temp_spo2_widget.setLayout(temp_spo2_layout)

        # 温度曲线（上面1/3）
        self.plot_w_temp = pg.PlotWidget(title="芯片结温实时监控 (℃)")
        self.plot_w_temp.showGrid(x=True, y=True)
        self.curve_temp = self.plot_w_temp.plot(pen=pg.mkPen(color=(200, 100, 0), width=2))
        temp_spo2_layout.addWidget(self.plot_w_temp, 1)

        # 血氧饱和度曲线（下面2/3）
        self.plot_w_spo2 = pg.PlotWidget(title="血氧饱和度 SpO2 (%) - 预留")
        self.plot_w_spo2.showGrid(x=True, y=True)
        self.plot_w_spo2.setYRange(70, 100)
        self.plot_w_spo2.setLabel('left', 'SpO2', units='%')
        self.curve_spo2 = self.plot_w_spo2.plot(pen=pg.mkPen('m', width=2))
        temp_spo2_layout.addWidget(self.plot_w_spo2, 2)

        spo2_mode_layout.addWidget(temp_spo2_widget, 1)  # 右侧占一半

        # ==================== 通用曲线（始终显示） ====================
        # 使用堆叠布局实现模式切换
        from PyQt5.QtWidgets import QStackedWidget
        self.mode_stack = QStackedWidget()
        self.mode_stack.addWidget(self.hr_mode_widget)
        self.mode_stack.addWidget(self.spo2_mode_widget)
        # 默认显示心率模式
        self.mode_stack.setCurrentIndex(0)
        plot_layout.addWidget(self.mode_stack, 3)  # PPG板块（最上面）

        # Ut 桥顶电压绘图 (水平并排)
        ut_widget = QWidget()
        ut_layout = QHBoxLayout()
        ut_layout.setContentsMargins(0, 0, 0, 0)
        ut_widget.setLayout(ut_layout)

        self.plot_w_ut1 = pg.PlotWidget(title="热膜桥顶1 (Ut1) - mV")
        self.plot_w_ut1.showGrid(x=True, y=True)
        self.curve_Ut1 = self.plot_w_ut1.plot(pen=pg.mkPen(color=(255, 165, 0), width=1.5))
        ut_layout.addWidget(self.plot_w_ut1)

        self.plot_w_ut2 = pg.PlotWidget(title="热膜桥顶2 (Ut2) - mV")
        self.plot_w_ut2.showGrid(x=True, y=True)
        self.curve_Ut2 = self.plot_w_ut2.plot(pen=pg.mkPen('m', width=1.5))
        ut_layout.addWidget(self.plot_w_ut2)

        plot_layout.addWidget(ut_widget, 2)  # Ut桥顶板块

        # Uc 桥中电压绘图 (水平并排)
        uc_widget = QWidget()
        uc_layout = QHBoxLayout()
        uc_layout.setContentsMargins(0, 0, 0, 0)
        uc_widget.setLayout(uc_layout)

        self.plot_w_uc1 = pg.PlotWidget(title="热膜桥中1 (Uc1) - mV")
        self.plot_w_uc1.showGrid(x=True, y=True)
        self.curve_Uc1 = self.plot_w_uc1.plot(pen=pg.mkPen('r', width=1.5))
        uc_layout.addWidget(self.plot_w_uc1)

        self.plot_w_uc2 = pg.PlotWidget(title="热膜桥中2 (Uc2) - mV")
        self.plot_w_uc2.showGrid(x=True, y=True)
        self.curve_Uc2 = self.plot_w_uc2.plot(pen=pg.mkPen('b', width=1.5))
        uc_layout.addWidget(self.plot_w_uc2)

        plot_layout.addWidget(uc_widget, 2)  # Uc桥中板块

        # MIMU 三轴加速绘图（最底部）
        self.plot_w_acc = pg.PlotWidget(title="三轴加速度计 (Acc_x, Acc_y, Acc_z)")
        self.plot_w_acc.showGrid(x=True, y=True)
        self.plot_w_acc.addLegend()
        self.curve_accx = self.plot_w_acc.plot(pen=pg.mkPen('r', width=1.5), name="Acc X")
        self.curve_accy = self.plot_w_acc.plot(pen=pg.mkPen('g', width=1.5), name="Acc Y")
        self.curve_accz = self.plot_w_acc.plot(pen=pg.mkPen('b', width=1.5), name="Acc Z")
        plot_layout.addWidget(self.plot_w_acc, 1)  # ACC板块（最底部）

        main_layout.addLayout(control_layout, 1)
        main_layout.addLayout(plot_layout, 5)

    def refresh_ports(self):
        self.cb_ports.clear()
        ports = serial.tools.list_ports.comports()
        for port in ports:
            self.cb_ports.addItem(port.device)

    def select_directory(self):
        dir_path = QFileDialog.getExistingDirectory(self, "选择保存路径")
        if dir_path:
            self.le_path.setText(dir_path)

    def toggle_serial(self):
        if self.serial_thread and self.serial_thread.is_running:
            self.serial_thread.stop()
            self.btn_connect.setText("打开串口")
            self.btn_connect.setStyleSheet("")
            self.cb_ports.setEnabled(True)
            self.cb_baudrate.setEnabled(True)
            self.lbl_mode.setText("当前模式: 离线")
            self.lbl_rate.setText("采样率: 0 Hz")
            self.lbl_loss.setText("丢包率: 0.00%")

            # 停止MATLAB计算
            if self.matlab_available and self.matlab_worker:
                self.matlab_worker.stop_calculation()

            if self.is_recording:
                self.toggle_record()
        else:
            port = self.cb_ports.currentText()
            baud = int(self.cb_baudrate.currentText())
            if not port:
                QMessageBox.warning(self, "警告", "请先选择串口！")
                return
            
            self.serial_thread = SerialReaderThread(port, baud, self.data_buffer, self.data_lock)
            self.serial_thread.data_received.connect(self.handle_new_data)
            self.serial_thread.error_occurred.connect(self.handle_serial_error)
            self.serial_thread.start()

            self.btn_connect.setText("关闭串口")
            self.btn_connect.setStyleSheet("background-color: #f44336; color: white;")
            self.cb_ports.setEnabled(False)
            self.cb_baudrate.setEnabled(False)

            # 不再自动启动MATLAB计算，需要用户手动点击启动心率计算按钮

    def toggle_hr_calculation(self):
        """切换心率计算状态"""
        if not self.matlab_available or self.matlab_worker is None:
            QMessageBox.warning(self, "提示", "MATLAB不可用，无法启动心率计算")
            return

        if self.matlab_worker.is_calculating:
            # 停止计算
            self.matlab_worker.stop_calculation()
            self.btn_hr_toggle.setText("启动心率计算")
            self.btn_hr_toggle.setStyleSheet("background-color: #FF9800; color: white; font-weight: bold;")
        else:
            # 启动计算
            self.matlab_worker.start_calculation()
            self.btn_hr_toggle.setText("停止心率计算")
            self.btn_hr_toggle.setStyleSheet("background-color: #f44336; color: white; font-weight: bold;")

    def clear_all_plots(self):
        """清空所有曲线显示"""
        # 清空所有数据缓冲区
        self.data_Uc1.clear()
        self.data_Uc2.clear()
        self.data_Ut1.clear()
        self.data_Ut2.clear()
        self.data_Accx.clear()
        self.data_Accy.clear()
        self.data_Accz.clear()
        self.data_ppg_g.clear()
        self.data_ppg_r.clear()
        self.data_ppg_ir.clear()
        self.data_temp.clear()
        self.data_hr_hf.clear()
        self.data_hr_acc.clear()
        self.data_time.clear()

        # 重置心率起始时间
        self.hr_start_time = time.time()

        # 清空图表显示
        self.curve_ppg_g.setData([])
        self.curve_ppg_r.setData([])
        self.curve_ppg_ir.setData([])
        self.curve_temp.setData([])
        self.curve_Uc1.setData([])
        self.curve_Uc2.setData([])
        self.curve_Ut1.setData([])
        self.curve_Ut2.setData([])
        self.curve_accx.setData([])
        self.curve_accy.setData([])
        self.curve_accz.setData([])
        self.curve_hr_hf.setData([])
        self.curve_hr_acc.setData([])
        # 清空心率模式特有的曲线
        self.curve_hr_hf_mode.setData([])
        self.curve_hr_acc_mode.setData([])
        # 清空血氧模式特有的曲线
        self.curve_spo2.setData([])

        # 重置心率显示
        self.lbl_hr_hf.setText("HF: -- BPM")
        self.lbl_hr_acc.setText("ACC: -- BPM")
        self.lbl_motion.setText("状态: --")

    def switch_display_mode(self, mode_str):
        """根据串口接收的数据模式切换显示布局

        Args:
            mode_str: 模式字符串，包含"心率模式"或"血氧模式"
        """
        if "心率模式" in mode_str:
            target_mode = 'hr'
        elif "血氧模式" in mode_str:
            target_mode = 'spo2'
        else:
            return  # 未识别的模式，不切换

        # 仅在模式真正改变时才切换
        if target_mode != self.current_mode:
            self.current_mode = target_mode
            if target_mode == 'hr':
                self.mode_stack.setCurrentIndex(0)  # 显示心率模式布局
                self.btn_switch_mode.setText("切换到血氧模式")
            else:
                self.mode_stack.setCurrentIndex(1)  # 显示血氧模式布局
                self.btn_switch_mode.setText("切换到心率模式")

            # 更新状态标签显示
            self.lbl_mode.setText(f"当前模式: {mode_str}")

    def get_current_display_mode(self):
        """获取当前显示模式"""
        return self.current_mode

    def manual_switch_mode(self):
        """手动切换显示模式"""
        if self.current_mode == 'hr':
            # 切换到血氧模式
            self.current_mode = 'spo2'
            self.mode_stack.setCurrentIndex(1)
            self.btn_switch_mode.setText("切换到心率模式")
            # 更新状态标签
            self.lbl_mode.setText("当前模式: 血氧模式 (红光+红外) [手动切换]")
        else:
            # 切换到心率模式
            self.current_mode = 'hr'
            self.mode_stack.setCurrentIndex(0)
            self.btn_switch_mode.setText("切换到血氧模式")
            # 更新状态标签
            self.lbl_mode.setText("当前模式: 心率模式 (单绿光) [手动切换]")

    def handle_serial_error(self, err_msg):
        QMessageBox.critical(self, "串口断开", err_msg)
        self.toggle_serial()

    def toggle_record(self):
        if self.is_recording:
            self.is_recording = False
            if self.csv_file:
                self.csv_file.close()
                self.csv_file = None
            if self.hr_csv_file:
                self.hr_csv_file.close()
                self.hr_csv_file = None
            self.btn_record.setText("开始记录")
            self.btn_record.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        else:
            if not (self.serial_thread and self.serial_thread.is_running):
                QMessageBox.warning(self, "提示", "请先打开串口连接！")
                return

            folder = self.le_path.text()
            prefix = self.le_filename.text()
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            filename = f"{prefix}_{timestamp}.csv"
            filepath = os.path.join(folder, filename)

            try:
                self.csv_file = open(filepath, 'w', newline='')
                self.csv_writer = csv.writer(self.csv_file)
                # 修改CSV表头，时间改为相对时间(s)
                self.csv_writer.writerow(["Time(s)", "Mode", "Uc1(mV)", "Uc2(mV)", "Ut1(mV)", "Ut2(mV)",
                                          "AccX", "AccY", "AccZ", "PPG_Green", "PPG_Red", "PPG_IR", "Temp(C)"])
                # 记录开始时间
                self.recording_start_time = time.time()
                self.is_recording = True
                self.btn_record.setText("停止记录")
                self.btn_record.setStyleSheet("background-color: #f44336; color: white; font-weight: bold;")

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

            except Exception as e:
                QMessageBox.critical(self, "文件错误", f"无法创建文件: {e}")
            except Exception as e:
                QMessageBox.critical(self, "文件错误", f"无法创建文件: {e}")

    def handle_new_data(self, Uc1, Uc2, Ut1, Ut2, Accx, Accy, Accz, ppg_g, ppg_r, ppg_ir, temp, mode_str, loss_rate):
        # 更新采样统计计数与 UI 标签
        self.packet_count += 1
        # 调用模式切换函数，自动检测并切换显示布局
        self.switch_display_mode(mode_str)
        self.lbl_loss.setText(f"丢包率: {loss_rate:.2f}%")

        self.data_Uc1.append(Uc1)
        self.data_Uc2.append(Uc2)
        self.data_Ut1.append(Ut1)
        self.data_Ut2.append(Ut2)
        self.data_Accx.append(Accx)
        self.data_Accy.append(Accy)
        self.data_Accz.append(Accz)
        
        # 追加扩展数据
        self.data_ppg_g.append(ppg_g)
        self.data_ppg_r.append(ppg_r)
        self.data_ppg_ir.append(ppg_ir)
        self.data_temp.append(temp)

        if self.is_recording and self.csv_writer:
            # 计算相对时间（秒，精确到毫秒）
            elapsed_time = round(time.time() - self.recording_start_time, 3)
            # 记录数据时新增当前模式标识和所有分离的光学与温度数据
            self.csv_writer.writerow([elapsed_time, mode_str, 
                                      round(Uc1, 5), round(Uc2, 5), round(Ut1, 5), round(Ut2, 5), 
                                      round(Accx, 5), round(Accy, 5), round(Accz, 5), 
                                      round(ppg_g, 5), round(ppg_r, 5), round(ppg_ir, 5), round(temp, 2)])
            self.csv_file.flush() 

    def update_sample_rate(self):
        # 计算1秒内收到的包数并重置
        if self.serial_thread and self.serial_thread.is_running:
            self.lbl_rate.setText(f"采样率: {self.packet_count} Hz")
            self.packet_count = 0

    def update_plots(self):
        if len(self.data_Uc1) > 0:
            # 根据当前模式更新对应的曲线
            if self.current_mode == 'hr':
                # 心率模式：更新PPG绿光曲线和心率曲线
                self.curve_ppg_g.setData(list(self.data_ppg_g))
                if len(self.data_time) > 0:
                    self.curve_hr_hf_mode.setData(list(self.data_time), list(self.data_hr_hf))
                    self.curve_hr_acc_mode.setData(list(self.data_time), list(self.data_hr_acc))
            else:
                # 血氧模式：更新红光、红外光、温度曲线
                self.curve_ppg_r.setData(list(self.data_ppg_r))
                self.curve_ppg_ir.setData(list(self.data_ppg_ir))
                self.curve_temp.setData(list(self.data_temp))

            # 通用曲线（所有模式都更新）
            self.curve_Uc1.setData(list(self.data_Uc1))
            self.curve_Uc2.setData(list(self.data_Uc2))
            self.curve_Ut1.setData(list(self.data_Ut1))
            self.curve_Ut2.setData(list(self.data_Ut2))

            self.curve_accx.setData(list(self.data_Accx))
            self.curve_accy.setData(list(self.data_Accy))
            self.curve_accz.setData(list(self.data_Accz))

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

    def handle_matlab_error(self, error_msg):
        """处理MATLAB错误 - 当MATLAB计算出错时关闭串口接收"""
        QMessageBox.warning(self, "MATLAB错误", error_msg)

        # 立刻停止MATLAB计算
        if self.matlab_worker and self.matlab_worker.is_calculating:
            self.matlab_worker.stop_calculation()

        # 关闭串口接收
        if self.serial_thread and self.serial_thread.is_running:
            self.serial_thread.stop()
            self.btn_connect.setText("打开串口")
            self.btn_connect.setStyleSheet("")
            self.cb_ports.setEnabled(True)
            self.cb_baudrate.setEnabled(True)
            self.lbl_mode.setText("当前模式: 离线")
            self.lbl_rate.setText("采样率: 0 Hz")
            self.lbl_loss.setText("丢包率: 0.00%")

        # 如果正在记录，停止记录
        if self.is_recording:
            self.toggle_record()

    def handle_matlab_status(self, status_msg):
        """处理MATLAB状态更新"""
        print(f"MATLAB状态: {status_msg}")

    def handle_calibration_status(self, is_calibrated, progress, message):
        """处理静息校准状态更新"""
        # 当校准完成时，强制显示100%进度
        display_progress = 100.0 if is_calibrated else progress

        self.lbl_calib_status.setText(f"状态: {message}")
        self.lbl_calib_progress.setText(f"进度: {display_progress:.0f}%")

        if is_calibrated:
            self.lbl_calib_status.setStyleSheet("font-weight: bold; color: #4CAF50; font-size: 14px;")
            self.lbl_calib_hint.setText("校准已完成！可以开始运动测试")
            self.lbl_calib_hint.setStyleSheet("color: #4CAF50; font-size: 12px;")
        else:
            self.lbl_calib_status.setStyleSheet("font-weight: bold; color: #FF9800; font-size: 14px;")
            self.lbl_calib_hint.setText("提示: 请保持静坐状态进行校准")
            self.lbl_calib_hint.setStyleSheet("color: #757575; font-size: 12px;")

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

    def closeEvent(self, event):
        # 清理MATLAB资源
        if self.matlab_worker:
            self.matlab_worker.cleanup()

        if self.serial_thread and self.serial_thread.is_running:
            self.serial_thread.stop()
        if self.csv_file:
            self.csv_file.close()
        event.accept()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())