# SISTEMA DE ADQUISICION DE DATOS - LABORATORIO DE DIFRACCION
# Arquitectura: Interfaz PyQt5 y Cliente Human-Machine Interfaz con QThread y matplotlib

import sys
import serial
import serial.tools.list_ports
from collections import deque
from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QComboBox,
    QFileDialog,
    QMessageBox,
    QGroupBox,
    QFormLayout,
)
from PyQt5.QtCore import QThread, pyqtSignal, QTimer, Qt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

# -------------------------------------------------------------
# Hilo de lectura Serial, mediante QThread


class SerialWorker(QThread):  # La serie Worker thread dedicado al serial en 2do plano,
    # evita la congelacion de la IG, delega la lectura de puertos.
    new_data_signal = pyqtSignal(
        float, float, float
    )  # Emite los datos convertidos (pos, avg, sd)
    error_signal = pyqtSignal(str)  # Emite mensajes de fallo critico en el bus serial

    def __init__(self, port, baudrate, resolucion):
        super().__init__()
        self.port = port
        self.baudrate = baudrate
        self.resolucion = resolucion
        self.serial_port = None
        self.is_running = True  # Bandera de control para el ciclo de vida del hilo

    def run(
        self,
    ):  # Inicializacion del descriptor de puerto fisico con timeout de bloqueo preventivo
        try:
            self.serial_port = serial.Serial(self.port, self.baudrate, timeout=1)
        except Exception as e:
            self.error_signal.emit(f"No se pudo abrir el puerto {self.port}: {e}")
            return

        while self.is_running:  # Bucle principal de escucha de telemetria de hardware
            try:
                if self.serial_port.in_waiting:
                    line = (
                        self.serial_port.readline()
                        .decode("utf-8", errors="ignore")
                        .strip()
                    )
                    if line:
                        parts = line.split()
                        if len(parts) == 3:
                            # Extraccion de valores ADC brutos (numero de Cuentas) del microcontrolador
                            avg_adc = int(parts[0])
                            step = int(parts[1])
                            sd_adc = float(parts[2])

                            # Factores de conversion analogico-digital (ADC de 10 bits a Voltios)
                            adc_volt = 5.0 / 1023.0
                            avg = avg_adc * adc_volt
                            sd = sd_adc * adc_volt
                            pos = step * self.resolucion
                            # Envio seguro de datos procesados hacia el hilo principal de UI
                            self.new_data_signal.emit(pos, avg, sd)
            except Exception:
                pass  # Tolerancia a fallos transitorios de transmision electromagnetica

    def send_command(self, cmd):
        # Envia comandos de control hacia el actuador con validacion de estado de enlace
        if self.serial_port and self.serial_port.is_open:
            try:
                self.serial_port.write((cmd + "\n").encode())
            except Exception as e:
                self.error_signal.emit(str(e))

    def stop(self):
        # Protocolo seguro de terminacion de hilo y parada de emergencia por hardware
        self.is_running = False
        if self.serial_port and self.serial_port.is_open:
            try:
                self.serial_port.write(b"X\n")  # Paro de emergencia
                self.serial_port.close()  # Libera el puerto serial en el sistema operativo
            except:
                pass


# -------------------------------------------------------------
# Ventana principal del Software (HMI/GUI)


