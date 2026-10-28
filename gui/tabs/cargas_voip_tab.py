"""Pestaña "Cargas VOIP": configura y controla el monitor de validación
de cargas (Bitrix24 + OCR). La lógica del monitor en sí vive en
cargas_voip.py (raíz del proyecto), no aquí — este archivo solo construye
la pestaña."""
import os
import threading

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QFileDialog
from qfluentwidgets import LineEdit, PrimaryPushButton, PushButton, BodyLabel, CaptionLabel, FluentIcon, HeaderCardWidget

from core.tesseract_tools import find_tesseract_exe, install_tesseract, has_spanish_traineddata
from gui.process_control import ManagedProcess
from gui import dialogs as messagebox
from gui.dialogs import boton_ayuda, checkbox_modo_prueba
from gui.qt_compat import LabelVar
from gui.log_viewer import crear_visor_log
from gui.widgets import fila_campo, texto_informativo, columna, pagina_con_scroll

# Debe coincidir con BITRIX_MENSAJES_LIMIT_MAX en cargas_voip.py: Bitrix24
# no permite traer más de esta cantidad de mensajes por consulta a
# im.dialog.messages.get, así que no tiene sentido guardar una "ventana de
# reintento" más alta -el monitor igual la recorta, pero mejor que el campo
# ya refleje el tope real en vez de mostrar un número que no hace nada-.
LOOKBACK_MAX = 200


