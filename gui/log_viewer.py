"""Visor de log en vivo, colapsable: un botón lo despliega/contrae in-place
en la misma pestaña (no abre una ventana ni un .txt aparte que haya que
volver a abrir para ver contenido nuevo). Mientras está desplegado, sigue
el archivo como "tail -f" -sondea cada POLL_MS con root.after() (ver
gui/qt_compat.py: AfterCapableWindow) y solo lee lo que se agregó desde
la última pasada, así que no vuelve a cargar el archivo entero en cada
ciclo-.

Uso (igual en cada pestaña con botón "Ver log"):

    btn_ver_log = crear_visor_log(base_dir / "cargas_voip.log", root)
    fila_de_botones.addWidget(btn_ver_log)
    layout_de_la_card.addWidget(btn_ver_log.panel)   # el panel expandible, debajo de la fila de botones

`btn_ver_log.panel` empieza oculto (panel.hide()); el propio botón lo
muestra/oculta al togglear.
"""
from PyQt6.QtGui import QTextCharFormat, QColor, QTextCursor
from qfluentwidgets import TransparentPushButton, FluentIcon, PlainTextEdit

FG = "#f2f2f4"
FONDO_TEXTO = "#141416"
BORDE = "#3a3a3c"
AMARILLO = "#ffd60a"
ROJO = "#ff453a"
ACCENT = "#0a84ff"

POLL_MS = 1000
MAX_LINEAS = 500  # recorta el buffer visible -no hace falta tener en memoria un log de días-
TAIL_INICIAL_BYTES = 200_000  # al abrir, arranca mostrando solo el final del log, no el historial completo


def crear_visor_log(log_path, root, texto_boton="Ver log"):
    estado = {"abierto": False, "after_id": None, "pos": 0}

    panel = PlainTextEdit()
    panel.setReadOnly(True)
    panel.setFixedHeight(220)
    panel.hide()
    panel.setStyleSheet(
        f"QPlainTextEdit{{background-color:{FONDO_TEXTO}; color:{FG}; border:1px solid {BORDE}; "
        f"border-radius:8px; font-family:Consolas; font-size:10pt;}}"
    )

    fmt_normal = QTextCharFormat()
    fmt_normal.setForeground(QColor(FG))
    fmt_error = QTextCharFormat()
    fmt_error.setForeground(QColor(ROJO))
    fmt_warning = QTextCharFormat()
    fmt_warning.setForeground(QColor(AMARILLO))
    fmt_prueba = QTextCharFormat()
    fmt_prueba.setForeground(QColor(ACCENT))

    def _formato_de(linea):
        if "[ERROR]" in linea:
            return fmt_error
        if "[WARNING]" in linea:
            return fmt_warning
        if "[PRUEBA]" in linea:
            return fmt_prueba
        return fmt_normal

    def _insertar(fragmento):
        if not fragmento:
            return
        scrollbar = panel.verticalScrollBar()
        al_final = scrollbar.value() >= scrollbar.maximum() - 4
        cursor = panel.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        for linea in fragmento.splitlines(keepends=True):
            cursor.insertText(linea, _formato_de(linea))
        total_lineas = panel.document().blockCount()
        if total_lineas > MAX_LINEAS:
            recorte = QTextCursor(panel.document())
            recorte.movePosition(QTextCursor.MoveOperation.Start)
            recorte.movePosition(QTextCursor.MoveOperation.NextBlock, QTextCursor.MoveMode.KeepAnchor,
                                  total_lineas - MAX_LINEAS)
            recorte.removeSelectedText()
        if al_final:
            scrollbar.setValue(scrollbar.maximum())

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
        panel.clear()
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

        panel.show()
        _ciclo()

    def _cerrar():
        if estado["after_id"] is not None:
            root.after_cancel(estado["after_id"])
            estado["after_id"] = None
        panel.hide()

    def alternar():
        if estado["abierto"]:
            _cerrar()
        else:
            _abrir()
        estado["abierto"] = not estado["abierto"]
        boton.setText(f"Ocultar log  ▲" if estado["abierto"] else f"{texto_boton}  ▼")

    boton = TransparentPushButton(FluentIcon.DOCUMENT, f"{texto_boton}  ▼")
    boton.clicked.connect(alternar)
    boton.panel = panel  # el caller lo agrega a su layout, debajo de la fila de botones
    return boton
