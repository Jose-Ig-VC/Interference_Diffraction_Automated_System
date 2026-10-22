# SISTEMA DE ADQUISICION DE DATOS - LABORATORIO DE DIFRACCION
# Arquitectura: Interfaz en Tkinter e Hilo de Adquisicion serial.

# importamos las librerias a utilizar:
import serial
import threading
import tkinter as tk
from tkinter import messagebox
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from collections import deque
import sys

# ---------------------------------------------------------------------
# Configuracion de hardware y puerto serie de forma manual
Puerto = "COM7"  # Identificador del puerto fisico de comunicacion
Banda = 9600  # Tasa de baudios estandar para la UART del microcontrolador
ser = serial.Serial(
    Puerto, Banda, timeout=1
)  # Instancia de conexion serie con timeout de bloqueo preventivo

# ---------------------------------------------------------------------
# Definimos las constantes y variables globales:

RESOLUCION = 0.025  # Resolucion espacial con la que el sistema mecanico esta definido

# Definimos las variables de almacenamiento para las mediciones tomadas.
xdata = deque(maxlen=30000)  # Vector temporal para posicion espacial (mm)
ydata = deque(maxlen=30000)  # Vector temporal para valores de voltaje (V)
saved_data = []  # Vector fijo en memoria para exportacion (tuplas: pos, avg, sd)

running_program = True  # Bandera de control de ciclo de vida de los hilos


# ---------------------------------------------------------------------
# Capa de Control del actuador (interfaz Hardware-Software)
def for_left():  # Envia comando por protocolo serial para desplazamiento inverso (Izquierda)
    ser.write(b"1\n")


def for_right():  # Envia comando para desplazamiento directo del carro (Derecha)
    ser.write(b"2\n")


def for_stop():  # Protocolo de parada de emergencia por software
    ser.write(b"S\n")


# ---------------------------------------------------------------------
# Exportacion de datos e imagenes
def save_plot_image():
    dialog = tk.Toplevel(root)
    dialog.title("Guardar Imagen")
    dialog.geometry("900x700")
    dialog.transient(root)

    tk.Label(dialog, text="Nombre del archivo de imagen:", font=("Arial", 16)).pack(
        pady=20
    )

    entry = tk.Entry(dialog, font=("Arial", 18), width=25)
    entry.pack(pady=10)
    entry.focus_set()

    format_var = tk.StringVar(value="PNG")

    tk.Radiobutton(
        dialog,
        text="PNG (recomendado)",
        variable=format_var,
        value="PNG",
        font=("Arial", 14),
    ).pack()

    tk.Radiobutton(
        dialog, text="JPG", variable=format_var, value="JPG", font=("Arial", 14)
    ).pack()

    def confirm():
        name = entry.get()
        if not name:
            return

        ext = format_var.get().lower()
        filename = f"{name}.{ext}"

        try:
            plt.figure(1)
            plt.savefig(filename, dpi=300)
            messagebox.showinfo("Imagen guardada", f"Grafica guardada como {filename}")
            dialog.destroy()
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo guardar imagen: {e}")

    tk.Button(
        dialog,
        text="Guardar Imagen",
        font=("Arial", 12, "bold"),
        bg="#007bff",
        fg="white",
        width=15,
        height=2,
        command=confirm,
    ).pack(pady=20)


def save_to_file():  # Serializa los datos en disco asegurando una validacion previa.
    if len(saved_data) == 0:
        messagebox.showwarning("Warning", "No data to save.")
        return

    dialog = tk.Toplevel(root)
    dialog.title("Guardar Archivo de Datos")
    dialog.geometry("500x300")
    dialog.transient(root)

    tk.Label(
        dialog, text="Ingrese el nombre del archivo de datos:", font=("Arial", 18)
    ).pack(pady=30)

    entry = tk.Entry(dialog, font=("Arial", 20), width=31)
    entry.pack(pady=15)
    entry.focus_set()

    filename_container = []

    def confirm():
        name = entry.get()
        if name:
            filename_container.append(name)
            dialog.destroy()

    tk.Button(
        dialog,
        text="Guardar Datos",
        font=("Arial", 12, "bold"),
        bg="#28a745",
        fg="white",
        width=15,
        height=2,
        command=confirm,
    ).pack(pady=15)

    root.wait_window(dialog)

    if filename_container:
        filename = filename_container[0]
        if not filename.endswith(".txt"):
            filename += ".txt"

        try:
            with open(filename, "w") as f:
                f.write("Position(mm) Average(V) Std(V)\n")
                for row in saved_data:
                    f.write(f"{row[0]:.4f} {row[1]:.4f} {row[2]:.4f}\n")

            messagebox.showinfo("Success", f"Datos guardados como {filename}")
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo guardar: {e}")
            return
    else:
        return

    respuesta = messagebox.askyesno(
        "Guardar grafica", "Desea guardar la imagen de la grafica generada"
    )

    if respuesta:
        save_plot_image()


