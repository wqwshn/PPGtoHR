import sys
import os
import time
import csv
import serial
import serial.tools.list_ports
from collections import deque
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QGridLayout, QLabel, QComboBox, QPushButton, 
                             QLineEdit, QFileDialog, QGroupBox, QMessageBox)
from PyQt5.QtCore import QThread, pyqtSignal, QTimer
import pyqtgraph as pg

# ---------------------------------------------------------
# 数据接收与解析线程 (后台)
# ---------------------------------------------------------
class SerialReaderThread(QThread):
    # 定义信号：将解析后的物理数据发送给主UI线程
    data_received = pyqtSignal(float, float, float, float, float, float, float, float)
    error_occurred = pyqtSignal(str)

    def __init__(self, port, baudrate):
        super().__init__()
        self.port = port
        self.baudrate = baudrate
        self.serial_port = None
        self.is_running = False

    def run(self):
        try:
            self.serial_port = serial.Serial(self.port, self.baudrate, timeout=0.5)
            self.is_running = True
            buffer = bytearray()

            while self.is_running:
                if self.serial_port.in_waiting:
                    buffer.extend(self.serial_port.read(self.serial_port.in_waiting))
                    
                    while len(buffer) >= 19:
                        if buffer[0] == 0xAA and buffer[1] == 0xBB:
                            packet = buffer[:19]
                            
                            if packet[18] == 0xCC:
                                calc_xor = 0
                                for b in packet[2:17]:
                                    calc_xor ^= b
                                
                                if calc_xor == packet[17]:
                                    self.parse_packet(packet)
                                else:
                                    print("XOR校验失败")
                            else:
                                print("帧尾校验失败")
                            
                            del buffer[:19]
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

        # --- 1. ADC 桥压转换 (转换为 mV) ---
        num_Ut2 = (data[2] << 16) + (data[3] << 8)
        Ut2 = ((num_Ut2 / 8388608.0) * 2.5) * 1000.0  # 乘 1000 转换为 mV

        num_Ut1 = (data[4] << 16) + (data[5] << 8)
        Ut1 = ((num_Ut1 / 8388608.0) * 2.5) * 1000.0

        num_Uc2 = (data[6] << 16) + (data[7] << 8)
        Uc2 = ((num_Uc2 / 8388608.0) * 2.5) * 1000.0

        num_Uc1 = (data[8] << 16) + (data[9] << 8)
        Uc1 = ((num_Uc1 / 8388608.0) * 2.5) * 1000.0

        # --- 2. MIMU 加速度计算 ---
        num_Accx = data[10] << 8
        Accx = -(num_Accx - 65536) * range_acc_num if num_Accx >= 32768 else -num_Accx * range_acc_num

        num_Accy = data[11] << 8
        Accy = -(num_Accy - 65536) * range_acc_num if num_Accy >= 32768 else -num_Accy * range_acc_num

        num_Accz = data[12] << 8
        Accz = (num_Accz - 65536) * range_acc_num if num_Accz >= 32768 else num_Accz * range_acc_num

        # --- 3. PPG 处理部分 ---
        raw_sum = (data[13] << 16) + (data[14] << 8) + data[15]
        count = data[16]
        
        if count == 0: count = 1
        raw_green = raw_sum / count
        ppg1_1 = raw_green * range_ppg_num + 1000.0

        self.data_received.emit(Uc1, Uc2, Ut1, Ut2, Accx, Accy, Accz, ppg1_1)

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
        self.resize(1300, 900)

        self.serial_thread = None
        self.is_recording = False
        self.csv_file = None
        self.csv_writer = None

        self.plot_pts = 1000
        self.data_Uc1 = deque(maxlen=self.plot_pts)
        self.data_Uc2 = deque(maxlen=self.plot_pts)
        self.data_Ut1 = deque(maxlen=self.plot_pts)
        self.data_Ut2 = deque(maxlen=self.plot_pts)
        self.data_Accx = deque(maxlen=self.plot_pts)
        self.data_Accy = deque(maxlen=self.plot_pts)
        self.data_Accz = deque(maxlen=self.plot_pts)
        self.data_ppg = deque(maxlen=self.plot_pts)

        self.init_ui()

        self.timer = QTimer()
        self.timer.timeout.connect(self.update_plots)
        self.timer.start(50)

    def init_ui(self):
        main_widget = QWidget()
        main_layout = QHBoxLayout()
        main_widget.setLayout(main_layout)
        self.setCentralWidget(main_widget)

        # --- 左侧控制面板 ---
        control_layout = QVBoxLayout()
        control_layout.setContentsMargins(10, 10, 10, 10)
        
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

        control_layout.addWidget(serial_group)
        control_layout.addWidget(record_group)
        control_layout.addStretch()

        # --- 右侧波形显示面板 ---
        plot_layout = QVBoxLayout()
        
        pg.setConfigOption('background', 'w')
        pg.setConfigOption('foreground', 'k')

        # 1. PPG 绘图
        self.plot_w_ppg = pg.PlotWidget(title="PPG 绿光信号 (ppg1_1)")
        self.plot_w_ppg.showGrid(x=True, y=True)
        self.curve_ppg = self.plot_w_ppg.plot(pen=pg.mkPen('g', width=2))
        plot_layout.addWidget(self.plot_w_ppg, 2) # 分配空间权重

        # 2. ADC 热膜绘图 (2x2 网格)
        adc_widget = QWidget()
        adc_layout = QGridLayout()
        adc_widget.setLayout(adc_layout)

        self.plot_w_uc1 = pg.PlotWidget(title="热膜桥中1 (Uc1) - mV")
        self.plot_w_uc1.showGrid(x=True, y=True)
        self.curve_Uc1 = self.plot_w_uc1.plot(pen=pg.mkPen('r', width=1.5))

        self.plot_w_uc2 = pg.PlotWidget(title="热膜桥中2 (Uc2) - mV")
        self.plot_w_uc2.showGrid(x=True, y=True)
        self.curve_Uc2 = self.plot_w_uc2.plot(pen=pg.mkPen('b', width=1.5))

        self.plot_w_ut1 = pg.PlotWidget(title="热膜桥顶1 (Ut1) - mV")
        self.plot_w_ut1.showGrid(x=True, y=True)
        self.curve_Ut1 = self.plot_w_ut1.plot(pen=pg.mkPen(color=(255, 165, 0), width=1.5))

        self.plot_w_ut2 = pg.PlotWidget(title="热膜桥顶2 (Ut2) - mV")
        self.plot_w_ut2.showGrid(x=True, y=True)
        self.curve_Ut2 = self.plot_w_ut2.plot(pen=pg.mkPen('m', width=1.5))

        adc_layout.addWidget(self.plot_w_uc1, 0, 0)
        adc_layout.addWidget(self.plot_w_uc2, 0, 1)
        adc_layout.addWidget(self.plot_w_ut1, 1, 0)
        adc_layout.addWidget(self.plot_w_ut2, 1, 1)
        
        plot_layout.addWidget(adc_widget, 4) # 网格图表占比更大

        # 3. MIMU 三轴加速绘图
        self.plot_w_acc = pg.PlotWidget(title="三轴加速度计 (Acc_x, Acc_y, Acc_z)")
        self.plot_w_acc.showGrid(x=True, y=True)
        self.plot_w_acc.addLegend()
        self.curve_accx = self.plot_w_acc.plot(pen=pg.mkPen('r', width=1.5), name="Acc X")
        self.curve_accy = self.plot_w_acc.plot(pen=pg.mkPen('g', width=1.5), name="Acc Y")
        self.curve_accz = self.plot_w_acc.plot(pen=pg.mkPen('b', width=1.5), name="Acc Z")
        plot_layout.addWidget(self.plot_w_acc, 2)

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
            
            if self.is_recording:
                self.toggle_record()
        else:
            port = self.cb_ports.currentText()
            baud = int(self.cb_baudrate.currentText())
            if not port:
                QMessageBox.warning(self, "警告", "请先选择串口！")
                return
            
            self.serial_thread = SerialReaderThread(port, baud)
            self.serial_thread.data_received.connect(self.handle_new_data)
            self.serial_thread.error_occurred.connect(self.handle_serial_error)
            self.serial_thread.start()

            self.btn_connect.setText("关闭串口")
            self.btn_connect.setStyleSheet("background-color: #f44336; color: white;")
            self.cb_ports.setEnabled(False)
            self.cb_baudrate.setEnabled(False)

    def handle_serial_error(self, err_msg):
        QMessageBox.critical(self, "串口断开", err_msg)
        self.toggle_serial()

    def toggle_record(self):
        if self.is_recording:
            self.is_recording = False
            if self.csv_file:
                self.csv_file.close()
                self.csv_file = None
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
                self.csv_writer.writerow(["Time", "Uc1(mV)", "Uc2(mV)", "Ut1(mV)", "Ut2(mV)", "AccX", "AccY", "AccZ", "PPG1_1"])
                self.is_recording = True
                self.btn_record.setText("停止记录")
                self.btn_record.setStyleSheet("background-color: #f44336; color: white; font-weight: bold;")
            except Exception as e:
                QMessageBox.critical(self, "文件错误", f"无法创建文件: {e}")

    def handle_new_data(self, Uc1, Uc2, Ut1, Ut2, Accx, Accy, Accz, ppg1_1):
        self.data_Uc1.append(Uc1)
        self.data_Uc2.append(Uc2)
        self.data_Ut1.append(Ut1)
        self.data_Ut2.append(Ut2)
        self.data_Accx.append(Accx)
        self.data_Accy.append(Accy)
        self.data_Accz.append(Accz)
        self.data_ppg.append(ppg1_1)

        if self.is_recording and self.csv_writer:
            curr_time = time.strftime("%H:%M:%S.%f")[:-3]
            self.csv_writer.writerow([curr_time, round(Uc1, 5), round(Uc2, 5), round(Ut1, 5), 
                                      round(Ut2, 5), round(Accx, 5), round(Accy, 5), 
                                      round(Accz, 5), round(ppg1_1, 5)])
            # 【关键修复】: 强制刷新缓冲区，确保实时写入磁盘
            self.csv_file.flush() 

    def update_plots(self):
        if len(self.data_ppg) > 0:
            self.curve_ppg.setData(list(self.data_ppg))
            
            self.curve_Uc1.setData(list(self.data_Uc1))
            self.curve_Uc2.setData(list(self.data_Uc2))
            self.curve_Ut1.setData(list(self.data_Ut1))
            self.curve_Ut2.setData(list(self.data_Ut2))
            
            self.curve_accx.setData(list(self.data_Accx))
            self.curve_accy.setData(list(self.data_Accy))
            self.curve_accz.setData(list(self.data_Accz))

    def closeEvent(self, event):
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