class DiffractionApp(QMainWindow):
    """
    Controlador principal de la Interfaz Humano-Maquina (HMI).
    Gestiona la disposicion visual, graficos en tiempo real y la persistencia de datos.
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sistema de Adquisicion - Patrones de Difraccion")
        self.resize(1800, 1100)

        # Constantes y estructuras de almacenamiento de alto rendimiento
        self.resolucion = 0.025
        self.xdata = deque(maxlen=30000)
        self.ydata = deque(maxlen=30000)
        self.saved_data = []  # Contenedor fijo para exportacion (tuplas completas)
        self.worker = None  # Referencia al hilo de adquisicion serial

        self.init_ui()
        self.auto_detect_port()

    def init_ui(
        self,
    ):  # Construccion jerarquica de componentes visuales (Ventana principal para el dividido en paneles)
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)

        # ----- PANEL IZQUIERDO: Controles -----
        control_panel = QVBoxLayout()

        # Grupo: Configuracion
        group_config = QGroupBox("Configuracion del Sistema")
        form_layout = QFormLayout()

        self.cb_ports = QComboBox()
        self.cb_ports.setStyleSheet("font-size: 14px; padding: 5px;")

        self.btn_refresh = QPushButton("Actualizar Puertos")
        self.btn_refresh.setStyleSheet("font-size: 14px; padding: 8px;")
        self.btn_refresh.clicked.connect(self.auto_detect_port)

        self.cb_laser = QComboBox()
        self.cb_laser.addItems(["ROJO", "VERDE", "AZUL"])
        self.cb_laser.setStyleSheet("font-size: 14px; padding: 5px; font-weight: bold;")

        # Logica para impedir el cambio de laser en plena medicion
        self.current_laser_index = 0
        self.cb_laser.currentIndexChanged.connect(self.on_laser_change)

        self.btn_connect = QPushButton("Conectar e Iniciar")
        self.btn_connect.setStyleSheet(
            "background-color: #28a745; color: white; font-weight: bold; font-size: 18px; padding: 12px;"
        )
        self.btn_connect.clicked.connect(self.toggle_connection)

        form_layout.addRow(QLabel("Puerto USB:"), self.cb_ports)
        form_layout.addRow("", self.btn_refresh)
        form_layout.addRow(QLabel("Color del Laser:"), self.cb_laser)
        form_layout.addRow("", self.btn_connect)
        group_config.setLayout(form_layout)
        control_panel.addWidget(group_config)

        # Grupo: Control de Motor y Adquisicion
        group_motor = QGroupBox("Control de Desplazamiento")
        motor_layout = QVBoxLayout()

        self.btn_left = QPushButton("MOVER IZQUIERDA")
        self.btn_left.setStyleSheet(
            "background-color: #007bff; color: white; padding: 20px; font-size: 16px; font-weight: bold;"
        )
        self.btn_left.clicked.connect(lambda: self.send_serial_cmd("1"))

        self.btn_right = QPushButton("MOVER DERECHA (TOMAR DATOS)")
        self.btn_right.setStyleSheet(
            "background-color: #dc3545; color: white; padding: 20px; font-size: 16px; font-weight: bold;"
        )
        self.btn_right.clicked.connect(lambda: self.send_serial_cmd("2"))

        self.btn_stop = QPushButton("DETENER")
        self.btn_stop.setStyleSheet(
            "background-color: #343a40; color: white; padding: 20px; font-size: 16px; font-weight: bold;"
        )
        self.btn_stop.clicked.connect(lambda: self.send_serial_cmd("S"))

        motor_layout.addWidget(self.btn_left)
        motor_layout.addWidget(self.btn_right)
        motor_layout.addWidget(self.btn_stop)
        group_motor.setLayout(motor_layout)
        control_panel.addWidget(group_motor)

        # Grupo: Guardado de Datos
        group_save = QGroupBox("Gestion de Datos")
        save_layout = QVBoxLayout()

        self.btn_save_txt = QPushButton("Guardar Datos (.txt / .csv)")
        self.btn_save_txt.setStyleSheet(
            "font-size: 15px; padding: 12px; font-weight: bold;"
        )
        self.btn_save_txt.clicked.connect(self.save_txt)

        self.btn_save_img = QPushButton("Guardar Grafica (.png/.jpg)")
        self.btn_save_img.setStyleSheet(
            "font-size: 15px; padding: 12px; font-weight: bold;"
        )
        self.btn_save_img.clicked.connect(self.save_img)

        self.btn_clear = QPushButton("Limpiar Datos de Pantalla")
        self.btn_clear.setStyleSheet(
            "background-color: #ffc107; color: black; font-size: 15px; padding: 12px; font-weight: bold;"
        )
        self.btn_clear.clicked.connect(self.clear_data)

        save_layout.addWidget(self.btn_save_txt)
        save_layout.addWidget(self.btn_save_img)
        save_layout.addWidget(self.btn_clear)
        group_save.setLayout(save_layout)
        control_panel.addWidget(group_save)

        control_panel.addStretch()

        # ----- PANEL DERECHO: Renderizado grafico (matplotlib) -----
        graph_panel = QVBoxLayout()
        self.figure = Figure(figsize=(8, 6), dpi=100)
        self.canvas = FigureCanvas(self.figure)
        self.ax = self.figure.add_subplot(111)
        self.setup_graph()
        graph_panel.addWidget(self.canvas)

        # Distribucion proporcional del espacio de la interfaz (1 parte para los controles, 5 partes para lo grafico)
        main_layout.addLayout(control_panel, 1)
        main_layout.addLayout(graph_panel, 5)

        # Temporizador dedicado al refresco periodico del espacio grafico
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_plot)
        self.enable_controls(False)

    def setup_graph(self):  # Inicializa parametros de la grafica cientifica
        self.ax.clear()
        self.ax.set_title("Patron de Intensidad", fontsize=18, fontweight="bold")
        self.ax.set_xlabel("Posicion (mm)", fontsize=14)
        self.ax.set_ylabel("Voltaje (V)", fontsize=14)
        self.ax.grid(True, linestyle="--", alpha=0.7)
        (self.line,) = self.ax.plot([], [], lw=2, color="#e63946")
        self.canvas.draw()

    def auto_detect_port(
        self,
    ):  # Escanea puertos seriales activos y prioriza automaticamente controladores comunes (CH340 / Arduino)
        self.cb_ports.clear()
        ports = serial.tools.list_ports.comports()
        target_port = None

        for p in ports:
            self.cb_ports.addItem(f"{p.device} - {p.description}")
            if "CH340" in p.description or "Arduino" in p.description:
                target_port = f"{p.device} - {p.description}"

        if target_port:
            self.cb_ports.setCurrentText(target_port)

    def on_laser_change(self, index):
        # Impedir el cambio si el equipo esta conectado
        if self.worker is not None and self.worker.is_running:
            QMessageBox.warning(
                self,
                "Accion Denegada",
                "No puedes cambiar el color del laser mientras el sistema esta conectado.\n"
                "Por favor, deten la medicion y desconecta el equipo para cambiar la fuente.",
            )

            # Restaura el indice anterior bloqueando temporalmente senales para prevenir bucles de eventos
            self.cb_laser.blockSignals(True)
            self.cb_laser.setCurrentIndex(self.current_laser_index)
            self.cb_laser.blockSignals(False)
        else:
            # Si no esta conectado, guardamos el nuevo indice valido
            self.current_laser_index = index

    def toggle_connection(self):
        if self.worker is None or not self.worker.is_running:
            port_text = self.cb_ports.currentText()
            if not port_text:
                QMessageBox.warning(self, "Error", "No se selecciono ningun puerto.")
                return

            port = port_text.split(" - ")[0]

            # Instancia y vinculacion de senales del hilo secundario de adquisicion
            self.worker = SerialWorker(port, 9600, self.resolucion)
            self.worker.new_data_signal.connect(self.receive_data)
            self.worker.error_signal.connect(self.show_error)
            self.worker.start()

            # Envia el comando inicial del laser seleccionado al conectar
            self.send_serial_cmd(self.cb_laser.currentText())

            # Actualizacion visual de estado de botones y activacion del timer de ploteo
            self.btn_connect.setText("Desconectar")
            self.btn_connect.setStyleSheet(
                "background-color: #dc3545; color: white; font-weight: bold; font-size: 18px; padding: 12px;"
            )
            self.enable_controls(True)
            self.timer.start(100)
        else:
            # Procedimiento limpio de cierre de hilo de fondo y restauracion de controles
            self.worker.stop()
            self.worker.wait()
            self.worker = None
            self.btn_connect.setText("Conectar e Iniciar")
            self.btn_connect.setStyleSheet(
                "background-color: #28a745; color: white; font-weight: bold; font-size: 18px; padding: 12px;"
            )
            self.enable_controls(False)
            self.timer.stop()

    def enable_controls(self, state):
        # Habilita de manera masiva los botones de control motriz
        self.btn_left.setEnabled(state)
        self.btn_right.setEnabled(state)
        self.btn_stop.setEnabled(state)

    def send_serial_cmd(self, cmd):
        # Puente de envio de comandos hacia el worker en ejecucion
        if self.worker:
            self.worker.send_command(cmd)

    def receive_data(self, pos, avg, sd):
        self.xdata.append(pos)
        self.ydata.append(avg)
        self.saved_data.append((pos, avg, sd))

    def update_plot(self):
        # Renderiza dinamicamente el espacio de Matplotlib con auto-escalado de ejes
        if self.xdata:
            self.line.set_data(self.xdata, self.ydata)
            self.ax.set_xlim(min(self.xdata), max(self.xdata) + 1)

            ymin, ymax = min(self.ydata), max(self.ydata)
            margen = (ymax - ymin) * 0.1 if ymax != ymin else 1
            self.ax.set_ylim(ymin - margen, ymax + margen)

            self.canvas.draw()

    def clear_data(self):
        # Limpia de forma segura los buffers en memoria tras confirmacion de usuario
        if not self.saved_data:
            return  # No hay nada que limpiar

        respuesta = QMessageBox.question(
            self,
            "Limpiar Datos",
            "Estas seguro de que deseas limpiar la pantalla y la memoria?\n\n"
            "Asegurate de haber guardado tus datos primero.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if respuesta == QMessageBox.Yes:
            self.xdata.clear()
            self.ydata.clear()
            self.saved_data.clear()
            self.setup_graph()  # Restaurar la grafica a su estado inicial en blanco

    def save_txt(
        self,
    ):  # Serializa y exporta los datos recopilados a disco en formatos CSV o TXT
        if not self.saved_data:
            QMessageBox.warning(self, "Aviso", "No hay datos recopilados para guardar.")
            return

        options = QFileDialog.Options()
        # Se agrega "Archivo CSV (*.csv)" a las opciones de filtro
        file_path, filtro = QFileDialog.getSaveFileName(
            self,
            "Guardar Datos de Medicion",
            "",
            "Archivo CSV (*.csv);;Archivos de Texto (*.txt);;Todos los Archivos (*)",
            options=options,
        )
        if file_path:
            try:
                # Verificamos el formato eligido por el usuario
                if "CSV" in filtro:
                    if not file_path.endswith(".csv"):
                        file_path += ".csv"
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(
                            "Position(mm),Average,Std\n"
                        )  # Cabecera separada por comas
                        for row in self.saved_data:
                            f.write(
                                f"{row[0]:.4f},{row[1]},{row[2]:.3f}\n"
                            )  # Datos separados por comas
                else:
                    if not file_path.endswith(".txt"):
                        file_path += ".txt"
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(
                            "Position(mm) Average Std\n"
                        )  # Cabecera separada por espacios
                        for row in self.saved_data:
                            f.write(
                                f"{row[0]:.4f} {row[1]} {row[2]:.3f}\n"
                            )  # Datos separados por espacios

                QMessageBox.information(self, "Exito", "Datos guardados correctamente.")
            except Exception as e:
                QMessageBox.critical(
                    self, "Error", f"No se pudo guardar el archivo: {e}"
                )

    def save_img(
        self,
    ):  # Exporta la figura grafica actual en formatos de alta resolucion industrial (PNG/JPG)
        if not self.xdata:
            QMessageBox.warning(self, "Aviso", "No hay grafica para guardar.")
            return

        options = QFileDialog.Options()
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Guardar Grafica",
            "",
            "Imagen PNG (*.png);;Imagen JPG (*.jpg)",
            options=options,
        )
        if file_path:
            try:
                self.figure.savefig(file_path, dpi=300, bbox_inches="tight")
                QMessageBox.information(self, "Exito", "Imagen guardada correctamente.")
            except Exception as e:
                QMessageBox.critical(
                    self, "Error", f"No se pudo guardar la imagen: {e}"
                )

    def show_error(self, msg):
        QMessageBox.critical(self, "Error de Comunicacion", msg)
        if self.worker and self.worker.is_running:
            self.toggle_connection()

    def closeEvent(self, event):
        # Intercepta el evento nativo de cierre de ventana de la OS para asegurar Teardown de hilos
        if self.worker:
            self.worker.stop()
            self.worker.wait()
        event.accept()


# -------------------------------------------------------------
# Punto de entrada principal de la aplicacion

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # Aplica un estilo multiplataforma moderno y consistente
    window = DiffractionApp()
    window.show()
    sys.exit(app.exec_())