# ---------------------------------------------------------------------
# Funcion de cierre seguro y gestion de recursos
def Close_all():
    global running_program
    running_program = False

    try:
        ser.write(b"X\n")
        ser.close()
    except:
        pass

    plt.close("all")
    try:
        root.destroy()
    except:
        pass

    sys.exit()


# ---------------------------------------------------------------------
# Hilo de adquisicion de datos en background
def read_serial():
    while running_program:
        try:
            line = ser.readline().decode().strip()
            if line:
                parts = line.split()
                if len(parts) == 3:
                    avg_adc = int(parts[0])
                    step = int(parts[1])
                    sd_adc = float(parts[2])

                    # Factor de conversion
                    adc_volt = 5.0 / 1023.0
                    avg = avg_adc * adc_volt
                    sd = sd_adc * adc_volt
                    pos = step * RESOLUCION

                    xdata.append(pos)
                    ydata.append(avg)
                    saved_data.append((pos, avg, sd))
        except:
            pass


# Inicializacion del hilo demonio de adquisicion en paralelo
threading.Thread(target=read_serial, daemon=True).start()


# ---------------------------------------------------------------------
# Interfaz de control y telemetria en tiempo real (visualizacion grafica)
def start_measurement_interface(laser_color):
    ser.write((laser_color + "\n").encode())

    root2 = tk.Toplevel()
    root2.title("Botones de Control")

    tk.Button(
        root2,
        text="LEFT",
        width=10,
        height=2,
        bg="blue",
        fg="white",
        font=("Arial", 18),
        command=for_left,
    ).pack(pady=5)

    tk.Button(
        root2,
        text="RIGHT",
        width=10,
        height=2,
        bg="red",
        fg="white",
        font=("Arial", 18),
        command=for_right,
    ).pack(pady=5)

    tk.Button(
        root2,
        text="STOP",
        width=10,
        height=2,
        bg="black",
        fg="white",
        font=("Arial", 18),
        command=for_stop,
    ).pack(pady=5)

    tk.Button(
        root2,
        text="SAVE",
        width=10,
        height=2,
        bg="green",
        fg="white",
        font=("Arial", 18),
        command=save_to_file,
    ).pack(pady=5)

    fig, ax = plt.subplots(figsize=(12, 6))
    (line,) = ax.plot([], [], lw=2)

    ax.set_title("Light Pattern", fontsize=28, fontweight="bold")
    ax.set_xlabel("Position (mm)", fontsize=22, fontweight="bold")
    ax.set_ylabel("Raw Voltage (V)", fontsize=22, fontweight="bold")
    ax.tick_params(axis="both", labelsize=18, width=2, length=8)

    for spine in ax.spines.values():
        spine.set_linewidth(2)

    ax.grid(True, linewidth=1)

    def update(frame):
        if xdata:
            line.set_data(xdata, ydata)
            ax.set_xlim(min(xdata), max(xdata) + 1)
            ymin, ymax = min(ydata), max(ydata)
            margen = (ymax - ymin) * 0.1 if ymax != ymin else 10
            ax.set_ylim(ymin - margen, ymax + margen)
            step_actual = xdata[-1] / RESOLUCION
        return (line,)

    anim = animation.FuncAnimation(fig, update, interval=50, cache_frame_data=False)
    plt.show()


# ---------------------------------------------------------------------
# Interfaz de configuracion inicial - Menu principal
root = tk.Tk()
root.title("Laboratorio de Difraccion")

messagebox.showinfo("Bienvenido", "Bienvenido al Experimento De Difraccion")

tk.Label(root, text="Seleccione el color del laser:", font=("Arial", 16)).pack(
    pady=35, padx=35
)

laser_var = tk.StringVar(value="ROJO")

dropdown = tk.OptionMenu(root, laser_var, "ROJO", "VERDE", "AZUL")
dropdown.config(font=("Arial", 15))
dropdown.pack(pady=15)

tk.Button(
    root,
    text="Ir a medicion",
    font=("Arial", 15, "bold"),
    bg="#007bff",
    width=15,
    height=2,
    fg="white",
    command=lambda: start_measurement_interface(laser_var.get()),
).pack(pady=15)

btn_exit = tk.Button(
    root,
    text="Terminar Todo",
    font=("Arial", 15, "bold"),
    bg="darkred",
    fg="white",
    width=15,
    height=2,
    command=Close_all,
)
btn_exit.pack(pady=15)

root.protocol("WM_DELETE_WINDOW", Close_all)
root.mainloop()
