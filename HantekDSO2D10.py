import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import pyvisa as visa
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import threading
import time
import csv
from datetime import datetime
import struct

class HantekDSO2000:
    """Clase simplificada para control de Hantek DSO2000 vía SCPI"""
    def __init__(self, resource_name=None, timeout=5000):
        self.rm = visa.ResourceManager()
        self._osci = None
        self.idn = ""
        self.timeout = timeout
        if resource_name:
            self.connect(resource_name)

    def connect(self, resource_name):
        try:
            self._osci = self.rm.open_resource(resource_name)
            self._osci.timeout = self.timeout
            self.idn = self._osci.query("*IDN?").strip()
            print(f"Conectado a: {self.idn}")
            return True, self.idn
        except Exception as e:
            return False, str(e)

    def disconnect(self):
        try:
            if self._osci:
                self._osci.close()
            self._osci = None
        except:
            pass

    def set_channel_scale(self, ch, scale):
        self._osci.write(f":CHANnel{ch}:SCALe {scale}")

    def set_channel_offset(self, ch, offset):
        self._osci.write(f":CHANnel{ch}:OFFSet {offset}")

    def set_timebase_scale(self, scale):
        self._osci.write(f":TIMebase:SCALe {scale}")

    def set_timebase_offset(self, offset):
        self._osci.write(f":TIMebase:MAIN:OFFSet {offset}")

    def set_memory_depth(self, points):
        """Configura el memory depth (ACQuire:POINts)"""
        self._osci.write(f":ACQuire:POINts {points}")

    def get_waveform(self, ch, progress_callback=None):
        """Obtiene los datos de la forma de onda usando el método PRIVate"""
        try:
            # Variables para almacenar datos
            samples_data = bytes()
            samples_total = -1
            samples_got = 0
            meta = bytes()
            
            # Función interna para leer paquetes
            def readPacket():
                nonlocal samples_total, samples_data, samples_got, meta
                
                self._osci.write("PRIVate:WAVeform:DATA:ALL?\n")
                inp = self._osci.read_raw()
                
                if chr(inp[0]) != '#' or chr(inp[1]) != '9':
                    return False
                
                this_len = int(inp[2:11].decode())
                if this_len == 0:
                    return False
                
                total_smpls = int(inp[11:20].decode())
                cur_pos = int(inp[20:29].decode())
                
                start = 29
                end_of_meta = 128
                
                if samples_total == -1:
                    samples_total = total_smpls
                    samples_data = bytearray(samples_total)
                    meta = inp[start:end_of_meta]
                else:
                    if samples_total != total_smpls:
                        return False
                
                cur_len = len(inp) - end_of_meta
                for i in range(0, cur_len):
                    samples_data[cur_pos + i] = inp[end_of_meta + i]
                samples_got += cur_len
                
                # Callback de progreso
                if progress_callback:
                    progress_callback(samples_got, samples_total)
                
                return samples_got == samples_total
            
            # Leer todos los paquetes
            while not readPacket():
                pass
            
            # Decodificar metadata
            res = struct.unpack('cc 16x 7s7s7s7s cccc 9s 6s 9x 9s 6s 10x', meta)
            (running, trigger, 
             v1, v2, v3, v4,
             c1e, c2e, c3e, c4e,
             sampling_rate, sampling_multiple,
             trigger_time, acq_start) = res
            
            channel_count = sum([int(c1e), int(c2e), int(c3e), int(c4e)])
            
            # Decodificar sampling rate
            try:
                sr = float(sampling_rate.decode())
            except:
                sr = float(sampling_rate)
            
            # Decodificar trigger time
            try:
                tt_str = trigger_time.decode() if isinstance(trigger_time, bytes) else trigger_time
                tt = float(tt_str.strip())
            except:
                tt = 0.0
            
            # Extraer muestras del canal específico
            block_len = 2000
            samples = list()
            
            for i in range((ch - 1) * block_len, len(samples_data), block_len * channel_count):
                samples.extend(struct.unpack('%db' % block_len, samples_data[i:i + block_len]))
            
            # Obtener parámetros del canal para conversión a voltaje
            offset = float(self._osci.query(f':CHANnel{ch}:OFFSet?'))
            scale = float(self._osci.query(f':CHANnel{ch}:SCALe?'))
            
            # Convertir a voltaje absoluto
            grid_y = 25
            voltaje = np.array([v / grid_y * scale - offset for v in samples])
            
            # Generar eje de tiempo
            tiempo = np.array([i / sr - tt for i in range(len(samples))])
            
            return tiempo, voltaje
            
        except Exception as e:
            print(f"Error leyendo canal {ch}: {e}")
            return None, None

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Hantek DSO2000 - Versión Simplificada")
        self.root.geometry("1200x750")

        self.scope = HantekDSO2000()
        self.is_connected = False
        self.is_running = False

        self.setup_ui()

    def setup_ui(self):
        # --- Panel Superior: Conexión ---
        conn_frame = ttk.LabelFrame(self.root, text="Conexión VISA")
        conn_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(conn_frame, text="Recurso:").pack(side="left", padx=5)
        self.visa_entry = ttk.Combobox(conn_frame, width=40)
        self.visa_entry.pack(side="left", padx=5)

        ttk.Button(conn_frame, text="🔍 Buscar", command=self.refresh_resources).pack(side="left", padx=2)
        ttk.Button(conn_frame, text="🤖 Auto-detectar", command=self.autodetect_hantek).pack(side="left", padx=2)
        self.btn_connect = ttk.Button(conn_frame, text="Conectar", command=self.toggle_connection)
        self.btn_connect.pack(side="left", padx=10)

        self.status_var = tk.StringVar(value="Desconectado")
        ttk.Label(conn_frame, textvariable=self.status_var, foreground="blue").pack(side="right", padx=10)

        # --- Panel Principal ---
        main_container = ttk.Frame(self.root)
        main_container.pack(fill="both", expand=True, padx=10, pady=5)

        # Columna Izquierda: Controles
        ctrl_frame = ttk.Frame(main_container, width=300)
        ctrl_frame.pack(side="left", fill="y", padx=5)

        # Memory Depth
        mem_frame = ttk.LabelFrame(ctrl_frame, text="Memory Depth")
        mem_frame.pack(fill="x", padx=5, pady=5)

        ttk.Label(mem_frame, text="Puntos:").grid(row=0, column=0, sticky="w", padx=5)
        self.mem_depth = ttk.Combobox(mem_frame, values=["4K", "40K", "400K", "4M"], width=10)
        self.mem_depth.set("4K")
        self.mem_depth.grid(row=0, column=1, padx=5, pady=5)
        self.mem_depth.bind("<<ComboboxSelected>>", self.set_memory_depth)

        # Controles de Canales
        ch_frame = ttk.LabelFrame(ctrl_frame, text="Canales")
        ch_frame.pack(fill="x", padx=5, pady=5)

        for ch in [1, 2]:
            frame = ttk.LabelFrame(ch_frame, text=f"Canal {ch}")
            frame.pack(fill="x", padx=5, pady=5)

            ttk.Label(frame, text="Escala (V/div):").grid(row=0, column=0, sticky="w", padx=5)
            scale = ttk.Combobox(frame, values=["0.01", "0.02", "0.05", "0.1", "0.2", "0.5", "1", "2", "5", "10"], width=10)
            scale.set("1")
            scale.grid(row=0, column=1, padx=5, pady=2)
            scale.bind("<<ComboboxSelected>>", lambda e, c=ch, s=scale: self.scope.set_channel_scale(c, s.get()))

        # Controles de Tiempo
        time_frame = ttk.LabelFrame(ctrl_frame, text="Base de Tiempo")
        time_frame.pack(fill="x", padx=5, pady=5)

        ttk.Label(time_frame, text="Escala (s/div):").grid(row=0, column=0, sticky="w", padx=5)
        self.t_scale = ttk.Combobox(time_frame, values=["1e-9", "2e-9", "5e-9", "1e-8", "2e-8", "5e-8", "1e-7", "2e-7", "5e-7", "1e-6", "2e-6", "5e-6", "1e-5", "2e-5", "5e-5", "1e-4", "2e-4", "5e-4", "1e-3", "2e-3", "5e-3", "1e-2", "2e-2", "5e-2", "1e-1", "2e-1", "5e-1", "1", "2", "5", "10", "20", "50"], width=10)
        self.t_scale.set("1e-3")
        self.t_scale.grid(row=0, column=1, padx=5, pady=2)
        self.t_scale.bind("<<ComboboxSelected>>", lambda e: self.scope.set_timebase_scale(self.t_scale.get()))

        # Columna Derecha: Gráfico
        plot_frame = ttk.Frame(main_container)
        plot_frame.pack(side="right", fill="both", expand=True)

        self.fig, self.ax = plt.subplots(figsize=(7, 5), facecolor='#121212')
        self.ax.set_facecolor('black')
        self.ax.grid(color='gray', linestyle='--', linewidth=0.5)
        self.ax.tick_params(colors='white', labelsize=12)
        self.ax.set_xlabel('Tiempo (s)', color='white', fontsize=14)
        self.ax.set_ylabel('Voltaje (V)', color='white', fontsize=14)

        self.canvas = FigureCanvasTkAgg(self.fig, master=plot_frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        # Barra de Acciones
        action_bar = ttk.Frame(plot_frame)
        action_bar.pack(fill="x", pady=5)

        ttk.Button(action_bar, text="🔄 Capturar", command=self.update_plot).pack(side="left", padx=5)
        self.btn_cont = ttk.Button(action_bar, text="▶ Continuo", command=self.toggle_continuous)
        self.btn_cont.pack(side="left", padx=5)
        ttk.Button(action_bar, text="⏹ Parar", command=self.stop_continuous).pack(side="left", padx=5)
        ttk.Button(action_bar, text="💾 Guardar CSV", command=self.save_data_csv).pack(side="left", padx=5)
        ttk.Button(action_bar, text="🔓 Desbloquear", command=self.unlock_button).pack(side="left", padx=5)

        # Barra de progreso (inicialmente oculta)
        self.progress_frame = ttk.Frame(plot_frame)
        self.progress_label = ttk.Label(self.progress_frame, text="")
        self.progress_label.pack(side="left", padx=5)
        self.progress_bar = ttk.Progressbar(self.progress_frame, mode='determinate', length=300)
        self.progress_bar.pack(side="left", padx=5)

    def set_memory_depth(self, event=None):
        if not self.is_connected:
            return
        
        depth_map = {"4K": 4000, "40K": 40000, "400K": 400000, "4M": 4000000}
        depth_str = self.mem_depth.get()
        depth_val = depth_map.get(depth_str, 4000)
        
        try:
            self.scope.set_memory_depth(depth_val)
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo configurar memory depth: {e}")

    def refresh_resources(self):
        try:
            res = self.scope.rm.list_resources()
            self.visa_entry["values"] = res
            if res: 
                self.visa_entry.set(res[0])
            self.status_var.set(f"Encontrados {len(res)} recursos")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def autodetect_hantek(self):
        self.status_var.set("Buscando Hantek...")
        self.root.update()
        res = self.scope.rm.list_resources()
        for r in res:
            try:
                temp_instr = self.scope.rm.open_resource(r)
                temp_instr.timeout = 1000
                idn = temp_instr.query("*IDN?")
                temp_instr.close()
                if "Hantek" in idn or "HANTEK" in idn or "DSO2" in idn:
                    self.visa_entry.set(r)
                    self.status_var.set(f"Detectado: {idn[:30]}...")
                    return
            except: 
                continue
        messagebox.showinfo("Auto-detectar", "No se encontró ningún Hantek.")

    def toggle_connection(self):
        if not self.is_connected:
            res = self.visa_entry.get()
            if not res:
                messagebox.showwarning("Conexión", "Seleccione un recurso VISA")
                return
            success, msg = self.scope.connect(res)
            if success:
                self.is_connected = True
                self.btn_connect.config(text="Desconectar")
                self.status_var.set(f"Conectado: {msg[:40]}")
                # Configurar memory depth inicial
                self.set_memory_depth()
            else:
                messagebox.showerror("Error al conectar", f"Detalle: {msg}\n\nVerifique el cable USB y que el equipo no esté ocupado.")
        else:
            self.stop_continuous()
            self.scope.disconnect()
            self.is_connected = False
            self.btn_connect.config(text="Conectar")
            self.status_var.set("Desconectado")

    def update_progress(self, current, total):
        """Callback para actualizar la barra de progreso"""
        progress = (current / total) * 100
        self.progress_bar['value'] = progress
        self.progress_label.config(text=f"Leyendo: {current}/{total} bytes ({progress:.1f}%)")
        self.root.update_idletasks()

    def update_plot(self):
        if not self.is_connected: 
            return

        # Mostrar barra de progreso
        self.progress_frame.pack(fill="x", pady=5)
        self.progress_bar['value'] = 0
        self.progress_label.config(text="Iniciando captura...")
        self.root.update_idletasks()

        self.ax.clear()
        self.ax.set_facecolor('black')
        self.ax.grid(color='gray', linestyle='--', linewidth=0.5)
        self.ax.tick_params(colors='white', labelsize=12)
        self.ax.set_xlabel('Tiempo (s)', color='white', fontsize=14)
        self.ax.set_ylabel('Voltaje (V)', color='white', fontsize=14)

        colors = {1: 'yellow', 2: 'cyan'}
        has_data = False
        for ch in [1, 2]:
            t, v = self.scope.get_waveform(ch, progress_callback=self.update_progress)
            if t is not None and len(t) > 0:
                self.ax.plot(t, v, color=colors[ch], label=f"CH{ch}", linewidth=1.5)
                has_data = True

        if has_data:
            self.ax.legend(loc='upper right', fontsize=12)
        self.canvas.draw()

        # Ocultar barra de progreso
        self.progress_frame.pack_forget()
        self.status_var.set("Captura completada")

    def toggle_continuous(self):
        if not self.is_connected: 
            return
        self.is_running = True
        self.btn_cont.config(state="disabled")
        threading.Thread(target=self.loop_capture, daemon=True).start()

    def stop_continuous(self):
        self.is_running = False
        self.btn_cont.config(state="normal")
    
    def unlock_button(self):
        """Desbloquea el botón de captura continua si quedó bloqueado"""
        self.is_running = False
        self.btn_cont.config(state="normal")
        self.status_var.set("Botón desbloqueado")

    def loop_capture(self):
        while self.is_running:
            self.update_plot()
            time.sleep(0.1)

    def save_data_csv(self):
        if not self.is_connected: 
            return

        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if not path: 
            return

        # Mostrar barra de progreso
        self.progress_frame.pack(fill="x", pady=5)
        self.progress_bar['value'] = 0
        self.progress_label.config(text="Guardando datos...")
        self.root.update_idletasks()

        with open(path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(["# Hantek DSO2000 Data Export"])
            writer.writerow([f"# Instrument: {self.scope.idn}"])
            writer.writerow([f"# Date: {datetime.now()}"])
            writer.writerow([f"# Memory Depth: {self.mem_depth.get()}"])
            writer.writerow([])

            self.progress_bar['value'] = 25
            self.root.update_idletasks()

            t1, v1 = self.scope.get_waveform(1, progress_callback=self.update_progress)
            
            self.progress_bar['value'] = 50
            self.root.update_idletasks()
            
            t2, v2 = self.scope.get_waveform(2, progress_callback=self.update_progress)

            self.progress_bar['value'] = 75
            self.progress_label.config(text="Escribiendo archivo...")
            self.root.update_idletasks()

            writer.writerow(["Time_CH1[s]", "Volt_CH1[V]", "Time_CH2[s]", "Volt_CH2[V]"])
            max_len = max(len(t1) if t1 is not None else 0, len(t2) if t2 is not None else 0)

            for i in range(max_len):
                row = []
                row.append(t1[i] if (t1 is not None and i < len(t1)) else "")
                row.append(v1[i] if (v1 is not None and i < len(v1)) else "")
                row.append(t2[i] if (t2 is not None and i < len(t2)) else "")
                row.append(v2[i] if (v2 is not None and i < len(v2)) else "")
                writer.writerow(row)

        self.progress_bar['value'] = 100
        self.progress_label.config(text="Guardado completo")
        self.root.update_idletasks()
        
        time.sleep(1)
        self.progress_frame.pack_forget()
        
        messagebox.showinfo("Guardar", f"Datos guardados en:\n{path}")

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()