def build(ctx):
    root = ctx.root
    BG = ctx.BG
    ACCENT = ctx.ACCENT
    config = ctx.config
    save_config = ctx.save_config
    base_dir = ctx.base_dir

    try:
        import cargas_voip
        cv_cfg = cargas_voip.load_cargas_voip_config()
    except Exception:
        cv_cfg = config.get("cargas_voip_config", {})

    page, layout = pagina_con_scroll(BG)

    # ---------------- Chat de Bitrix24 ----------------
    cv_chat_edit = LineEdit()
    cv_chat_edit.setText(cv_cfg.get("chat_id", "chat135"))

    cv_estado_sheet_edit = LineEdit()
    cv_estado_sheet_edit.setText(cv_cfg.get(
        "estado_sheet_url",
        "https://docs.google.com/spreadsheets/d/1ql2y9RBBS4aelGIIawJNielWYo_BUwxtNRCdvVy_gac/edit?gid=0"))

    conexion_content = columna(
        texto_informativo("ℹ  El Webhook de Bitrix24 ahora se configura una sola vez en la pestaña Configuración."),
        fila_campo("Chat ID:", cv_chat_edit),
        fila_campo(
            "Hoja de estado compartido (URL):", cv_estado_sheet_edit,
            ayuda=boton_ayuda(
                "Aquí se guarda hasta qué mensaje ya se revisó, compartido entre las 3 PC que "
                "usan este monitor por turnos -así ninguna reprocesa lo que otra ya validó-. "
                "Usa las mismas credenciales de Google ya configuradas en la pestaña Configuración.",
                titulo="Hoja de estado compartido",
            ),
        ),
    )
    conexion_card = ctx.seccion_plegable(FluentIcon.LINK, "Chat de Bitrix24", conexion_content, "cargas_voip.conexion")
    layout.addWidget(conexion_card)

    # ---------------- Validación ----------------
    cv_poll_edit = LineEdit()
    cv_poll_edit.setText(str(cv_cfg.get("poll_interval", 20)))

    cv_tol_edit = LineEdit()
    cv_tol_edit.setText(str(cv_cfg.get("tolerancia_pct", 0.5)))

    cv_tol_trm_edit = LineEdit()
    cv_tol_trm_edit.setText(str(cv_cfg.get("tolerancia_trm_pct", 0.1)))

    cv_lookback_edit = LineEdit()
    cv_lookback_edit.setText(str(min(cv_cfg.get("lookback_n", 10), LOOKBACK_MAX)))

    def _limitar_lookback():
        try:
            valor = int(cv_lookback_edit.text())
        except ValueError:
            return  # se valida en serio al guardar; acá solo se recorta si ya es un número
        if valor > LOOKBACK_MAX:
            cv_lookback_edit.setText(str(LOOKBACK_MAX))

    cv_lookback_edit.editingFinished.connect(_limitar_lookback)

    cv_dry_run_chk = checkbox_modo_prueba(bool(cv_cfg.get("dry_run", True)))

    # Ancho fijo de etiqueta/ícono para que los 4 campos numéricos de abajo
    # arranquen todos en la misma columna, sin importar que "Ventana de
    # reintento (mensajes atrás):" sea mucho más largo que "Tolerancia TRM
    # (%):" -reportado 2026-10-28 como "los números no quedan alineados".
    ANCHO_LABEL_VALIDACION = 260
    ANCHO_ICONO_VALIDACION = 26

    validacion_content = columna(
        fila_campo(
            "Intervalo de sondeo (s):", cv_poll_edit,
            ancho_label=ANCHO_LABEL_VALIDACION, ancho_icono=ANCHO_ICONO_VALIDACION,
        ),
        fila_campo(
            "Tolerancia monto USD (%):", cv_tol_edit,
            ancho_label=ANCHO_LABEL_VALIDACION, ancho_icono=ANCHO_ICONO_VALIDACION,
        ),
        fila_campo(
            "Tolerancia TRM (%):", cv_tol_trm_edit,
            ancho_label=ANCHO_LABEL_VALIDACION, ancho_icono=ANCHO_ICONO_VALIDACION,
        ),
        fila_campo(
            "Ventana de reintento (mensajes atrás):", cv_lookback_edit,
            ancho_label=ANCHO_LABEL_VALIDACION, ancho_icono=ANCHO_ICONO_VALIDACION,
            ayuda=boton_ayuda(
                "En cada ciclo, además de los mensajes nuevos, revisa esta cantidad de mensajes anteriores "
                "al último ya visto -por si alguno se quedó sin confirmar- y lo reprocesa si no consta como "
                "ya confirmado (ni en el registro local de esta PC ni en el de las otras, compartido en la "
                "misma hoja de Sheets del estado).",
                titulo="Ventana de reintento",
            ),
        ),
        fila_campo(
            "", cv_dry_run_chk,
            ayuda=boton_ayuda(
                "Detecta y registra qué haría en el log, sin escribir nada en el chat de Bitrix24 ni modificar "
                "el CSV de auditoría ni el estado compartido (Sheets). Actívalo para probar cambios sin que el "
                "equipo vea mensajes de prueba ni se entere si algo sale mal.",
                titulo="Modo de prueba (dry-run)",
            ),
        ),
    )
    validacion_card = ctx.seccion_plegable(FluentIcon.ACCEPT, "Validación", validacion_content, "cargas_voip.validacion")
    layout.addWidget(validacion_card)

    # ---------------- Control del monitor (NO plegable) ----------------
    control_card = HeaderCardWidget("▶  Monitor")

    cargas_voip_status_label = BodyLabel()
    cargas_voip_status_label.setStyleSheet(f"color: {ACCENT}; font-weight: bold;")
    cargas_voip_status_var = LabelVar(cargas_voip_status_label)

    monitor = ctx.register_process(ManagedProcess(
        root=root,
        launch_cmd=lambda: ctx.get_launch_cmd(['--cargas-voip']),
    )).bind(cargas_voip_status_var, on_change=lambda running: (
        btn_cv_iniciar.setEnabled(not running),
        btn_cv_detener.setEnabled(running),
    ))

    def cv_iniciar():
        if monitor.running:
            messagebox.showinfo("Cargas VOIP", "El monitor ya está en ejecución.")
            return
        if not config.get("bitrix_webhook_url", "").strip():
            messagebox.showerror("Error", "Configura primero la Webhook URL de Bitrix24 en la pestaña Configuración.")
            return
        tesseract_exe = find_tesseract_exe(config.get("cargas_voip_config", {}).get("tesseract_cmd", ""))
        if not tesseract_exe:
            messagebox.showerror(
                "Tesseract requerido",
                "El OCR es obligatorio para validar las capturas: instala Tesseract con el botón "
                "'⬇  Descargar e instalar Tesseract' antes de iniciar el monitor."
            )
            return
        if not has_spanish_traineddata(tesseract_exe):
            messagebox.showerror(
                "Falta el paquete de idioma español",
                "Tesseract está instalado pero sin el paquete de español (spa.traineddata). "
                "El OCR lee los montos en silencio solo con inglés, lo que puede generar lecturas "
                "incorrectas. Usa el botón '⬇  Descargar e instalar Tesseract' para instalarlo "
                "antes de iniciar el monitor."
            )
            return

        monitor.start()
        messagebox.showinfo("Cargas VOIP", "Monitor iniciado en segundo plano.\nRevisa 'cargas_voip.log' para ver la actividad.")

    def cv_detener():
        monitor.stop()
        messagebox.showinfo("Cargas VOIP", "Monitor detenido.")

    def cv_ver_registro():
        csv_path = base_dir / "registro_cargas.csv"
        if not csv_path.exists():
            messagebox.showinfo("Registro", "Todavía no se ha generado 'registro_cargas.csv'.")
            return
        os.startfile(str(csv_path))

    btn_cv_iniciar = PrimaryPushButton(FluentIcon.PLAY, "Iniciar Monitor")
    btn_cv_iniciar.clicked.connect(cv_iniciar)

    btn_cv_detener = PushButton(FluentIcon.PAUSE, "Detener Monitor")
    btn_cv_detener.setEnabled(False)
    btn_cv_detener.clicked.connect(cv_detener)

    btn_ver_registro = PushButton(FluentIcon.DOCUMENT, "Ver registro (CSV)")
    btn_ver_registro.clicked.connect(cv_ver_registro)

    btn_ver_log = crear_visor_log(base_dir / "cargas_voip.log", root)

    fila_botones = QHBoxLayout()
    fila_botones.addWidget(btn_cv_iniciar)
    fila_botones.addWidget(btn_cv_detener)
    fila_botones.addSpacing(10)
    fila_botones.addWidget(cargas_voip_status_label)
    fila_botones.addStretch(1)
    fila_botones.addWidget(btn_ver_registro)
    fila_botones.addWidget(btn_ver_log)
    fila_botones_widget = QWidget()
    fila_botones_widget.setLayout(fila_botones)

    monitor_content = columna(fila_botones_widget, btn_ver_log.panel, margins=(24, 16, 24, 20))
    control_card.viewLayout.addWidget(monitor_content)
    layout.addWidget(control_card)

    # ---------------- OCR (Tesseract) ----------------
    # Se agrega DESPUÉS de Monitor a propósito: Chat de Bitrix24,
    # Validación, Monitor, OCR (Tesseract), en ese orden.
    cv_tesseract_edit = LineEdit()
    cv_tesseract_edit.setPlaceholderText(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    cv_tesseract_edit.setText(cv_cfg.get("tesseract_cmd", ""))

    def seleccionar_tesseract():
        path, _ = QFileDialog.getOpenFileName(
            root, "Seleccionar tesseract.exe", "", "Ejecutable (*.exe);;Todos (*.*)"
        )
        if path:
            cv_tesseract_edit.setText(path)
            actualizar_estado_tesseract()

    btn_examinar = PushButton("Examinar")
    btn_examinar.clicked.connect(seleccionar_tesseract)

    cv_tesseract_status_label = CaptionLabel("Comprobando...")
    cv_tesseract_status_label.setStyleSheet("color: #9399b2;")

    def actualizar_estado_tesseract():
        found = find_tesseract_exe(cv_tesseract_edit.text().strip())
        if found:
            cv_tesseract_status_label.setText(f"✓  Detectado: {found}")
            if not cv_tesseract_edit.text().strip():
                cv_tesseract_edit.setText(found)
        else:
            cv_tesseract_status_label.setText("✗  No se detectó Tesseract OCR en este equipo.")

    def descargar_instalar_tesseract():
        respuesta = messagebox.askyesno(
            "Descargar e instalar Tesseract",
            "Esto descargará el instalador oficial más reciente de Tesseract OCR "
            "desde GitHub (tesseract-ocr/tesseract) y lo ejecutará.\n\n"
            "Windows pedirá permiso de administrador porque se instala en "
            "'Program Files'. Acepta ese aviso para continuar.\n\n¿Continuar?"
        )
        if not respuesta:
            return

        btn_descargar_tesseract.setEnabled(False)
        btn_descargar_tesseract.setText("Instalando...")
        cv_tesseract_status_label.setText("Consultando última versión disponible...")

        def progreso(msg):
            root.after(0, lambda: cv_tesseract_status_label.setText(msg))

        def tarea():
            try:
                version, exe_path = install_tesseract(progress_cb=progreso)

                def terminar_ok():
                    if exe_path:
                        cv_tesseract_edit.setText(exe_path)
                        config.setdefault("cargas_voip_config", {})["tesseract_cmd"] = exe_path
                        save_config(config)
                        actualizar_estado_tesseract()
                        messagebox.showinfo(
                            "Tesseract instalado",
                            f"Tesseract {version} instalado correctamente.\nRuta guardada: {exe_path}"
                        )
                    else:
                        actualizar_estado_tesseract()
                        messagebox.showwarning(
                            "Revisar instalación",
                            "Se ejecutó el instalador pero no se pudo confirmar la instalación. "
                            "Si se abrió su asistente, complétalo y vuelve a intentar."
                        )
                root.after(0, terminar_ok)
            except Exception as e:
                root.after(0, lambda: (
                    actualizar_estado_tesseract(),
                    messagebox.showerror("Error", f"No se pudo instalar Tesseract:\n{e}")
                ))
            finally:
                root.after(0, lambda: (btn_descargar_tesseract.setEnabled(True), btn_descargar_tesseract.setText("Descargar e instalar Tesseract")))

        threading.Thread(target=tarea, daemon=True).start()

    btn_descargar_tesseract = PrimaryPushButton(FluentIcon.DOWNLOAD, "Descargar e instalar Tesseract")
    btn_descargar_tesseract.clicked.connect(descargar_instalar_tesseract)

    fila_estado_tesseract = QHBoxLayout()
    fila_estado_tesseract.addWidget(cv_tesseract_status_label)
    fila_estado_tesseract.addStretch(1)
    fila_estado_tesseract.addWidget(btn_descargar_tesseract)
    fila_estado_tesseract_widget = QWidget()
    fila_estado_tesseract_widget.setLayout(fila_estado_tesseract)

    ocr_content = columna(
        texto_informativo(
            "El OCR es obligatorio para validar las capturas de carga: Tesseract debe estar instalado "
            "(usa el botón de abajo para descargarlo) antes de poder iniciar el monitor. El registro "
            "detallado de cada lectura queda siempre activado en 'cargas_voip.log'."
        ),
        fila_campo("Ruta tesseract.exe:", cv_tesseract_edit, extra=btn_examinar),
        fila_estado_tesseract_widget,
    )
    ocr_card = ctx.seccion_plegable(FluentIcon.SEARCH, "OCR (Tesseract)", ocr_content, "cargas_voip.ocr")
    layout.addWidget(ocr_card)
    actualizar_estado_tesseract()

    def guardar_cargas_voip():
        try:
            poll_interval = int(cv_poll_edit.text())
        except ValueError:
            messagebox.showerror("Error", "El intervalo de sondeo debe ser un número entero de segundos.")
            return
        try:
            tolerancia_pct = float(cv_tol_edit.text())
            tolerancia_trm_pct = float(cv_tol_trm_edit.text())
        except ValueError:
            messagebox.showerror("Error", "Las tolerancias deben ser números.")
            return
        try:
            lookback_n = int(cv_lookback_edit.text())
        except ValueError:
            messagebox.showerror("Error", "La ventana de reintento debe ser un número entero de mensajes.")
            return
        if lookback_n > LOOKBACK_MAX:
            lookback_n = LOOKBACK_MAX
            cv_lookback_edit.setText(str(lookback_n))

        config["cargas_voip_config"] = {
            "chat_id": cv_chat_edit.text().strip() or "chat135",
            "poll_interval": poll_interval,
            "tolerancia_pct": tolerancia_pct,
            "tolerancia_trm_pct": tolerancia_trm_pct,
            "tesseract_cmd": cv_tesseract_edit.text().strip(),
            "debug_ocr": True,
            "estado_sheet_url": cv_estado_sheet_edit.text().strip(),
            "lookback_n": lookback_n,
            "dry_run": bool(cv_dry_run_chk.isChecked()),
        }
        save_config(config)
        messagebox.showinfo("Guardado", "Configuración de Cargas VOIP actualizada.")

    # Botón de guardar al final, afuera de las secciones plegables -aplica
    # a la configuración completa de la pestaña, no solo a la de OCR-.
    btn_guardar = PrimaryPushButton(FluentIcon.SAVE, "Guardar configuración")
    btn_guardar.clicked.connect(guardar_cargas_voip)
    fila_guardar = QHBoxLayout()
    fila_guardar.addStretch(1)
    fila_guardar.addWidget(btn_guardar)
    layout.addLayout(fila_guardar)

    layout.addStretch(1)
    return page
