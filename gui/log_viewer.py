"""Visor de log en vivo, colapsable: un botón lo despliega/contrae in-place
en la misma pestaña (no abre una ventana ni un .txt aparte que haya que
volver a abrir para ver contenido nuevo). Mientras está desplegado, sigue
el archivo como "tail -f" -sondea cada POLL_MS con root.after() y solo lee
lo que se agregó desde la última pasada, así que no vuelve a cargar el
archivo entero en cada ciclo-.

Uso (igual en cada pestaña con botón "Ver log"):

    btn_ver_log = crear_visor_log(fila_botones, control_frame, log_path, root)
    btn_ver_log.pack(side="right", padx=3)

`fila_botones` es donde se coloca el botón (el caller decide el orden/lado,
como con cualquier otro ttk.Button de la fila); `control_frame` es el
contenedor donde debe aparecer el panel al desplegarse -normalmente el
mismo LabelFrame "Monitor" que ya contiene esa fila de botones, para que el
panel quede justo debajo-.
"""
import tkinter as tk
from tkinter import ttk

# Mismos colores que gui/dialogs.py (tema oscuro de gui/theme.py).
FG = "#cdd6f4"
FONDO_TEXTO = "#11111b"
BORDE = "#45475a"
AMARILLO = "#f9e2af"
ROJO = "#f38ba8"
ACCENT = "#89b4fa"

POLL_MS = 1000
MAX_LINEAS = 500  # recorta el buffer visible -no hace falta tener en memoria un log de días-
TAIL_INICIAL_BYTES = 200_000  # al abrir, arranca mostrando solo el final del log, no el historial completo


def crear_visor_log(fila_botones, panel_parent, log_path, root, texto_boton="🗒  Ver log"):
    estado = {"abierto": False, "after_id": None, "pos": 0}

    panel = ttk.Frame(panel_parent)
    marco = tk.Frame(panel, bg=FONDO_TEXTO, highlightthickness=1, highlightbackground=BORDE)
    marco.pack(fill="both", expand=True, pady=(8, 0))

    texto = tk.Text(
        marco, height=24, bg=FONDO_TEXTO, fg=FG, insertbackground=FG,
        font=("Consolas", 9), wrap="none", state="disabled",
        borderwidth=0, highlightthickness=0,
    )
    scroll_y = ttk.Scrollbar(marco, orient="vertical", command=texto.yview)
    texto.configure(yscrollcommand=scroll_y.set)
    texto.pack(side="left", fill="both", expand=True)
    scroll_y.pack(side="right", fill="y")

    texto.tag_configure("error", foreground=ROJO)
    texto.tag_configure("warning", foreground=AMARILLO)
    texto.tag_configure("prueba", foreground=ACCENT)

    def _tag_de(linea):
        if "[ERROR]" in linea:
            return "error"
        if "[WARNING]" in linea:
            return "warning"
        if "[PRUEBA]" in linea:
            return "prueba"
        return None

    def _insertar(fragmento):
        if not fragmento:
            return
        texto.configure(state="normal")
        al_final = texto.yview()[1] >= 0.999
        for linea in fragmento.splitlines(keepends=True):
            tag = _tag_de(linea)
            texto.insert("end", linea, tag) if tag else texto.insert("end", linea)
        total_lineas = int(texto.index("end-1c").split(".")[0])
        if total_lineas > MAX_LINEAS:
            texto.delete("1.0", f"{total_lineas - MAX_LINEAS + 1}.0")
        if al_final:
            texto.see("end")
        texto.configure(state="disabled")

    def _leer_delta():
        """Lee solo lo agregado desde la última pasada. Si el archivo rotó
        a medianoche (TimedRotatingFileHandler) o se borró -tamaño actual
        menor que la posición ya leída-, se relee desde el principio en
        vez de intentar seguir desde un offset que ya no existe."""
        if not log_path.exists():
            return ""
        tam = log_path.stat().st_size
        if tam < estado["pos"]:
            estado["pos"] = 0
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(estado["pos"])
            data = f.read()
            estado["pos"] = f.tell()
        return data

    def _ciclo():
        _insertar(_leer_delta())
        estado["after_id"] = root.after(POLL_MS, _ciclo)

    def _abrir():
        texto.configure(state="normal")
        texto.delete("1.0", "end")
        texto.configure(state="disabled")
        estado["pos"] = 0

        if log_path.exists():
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                tam = log_path.stat().st_size
                if tam > TAIL_INICIAL_BYTES:
                    f.seek(tam - TAIL_INICIAL_BYTES)
                    f.readline()  # descarta la línea que quedó partida a la mitad
                inicial = f.read()
                estado["pos"] = f.tell()
            _insertar(inicial)
        else:
            _insertar("(Todavía no se ha generado este archivo de log; en cuanto el "
                       "monitor escriba algo aparecerá aquí.)\n")

        panel.pack(fill="both", expand=True, pady=(0, 8))
        _ciclo()

    def _cerrar():
        if estado["after_id"] is not None:
            root.after_cancel(estado["after_id"])
            estado["after_id"] = None
        panel.pack_forget()

    def alternar():
        if estado["abierto"]:
            _cerrar()
        else:
            _abrir()
        estado["abierto"] = not estado["abierto"]
        boton.configure(text=f"🗒  Ocultar log  ▲" if estado["abierto"] else f"{texto_boton}  ▼")

    boton = ttk.Button(fila_botones, text=f"{texto_boton}  ▼", command=alternar)
    return boton
