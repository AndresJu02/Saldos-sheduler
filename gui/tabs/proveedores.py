"""Pestaña "Proveedores": tabla de proveedores (habilitar/deshabilitar,
visible/oculto, orden) y formulario para agregar proveedores genéricos
por selectores web (sin escribir un .py a medida)."""
import re
import threading

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QDialog, QAbstractItemView,
    QTableWidgetItem, QListWidget as QtListWidget, QStackedWidget, QFrame,
)
from qfluentwidgets import (
    TableWidget, PrimaryPushButton, PushButton, TransparentPushButton, StrongBodyLabel,
    BodyLabel, CaptionLabel, LineEdit, ComboBox, FluentIcon, SubtitleLabel,
    TransparentToolButton,
)

from gui import dialogs as messagebox
from gui.dialogs import boton_ayuda
from gui.widgets import texto_informativo, columna, pagina_con_scroll, pintar_boton_peligro, alinear_boton_izquierda

from core.providers_registry import get_all_providers, sort_providers_by_order, get_custom_provider_names
from core.chrome_tools import ensure_chrome, ensure_chromedriver, get_robust_driver
from core.element_picker import elegir_elemento
from core.flow_provider import FlowWebProvider, TIPOS_PASO, describir_paso, migrar_definicion_antigua
from generic_provider import DEFAULT_BALANCE_REGEX


def _dialogo_base(root, titulo, ancho, alto, bg):
    win = QDialog(root)
    win.setWindowTitle(titulo)
    win.resize(ancho, alto)
    win.setStyleSheet(f"QDialog{{background-color:{bg};}}")
    return win


def build(ctx):
    root = ctx.root
    BG = ctx.BG
    ACCENT = ctx.ACCENT
    ENTRY_BG = ctx.ENTRY_BG
    FG = ctx.FG
    VERDE = ctx.VERDE
    ROJO = ctx.ROJO
    config = ctx.config
    save_config = ctx.save_config

    page, layout = pagina_con_scroll(BG)

    # Proveedores que SIEMPRE corren en modo visible por requerir intervención
    # visual obligatoria (captcha), y por lo tanto no aplica alternar la opción.
    PROVEEDORES_VISIBLE_FORZADO = {"SipMovil", "1980"}

    main_row = QHBoxLayout()

    tree = TableWidget()
    # Esquinas redondeadas de verdad (TableWidget trae estos dos métodos
    # dedicados para eso, ver qfluentwidgets.components.widgets.table_view)
    # -sin esto se ve con esquinas cuadradas, distinto al resto de la
    # interfaz (chips, tarjetas, etc, reportado 2026-10-28).
    tree.setBorderVisible(True)
    tree.setBorderRadius(8)
    tree.setColumnCount(3)
    tree.setHorizontalHeaderLabels(["ESTADO", "PROVEEDOR", "VISIBLE"])
    tree.verticalHeader().hide()
    tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    tree.setColumnWidth(0, 70)
    tree.setColumnWidth(2, 90)
    tree.horizontalHeader().setStretchLastSection(False)
    from PyQt6.QtWidgets import QHeaderView
    tree.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    main_row.addWidget(tree, 1)

    def refresh_tree(mantener_seleccion=None):
        tree.setRowCount(0)
        providers = get_all_providers()
        providers = sort_providers_by_order(providers, config)
        enabled = [str(e).strip() for e in config.get("enabled_providers", [])]
        visibles = [str(v).strip() for v in config.get("visible_providers", [])]
        tree.setRowCount(len(providers))
        for row, p in enumerate(providers):
            nombre_limpio = str(p.name).strip()
            habilitado = nombre_limpio in enabled
            if nombre_limpio in PROVEEDORES_VISIBLE_FORZADO:
                icono_visible = "👁 (fijo)"
            elif nombre_limpio in visibles:
                icono_visible = "👁"
            else:
                icono_visible = "—"

            item_estado = QTableWidgetItem("✓" if habilitado else "✗")
            item_estado.setForeground(QColor(VERDE if habilitado else ROJO))
            item_estado.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            tree.setItem(row, 0, item_estado)
            tree.setItem(row, 1, QTableWidgetItem(nombre_limpio))
            item_visible = QTableWidgetItem(icono_visible)
            item_visible.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            tree.setItem(row, 2, item_visible)
        tree.resizeRowsToContents()

        if mantener_seleccion:
            for row in range(tree.rowCount()):
                if tree.item(row, 1).text() in mantener_seleccion:
                    tree.selectRow(row)

    refresh_tree()

    def nombres_seleccionados():
        filas = sorted(set(idx.row() for idx in tree.selectionModel().selectedRows()))
        return [tree.item(r, 1).text() for r in filas]

    def nombre_seleccionado():
        nombres = nombres_seleccionados()
        return nombres[0] if nombres else None

    def configurar_proveedor():
        nombre = nombre_seleccionado()
        if not nombre:
            messagebox.showinfo("Seleccionar", "Seleccione un proveedor primero.")
            return
        providers = get_all_providers()
        provider = next((p for p in providers if str(p.name).strip() == nombre), None)
        if not provider:
            return

        win = _dialogo_base(root, f"Configurar {provider.name}", 440, min(max(140 + (len(provider.config_fields) + 3) * 52, 320), 600), BG)
        win_layout = QVBoxLayout(win)
        grid = QGridLayout()
        win_layout.addLayout(grid)

        entries = {}
        prov_cfg = config.get("providers_config", {}).get(provider.name, {})

        for idx, field in enumerate(provider.config_fields):
            grid.addWidget(StrongBodyLabel(field["label"]), idx, 0)
            edit = LineEdit()
            edit.setText(str(prov_cfg.get(field["key"], field.get("default", ""))))
            grid.addWidget(edit, idx, 1)
            entries[field["key"]] = edit

        row_offset = len(provider.config_fields)
        grid.addWidget(StrongBodyLabel("Fila (Sheet Row)"), row_offset, 0)
        edit_row = LineEdit()
        edit_row.setText(str(prov_cfg.get("sheet_row", provider.sheet_row)))
        grid.addWidget(edit_row, row_offset, 1)
        entries["sheet_row"] = edit_row

        grid.addWidget(StrongBodyLabel("Columna (Sheet Col)"), row_offset + 1, 0)
        edit_col = LineEdit()
        edit_col.setText(str(prov_cfg.get("sheet_col", provider.sheet_col)))
        grid.addWidget(edit_col, row_offset + 1, 1)
        entries["sheet_col"] = edit_col

        def guardar():
            new_cfg = {}
            for k, v in entries.items():
                val = v.text()
                if k in ("sheet_row", "sheet_col"):
                    try:
                        new_cfg[k] = int(val)
                    except ValueError:
                        new_cfg[k] = val
                else:
                    new_cfg[k] = val
            config.setdefault("providers_config", {})[provider.name] = new_cfg
            save_config(config)
            win.accept()

        btn_guardar = PrimaryPushButton(FluentIcon.SAVE, "Guardar")
        btn_guardar.clicked.connect(guardar)
        fila_btn = QHBoxLayout()
        fila_btn.addStretch(1)
        fila_btn.addWidget(btn_guardar)
        win_layout.addLayout(fila_btn)
        win.exec()

    def toggle_proveedor():
        nombres_sel = nombres_seleccionados()
        if not nombres_sel:
            messagebox.showinfo("Seleccionar", "Seleccione al menos un proveedor.")
            return

        enabled = [str(e).strip() for e in config.get("enabled_providers", [])]
        accion_habilitar = nombres_sel[0] not in enabled

        for nombre in nombres_sel:
            if accion_habilitar:
                if nombre not in enabled:
                    enabled.append(nombre)
            else:
                if nombre in enabled:
                    enabled.remove(nombre)

        config["enabled_providers"] = enabled
        save_config(config)
        refresh_tree(mantener_seleccion=nombres_sel)

    def toggle_visible():
        """Alterna el modo 'visible' (headless=False) para los proveedores
        seleccionados. No aplica a los que requieren captcha (SipMovil, 1980),
        ya que esos siempre corren visibles de forma fija."""
        nombres_sel = [n for n in nombres_seleccionados() if n not in PROVEEDORES_VISIBLE_FORZADO]
        if not nombres_seleccionados():
            messagebox.showinfo("Seleccionar", "Seleccione al menos un proveedor.")
            return
        if not nombres_sel:
            messagebox.showinfo(
                "No aplica",
                "Los proveedores seleccionados ya corren siempre en modo visible "
                "(requieren captcha) y no se pueden alternar."
            )
            return

        visibles = [str(v).strip() for v in config.get("visible_providers", [])]
        activar = nombres_sel[0] not in visibles

        for nombre in nombres_sel:
            if activar:
                if nombre not in visibles:
                    visibles.append(nombre)
            else:
                if nombre in visibles:
                    visibles.remove(nombre)

        config["visible_providers"] = visibles
        save_config(config)
        refresh_tree(mantener_seleccion=nombres_sel)

    def probar_ahora_seleccionado():
        """Corre el proveedor seleccionado (predeterminado o personalizado)
        una sola vez, en modo visible, para poder ver el paso a paso y
        diagnosticar visualmente el error."""
        nombre = nombre_seleccionado()
        if not nombre:
            messagebox.showinfo("Seleccionar", "Seleccione un proveedor primero.")
            return
        from pathlib import Path
        if not Path(config.get("credentials_path", "")).exists():
            messagebox.showerror("Error", "Configura primero las credenciales de Google (pestaña Configuración).")
            return

        provider = next((p for p in get_all_providers() if str(p.name).strip() == nombre), None)
        if not provider:
            return

        prov_cfg = config.get("providers_config", {}).get(provider.name, {})
        test_cfg = {}
        for field in provider.config_fields:
            key = field["key"]
            test_cfg[key] = prov_cfg.get(key, field.get("default", ""))

        btn_probar_sel.setEnabled(False)
        btn_probar_sel.setText("Probando...")

        def tarea():
            try:
                chrome_exe = ensure_chrome()
                chromedriver_exe = ensure_chromedriver()
                try:
                    ok, msg = provider.get_balance(
                        test_cfg, None, "",
                        driver_paths={"chrome_exe": chrome_exe, "chromedriver_exe": chromedriver_exe},
                        get_driver_fn=get_robust_driver,
                        headless=False
                    )
                except TypeError:
                    # Proveedor que aún no acepta 'headless' como parámetro
                    # (p. ej. SipMovil/1980, que ya corren visibles de forma fija).
                    ok, msg = provider.get_balance(
                        test_cfg, None, "",
                        driver_paths={"chrome_exe": chrome_exe, "chromedriver_exe": chromedriver_exe},
                        get_driver_fn=get_robust_driver
                    )
            except Exception as e:
                ok, msg = False, str(e)

            def mostrar():
                btn_probar_sel.setEnabled(True)
                btn_probar_sel.setText("Probar ahora (visible)")
                if ok:
                    messagebox.showinfo("Prueba exitosa", f"Saldo detectado: {msg}\n\n"
                                         "(No se escribió en la hoja, esto fue solo una prueba.)")
                else:
                    messagebox.showerror("Prueba fallida", f"No se pudo leer el saldo:\n{msg}")
            root.after(0, mostrar)

        threading.Thread(target=tarea, daemon=True).start()

    def mover_arriba():
        nombre = nombre_seleccionado()
        if not nombre:
            return
        order = [str(o).strip() for o in config.get("provider_order", [])]
        if nombre not in order:
            order.append(nombre)
            config["provider_order"] = order
            save_config(config)
            refresh_tree()
            order = [str(o).strip() for o in config.get("provider_order", [])]
        if nombre not in order:
            return
        idx = order.index(nombre)
        if idx > 0:
            order[idx], order[idx - 1] = order[idx - 1], order[idx]
            config["provider_order"] = order
            save_config(config)
            refresh_tree(mantener_seleccion=[nombre])

    def mover_abajo():
        nombre = nombre_seleccionado()
        if not nombre:
            return
        order = [str(o).strip() for o in config.get("provider_order", [])]
        if nombre not in order:
            order.append(nombre)
            config["provider_order"] = order
            save_config(config)
            refresh_tree()
            order = [str(o).strip() for o in config.get("provider_order", [])]
        if nombre not in order:
            return
        idx = order.index(nombre)
        if idx < len(order) - 1:
            order[idx], order[idx + 1] = order[idx + 1], order[idx]
            config["provider_order"] = order
            save_config(config)
            refresh_tree(mantener_seleccion=[nombre])

    # ====================================================================
    # Formulario de proveedor genérico (agregar / editar)
    # ====================================================================
    def abrir_formulario_proveedor(nombre_original=None):
        """Abre el formulario de proveedor genérico. Si nombre_original viene
        dado, precarga los datos existentes de ese proveedor y guarda como
        edición (en vez de crear uno nuevo)."""
        datos_existentes = {}
        if nombre_original:
            for c in config.get("custom_providers", []):
                if c.get("name") == nombre_original:
                    datos_existentes = dict(c)
                    break

        win = _dialogo_base(
            root, f"Editar proveedor: {nombre_original}" if nombre_original else "Agregar proveedor",
            780, 680, BG,
        )
        win_layout = QVBoxLayout(win)

        # --- Picker "Elegir en la página" (ver core/element_picker.py) ---
        # El navegador que abre se reutiliza entre los distintos botones
        # "🎯 Elegir" de este mismo diálogo -así el usuario puede iniciar
        # sesión de verdad entre un campo y otro (ej. entre elegir el botón
        # de enviar y elegir dónde está el saldo, que solo aparece ya
        # logueado)- y se cierra junto con la ventana.
        picker_estado = {"driver": None}

        def cerrar_picker():
            driver = picker_estado["driver"]
            if driver is not None:
                picker_estado["driver"] = None
                try:
                    driver.quit()
                except Exception:
                    pass

        win.finished.connect(lambda _r: cerrar_picker())

        vars_ = {}

        def add_field(grid, row, label, key, default="", ancho=300):
            valor = datos_existentes.get(key, default)
            grid.addWidget(StrongBodyLabel(label), row, 0)
            edit = LineEdit()
            edit.setFixedWidth(ancho)
            edit.setText(str(valor))
            grid.addWidget(edit, row, 1)
            vars_[key] = edit
            return edit

        grid_datos = QGridLayout()
        win_layout.addWidget(SubtitleLabel("Datos del sitio"))
        win_layout.addLayout(grid_datos)
        add_field(grid_datos, 0, "Nombre del proveedor *", "name")
        add_field(grid_datos, 1, "Usuario (por defecto)", "usuario_default")
        add_field(grid_datos, 2, "Contraseña (por defecto)", "password_default")
        add_field(grid_datos, 3, "Timeout de carga (segundos)", "timeout", default="30", ancho=80)

        win_layout.addWidget(SubtitleLabel("Ubicación en la hoja de cálculo"))
        grid_sheet = QGridLayout()
        win_layout.addLayout(grid_sheet)
        add_field(grid_sheet, 0, "Fila (Sheet Row)", "sheet_row", default="1", ancho=80)
        add_field(grid_sheet, 1, "Columna (Sheet Col)", "sheet_col", default="1", ancho=80)

        # ------------------------------------------------------------
        # Flujo: lista de pasos reordenable (Ir a URL, Escribir texto,
        # Clic, Presionar tecla, Esperar, Leer texto). Reemplaza a los 4
        # campos fijos de antes -si se está editando un proveedor viejo
        # que todavía usaba ese formato, se migra a pasos automáticamente
        # (ver migrar_definicion_antigua en core/flow_provider.py)-.
        # ------------------------------------------------------------
        if datos_existentes.get("steps"):
            pasos = [dict(p) for p in datos_existentes["steps"]]
        elif datos_existentes:
            pasos = migrar_definicion_antigua(datos_existentes)
        else:
            # Proveedor nuevo: se arranca con el esqueleto típico de un
            # login simple, listo para completar con "🎯 Elegir".
            pasos = [
                {"type": "goto", "url": ""},
                {"type": "type", "selector_type": "css", "selector": "", "valor": "{usuario}"},
                {"type": "type", "selector_type": "css", "selector": "", "valor": "{password}"},
                {"type": "key", "tecla": "ENTER", "selector_type": "", "selector": ""},
                {"type": "wait", "segundos": 1.5},
                {"type": "extract", "selector_type": "xpath", "selector": "",
                 "regex": DEFAULT_BALANCE_REGEX, "prefix": "", "suffix": "", "guardar_como": "saldo"},
            ]

        def obtener_url_inicial():
            for p in pasos:
                if p.get("type") == "goto" and (p.get("url") or "").strip():
                    return p["url"].strip()
            return None

        fila_titulo_flujo = QHBoxLayout()
        fila_titulo_flujo.addWidget(boton_ayuda(
            "Este flujo cubre sitios con login de usuario/contraseña, incluso con varias pantallas o "
            "pasos intermedios. Sitios con captcha todavía requieren un archivo .py a medida en la "
            "carpeta 'providers'.\n\n"
            "Tip: usa el botón \"🎯 Elegir\" de cada paso en vez de escribir el CSS/XPath a mano -abre "
            "el navegador, haz clic en el elemento indicado y se llena solo-. El navegador queda "
            "abierto entre un paso y otro, así que puedes iniciar sesión de verdad antes de elegir "
            "dónde está el saldo (que solo aparece ya logueado).",
            titulo="Flujo (pasos, en orden)",
        ))
        fila_titulo_flujo.addWidget(SubtitleLabel("Flujo (pasos, en orden)"))
        fila_titulo_flujo.addStretch(1)
        win_layout.addLayout(fila_titulo_flujo)

        lista_pasos = QtListWidget()
        lista_pasos.setFixedHeight(160)
        lista_pasos.setStyleSheet(
            f"QListWidget{{background-color:{ENTRY_BG}; color:{FG}; border:1px solid #585b70; border-radius:6px;}}"
            f"QListWidget::item:selected{{background-color:{ACCENT}; color:{BG};}}"
        )
        win_layout.addWidget(lista_pasos)

        def refrescar_lista_pasos(seleccionar=None):
            lista_pasos.clear()
            for p in pasos:
                lista_pasos.addItem(describir_paso(p))
            if seleccionar is not None and 0 <= seleccionar < len(pasos):
                lista_pasos.setCurrentRow(seleccionar)

        def indice_seleccionado():
            fila = lista_pasos.currentRow()
            return fila if fila >= 0 else None

        def agregar_paso():
            def on_guardar(nuevo):
                pasos.append(nuevo)
                refrescar_lista_pasos(len(pasos) - 1)
            abrir_editor_paso(win, None, on_guardar, picker_estado, obtener_url_inicial)

        def editar_paso_seleccionado(forzar_aviso=True):
            idx = indice_seleccionado()
            if idx is None:
                if forzar_aviso:
                    messagebox.showinfo("Seleccionar", "Selecciona un paso primero.")
                return

            def on_guardar(nuevo):
                pasos[idx] = nuevo
                refrescar_lista_pasos(idx)
            abrir_editor_paso(win, pasos[idx], on_guardar, picker_estado, obtener_url_inicial)

        def eliminar_paso():
            idx = indice_seleccionado()
            if idx is None:
                messagebox.showinfo("Seleccionar", "Selecciona un paso primero.")
                return
            pasos.pop(idx)
            refrescar_lista_pasos(min(idx, len(pasos) - 1))

        def mover_paso(delta):
            idx = indice_seleccionado()
            if idx is None:
                return
            nuevo_idx = idx + delta
            if not (0 <= nuevo_idx < len(pasos)):
                return
            pasos[idx], pasos[nuevo_idx] = pasos[nuevo_idx], pasos[idx]
            refrescar_lista_pasos(nuevo_idx)

        lista_pasos.itemDoubleClicked.connect(lambda _it: editar_paso_seleccionado(forzar_aviso=False))
        refrescar_lista_pasos()

        botones_pasos = QHBoxLayout()
        btn_agregar_paso = PushButton(FluentIcon.ADD, "Agregar paso")
        btn_agregar_paso.clicked.connect(agregar_paso)
        btn_editar_paso = PushButton(FluentIcon.EDIT, "Editar")
        btn_editar_paso.clicked.connect(lambda: editar_paso_seleccionado(True))
        btn_eliminar_paso = PushButton(FluentIcon.DELETE, "Eliminar")
        btn_eliminar_paso.clicked.connect(eliminar_paso)
        btn_subir_paso = PushButton(FluentIcon.UP, "")
        btn_subir_paso.setFixedWidth(40)
        btn_subir_paso.clicked.connect(lambda: mover_paso(-1))
        btn_bajar_paso = PushButton(FluentIcon.DOWN, "")
        btn_bajar_paso.setFixedWidth(40)
        btn_bajar_paso.clicked.connect(lambda: mover_paso(1))
        for b in (btn_agregar_paso, btn_editar_paso, btn_eliminar_paso, btn_subir_paso, btn_bajar_paso):
            botones_pasos.addWidget(b)
        botones_pasos.addStretch(1)
        win_layout.addLayout(botones_pasos)

        def leer_definicion():
            nombre = vars_["name"].text().strip()
            if not nombre:
                messagebox.showerror("Faltan datos", "El nombre es obligatorio.")
                return None
            if not pasos:
                messagebox.showerror("Faltan datos", "Agrega al menos un paso al flujo.")
                return None
            if not any(p.get("type") == "goto" and (p.get("url") or "").strip() for p in pasos):
                messagebox.showerror("Faltan datos", "El flujo necesita un paso \"Ir a URL\" con la URL del sitio.")
                return None
            if not any(p.get("type") == "extract" for p in pasos):
                messagebox.showerror("Faltan datos", "El flujo necesita al menos un paso \"Leer texto\" (el saldo).")
                return None
            try:
                sheet_row = int(vars_["sheet_row"].text().strip())
                sheet_col = int(vars_["sheet_col"].text().strip())
            except ValueError:
                messagebox.showerror("Error", "Fila y columna deben ser números.")
                return None

            return {
                "name": nombre,
                "steps": [dict(p) for p in pasos],
                "usuario_default": vars_["usuario_default"].text(),
                "password_default": vars_["password_default"].text(),
                "timeout": vars_["timeout"].text().strip() or "30",
                "sheet_row": sheet_row,
                "sheet_col": sheet_col,
            }

        def probar_ahora():
            definicion = leer_definicion()
            if not definicion:
                return
            from pathlib import Path
            if not Path(config.get("credentials_path", "")).exists():
                messagebox.showerror("Error", "Configura primero las credenciales de Google (pestaña Configuración).")
                return

            btn_probar.setEnabled(False)
            btn_probar.setText("Probando...")

            def tarea():
                try:
                    chrome_exe = ensure_chrome()
                    chromedriver_exe = ensure_chromedriver()
                    provider = FlowWebProvider(definicion)
                    test_cfg = {
                        "usuario": definicion.get("usuario_default", ""),
                        "password": definicion.get("password_default", ""),
                    }
                    ok, msg = provider.get_balance(
                        test_cfg, None, "",
                        driver_paths={"chrome_exe": chrome_exe, "chromedriver_exe": chromedriver_exe},
                        get_driver_fn=get_robust_driver,
                        headless=False
                    )
                except Exception as e:
                    ok, msg = False, str(e)

                def mostrar():
                    btn_probar.setEnabled(True)
                    btn_probar.setText("Probar ahora")
                    if ok:
                        messagebox.showinfo("Prueba exitosa", f"Saldo detectado: {msg}\n\n"
                                             "(No se escribió en la hoja, esto fue solo una prueba de conexión.)")
                    else:
                        messagebox.showerror("Prueba fallida", f"No se pudo leer el saldo:\n{msg}")
                root.after(0, mostrar)

            threading.Thread(target=tarea, daemon=True).start()

        def guardar_proveedor():
            definicion = leer_definicion()
            if not definicion:
                return
            existentes = config.setdefault("custom_providers", [])
            nombres_todos = {p.name for p in get_all_providers()}
            nombre_nuevo = definicion["name"]

            # Si estamos editando y el usuario NO cambió el nombre, se excluye
            # a sí mismo de la validación de duplicados; si SÍ lo cambió,
            # solo se excluye el nombre original.
            colision = nombre_nuevo in nombres_todos and nombre_nuevo != nombre_original
            if colision:
                messagebox.showerror("Nombre repetido", "Ya existe un proveedor con ese nombre.")
                return

            existentes[:] = [c for c in existentes
                              if c.get("name") not in (nombre_original, nombre_nuevo)]
            existentes.append(definicion)

            if nombre_original and nombre_original != nombre_nuevo:
                enabled = config.setdefault("enabled_providers", [])
                config["enabled_providers"] = [nombre_nuevo if n == nombre_original else n for n in enabled]

                order = config.setdefault("provider_order", [])
                config["provider_order"] = [nombre_nuevo if n == nombre_original else n for n in order]

                providers_cfg = config.setdefault("providers_config", {})
                if nombre_original in providers_cfg:
                    providers_cfg[nombre_nuevo] = providers_cfg.pop(nombre_original)

            save_config(config)

            enabled = config.setdefault("enabled_providers", [])
            if nombre_nuevo not in enabled:
                enabled.append(nombre_nuevo)
            order = config.setdefault("provider_order", [])
            if nombre_nuevo not in order:
                order.append(nombre_nuevo)
            save_config(config)

            accion = "actualizado" if nombre_original else "guardado y habilitado"
            messagebox.showinfo("Guardado", f"Proveedor '{nombre_nuevo}' {accion}.")
            win.accept()
            refresh_tree()

        btns = QHBoxLayout()
        btns.addWidget(boton_ayuda(
            "La prueba abre el navegador visible para que puedas ver qué hace paso a paso.\n"
            "Al guardar, la ejecución real siempre corre oculta, como los demás proveedores.",
            titulo="Probar / Guardar",
        ))
        btn_probar = PushButton(FluentIcon.PLAY, "Probar ahora")
        btn_probar.clicked.connect(probar_ahora)
        btns.addWidget(btn_probar)
        btns.addStretch(1)
        btn_guardar_prov = PrimaryPushButton(FluentIcon.SAVE, "Guardar")
        btn_guardar_prov.clicked.connect(guardar_proveedor)
        btns.addWidget(btn_guardar_prov)
        win_layout.addLayout(btns)

        win.exec()

    def elegir_en_pagina_generico(ventana, combo_tipo, edit_valor, mensaje, boton, obtener_url_inicial, picker_estado):
        """Usado por el editor de un paso (abrir_editor_paso, más abajo):
        abre (o reutiliza) un Chrome visible -partiendo de la URL del
        primer paso "Ir a URL" del flujo- y llena el combo de tipo de
        selector + el valor con lo que el usuario elija en la página."""
        url_inicial = obtener_url_inicial()
        if picker_estado["driver"] is None and not url_inicial:
            messagebox.showerror(
                "Falta la URL",
                "Agrega primero un paso \"Ir a URL\" (con la URL del sitio) más arriba en la lista, "
                "o elige otro elemento primero para que se abra el navegador.")
            return

        texto_original = boton.text()
        boton.setEnabled(False)
        boton.setText("⏳  Haz clic en el navegador...")

        def tarea():
            try:
                driver = picker_estado["driver"]
                if driver is None:
                    chrome_exe = ensure_chrome()
                    chromedriver_exe = ensure_chromedriver()
                    driver = get_robust_driver(chrome_exe, chromedriver_exe, headless=False)
                    driver.get(url_inicial)
                    picker_estado["driver"] = driver
                resultado = elegir_elemento(driver, mensaje)
                error = None
            except Exception as e:
                resultado, error = None, e

            def terminar():
                boton.setEnabled(True)
                boton.setText(texto_original)
                if error:
                    messagebox.showerror("Error", f"No se pudo elegir el elemento en el navegador:\n{error}")
                    return
                if resultado is None:
                    messagebox.showinfo("Sin selección",
                                         "No se detectó ningún clic (se agotó el tiempo de espera). Intenta de nuevo.")
                    return
                combo_tipo.setCurrentText(resultado["tipo"])
                edit_valor.setText(resultado["valor"])

            root.after(0, terminar)

        threading.Thread(target=tarea, daemon=True).start()

    def abrir_editor_paso(parent_win, paso_existente, on_guardar, picker_estado, obtener_url_inicial):
        """Diálogo para agregar/editar UN paso del flujo. Los campos que
        se muestran cambian según el "Tipo de paso" elegido arriba."""
        paso_actual = dict(paso_existente) if paso_existente else {}

        ed = _dialogo_base(parent_win, "Editar paso" if paso_existente else "Agregar paso", 480, 460, BG)
        cont = QVBoxLayout(ed)

        cont.addWidget(StrongBodyLabel("Tipo de paso:"))
        opciones_tipo = list(TIPOS_PASO.keys())
        etiquetas_tipo = [TIPOS_PASO[t]["label"] for t in opciones_tipo]
        combo_tipo = ComboBox()
        combo_tipo.addItems(etiquetas_tipo)
        combo_tipo.setCurrentText(TIPOS_PASO.get(paso_actual.get("type", "goto"), TIPOS_PASO["goto"])["label"])
        cont.addWidget(combo_tipo)

        campos_stack = QStackedWidget()
        cont.addWidget(campos_stack, 1)
        campo_vars = {}

        def fila_selector(etiqueta, prefijo, mensaje, ancho=260):
            """`prefijo` solo nombra las claves de `campo_vars` (para que
            los 4 grupos -type/click/key/extract- no se pisen entre sí,
            ya que todos existen a la vez dentro del mismo diálogo). El
            valor INICIAL siempre se lee de "selector"/"selector_type",
            que es como se guarda cualquier paso -sin importar su tipo-;
            si se leyera con el prefijo, reabrir un paso ya guardado
            para editarlo mostraría los campos vacíos."""
            contenedor = QWidget()
            v = QVBoxLayout(contenedor)
            v.setContentsMargins(0, 8, 0, 0)
            v.addWidget(StrongBodyLabel(etiqueta))
            fila = QHBoxLayout()
            combo = ComboBox()
            combo.addItems(["css", "id", "name", "xpath"])
            combo.setCurrentText(paso_actual.get("selector_type", "css"))
            combo.setFixedWidth(90)
            fila.addWidget(combo)
            edit = LineEdit()
            edit.setFixedWidth(ancho)
            edit.setText(paso_actual.get("selector", ""))
            fila.addWidget(edit)
            btn = PushButton(FluentIcon.VIEW, "Elegir")
            btn.clicked.connect(lambda: elegir_en_pagina_generico(ed, combo, edit, mensaje, btn, obtener_url_inicial, picker_estado))
            fila.addWidget(btn)
            v.addLayout(fila)
            campo_vars[f"{prefijo}_type"] = combo
            campo_vars[prefijo] = edit
            return contenedor

        # --- goto ---
        f_goto = QWidget()
        v_goto = QVBoxLayout(f_goto)
        v_goto.addWidget(StrongBodyLabel("URL:"))
        var_url = LineEdit()
        var_url.setText(paso_actual.get("url", ""))
        v_goto.addWidget(var_url)
        v_goto.addStretch(1)
        campos_stack.addWidget(f_goto)

        # --- type ---
        f_type = QWidget()
        v_type = QVBoxLayout(f_type)
        v_type.addWidget(fila_selector("Selector del campo:", "selector", "🎯 Haz clic en el campo donde escribir"))
        v_type.addWidget(StrongBodyLabel("Texto a escribir:"))
        var_valor_type = LineEdit()
        var_valor_type.setText(paso_actual.get("valor", ""))
        v_type.addWidget(var_valor_type)
        fila_ph = QHBoxLayout()
        fila_ph.addWidget(boton_ayuda(
            "{usuario} y {password} se reemplazan por lo configurado para este proveedor.",
            titulo="Variables del texto a escribir",
        ))
        btn_ph_user = PushButton("+ {usuario}")
        btn_ph_user.clicked.connect(lambda: var_valor_type.setText(var_valor_type.text() + "{usuario}"))
        btn_ph_pass = PushButton("+ {password}")
        btn_ph_pass.clicked.connect(lambda: var_valor_type.setText(var_valor_type.text() + "{password}"))
        fila_ph.addWidget(btn_ph_user)
        fila_ph.addWidget(btn_ph_pass)
        fila_ph.addStretch(1)
        v_type.addLayout(fila_ph)
        v_type.addStretch(1)
        campos_stack.addWidget(f_type)

        # --- click ---
        f_click = QWidget()
        v_click = QVBoxLayout(f_click)
        v_click.addWidget(fila_selector("Selector del elemento:", "selector_click", "🎯 Haz clic en el elemento que se debe clickear"))
        v_click.addStretch(1)
        campos_stack.addWidget(f_click)

        # --- key ---
        f_key = QWidget()
        v_key = QVBoxLayout(f_key)
        v_key.addWidget(StrongBodyLabel("Tecla:"))
        combo_tecla = ComboBox()
        combo_tecla.addItems(["ENTER", "TAB", "ESCAPE"])
        combo_tecla.setCurrentText(paso_actual.get("tecla", "ENTER"))
        combo_tecla.setFixedWidth(120)
        v_key.addWidget(combo_tecla)
        v_key.addWidget(fila_selector("Elemento (opcional; vacío = el que tenga el foco):", "selector_key", "🎯 Haz clic en el campo donde enviar la tecla"))
        v_key.addStretch(1)
        campos_stack.addWidget(f_key)

        # --- wait ---
        f_wait = QWidget()
        v_wait = QVBoxLayout(f_wait)
        v_wait.addWidget(StrongBodyLabel("Segundos a esperar:"))
        var_segundos = LineEdit()
        var_segundos.setFixedWidth(80)
        var_segundos.setText(str(paso_actual.get("segundos", "1")))
        v_wait.addWidget(var_segundos)
        v_wait.addStretch(1)
        campos_stack.addWidget(f_wait)

        # --- extract ---
        f_extract = QWidget()
        v_extract = QVBoxLayout(f_extract)
        v_extract.addWidget(fila_selector("Selector del texto a leer:", "selector_extract", "🎯 Haz clic en el número/texto que se debe leer", ancho=300))
        v_extract.addWidget(StrongBodyLabel("Regex de extracción (opcional):"))
        var_regex = LineEdit()
        var_regex.setText(paso_actual.get("regex", DEFAULT_BALANCE_REGEX))
        v_extract.addWidget(var_regex)
        fila_afijos = QHBoxLayout()
        fila_afijos.addWidget(BodyLabel("Prefijo:"))
        var_prefix = LineEdit()
        var_prefix.setFixedWidth(60)
        var_prefix.setText(paso_actual.get("prefix", ""))
        fila_afijos.addWidget(var_prefix)
        fila_afijos.addWidget(BodyLabel("Sufijo:"))
        var_suffix = LineEdit()
        var_suffix.setFixedWidth(60)
        var_suffix.setText(paso_actual.get("suffix", ""))
        fila_afijos.addWidget(var_suffix)
        fila_afijos.addStretch(1)
        v_extract.addLayout(fila_afijos)
        fila_guardar_como = QHBoxLayout()
        fila_guardar_como.addWidget(boton_ayuda(
            'Usa "saldo" salvo que necesites leer varios valores en el mismo flujo.',
            titulo="Guardar como",
        ))
        fila_guardar_como.addWidget(StrongBodyLabel("Guardar como:"))
        var_guardar_como = LineEdit()
        var_guardar_como.setFixedWidth(140)
        var_guardar_como.setText(paso_actual.get("guardar_como", "saldo"))
        fila_guardar_como.addWidget(var_guardar_como)
        fila_guardar_como.addStretch(1)
        v_extract.addLayout(fila_guardar_como)
        v_extract.addStretch(1)
        campos_stack.addWidget(f_extract)

        def on_tipo_cambiado(_idx=None):
            idx = etiquetas_tipo.index(combo_tipo.currentText())
            campos_stack.setCurrentIndex(idx)

        combo_tipo.currentIndexChanged.connect(on_tipo_cambiado)
        on_tipo_cambiado()

        def guardar_paso():
            idx = etiquetas_tipo.index(combo_tipo.currentText())
            tipo = opciones_tipo[idx]

            if tipo == "goto":
                url = var_url.text().strip()
                if not url:
                    messagebox.showerror("Faltan datos", "Indica la URL.")
                    return
                nuevo = {"type": "goto", "url": url}
            elif tipo == "type":
                selector = campo_vars["selector"].text().strip()
                if not selector:
                    messagebox.showerror("Faltan datos", "Indica el selector del campo (o elígelo en la página).")
                    return
                nuevo = {"type": "type", "selector_type": campo_vars["selector_type"].currentText(),
                         "selector": selector, "valor": var_valor_type.text()}
            elif tipo == "click":
                selector = campo_vars["selector_click"].text().strip()
                if not selector:
                    messagebox.showerror("Faltan datos", "Indica el selector del elemento (o elígelo en la página).")
                    return
                nuevo = {"type": "click", "selector_type": campo_vars["selector_click_type"].currentText(),
                         "selector": selector}
            elif tipo == "key":
                nuevo = {"type": "key", "tecla": combo_tecla.currentText(),
                         "selector_type": campo_vars["selector_key_type"].currentText(),
                         "selector": campo_vars["selector_key"].text().strip()}
            elif tipo == "wait":
                try:
                    segundos = float(var_segundos.text().strip())
                except ValueError:
                    messagebox.showerror("Error", "Los segundos deben ser un número.")
                    return
                nuevo = {"type": "wait", "segundos": segundos}
            elif tipo == "extract":
                selector = campo_vars["selector_extract"].text().strip()
                if not selector:
                    messagebox.showerror("Faltan datos",
                                          "Indica el selector del texto a leer (o elígelo en la página).")
                    return
                nuevo = {"type": "extract", "selector_type": campo_vars["selector_extract_type"].currentText(),
                         "selector": selector, "regex": var_regex.text().strip() or DEFAULT_BALANCE_REGEX,
                         "prefix": var_prefix.text(), "suffix": var_suffix.text(),
                         "guardar_como": var_guardar_como.text().strip() or "saldo"}
            else:
                return

            on_guardar(nuevo)
            ed.accept()

        btns_ed = QHBoxLayout()
        btn_cancelar_ed = PushButton("Cancelar")
        btn_cancelar_ed.clicked.connect(ed.reject)
        btn_guardar_ed = PrimaryPushButton(FluentIcon.SAVE, "Guardar paso")
        btn_guardar_ed.clicked.connect(guardar_paso)
        btns_ed.addStretch(1)
        btns_ed.addWidget(btn_cancelar_ed)
        btns_ed.addWidget(btn_guardar_ed)
        cont.addLayout(btns_ed)

        ed.exec()

    def agregar_proveedor():
        abrir_formulario_proveedor(nombre_original=None)

    def editar_proveedor():
        nombre = nombre_seleccionado()
        if not nombre:
            messagebox.showinfo("Seleccionar", "Seleccione un proveedor primero.")
            return
        custom_names = get_custom_provider_names(config)
        if nombre not in custom_names:
            messagebox.showwarning(
                "No editable aquí",
                "Solo los proveedores creados con 'Agregar proveedor' se pueden editar aquí.\n"
                "Para los proveedores incluidos en la aplicación, usa el botón 'Configurar' "
                "(usuario, contraseña, URL y ubicación en la hoja)."
            )
            return
        abrir_formulario_proveedor(nombre_original=nombre)

    def eliminar_proveedor():
        nombre = nombre_seleccionado()
        if not nombre:
            messagebox.showinfo("Seleccionar", "Seleccione un proveedor primero.")
            return
        custom_names = get_custom_provider_names(config)
        if nombre not in custom_names:
            messagebox.showwarning(
                "No permitido",
                "Solo se pueden eliminar proveedores creados con 'Agregar proveedor'.\n"
                "Los proveedores incluidos en la aplicación no se pueden borrar desde aquí."
            )
            return
        if not messagebox.askyesno("Confirmar", f"¿Eliminar el proveedor '{nombre}'? Esta acción no se puede deshacer."):
            return

        config["custom_providers"] = [c for c in config.get("custom_providers", []) if c.get("name") != nombre]
        config["enabled_providers"] = [n for n in config.get("enabled_providers", []) if n != nombre]
        config["provider_order"] = [n for n in config.get("provider_order", []) if n != nombre]
        config.get("providers_config", {}).pop(nombre, None)
        save_config(config)
        refresh_tree()

    btn_panel = QVBoxLayout()
    btn_panel.setSpacing(6)

    btn_configurar = PrimaryPushButton(FluentIcon.SETTING, "Configurar")
    btn_configurar.clicked.connect(configurar_proveedor)
    btn_toggle = PushButton(FluentIcon.SYNC, "Habilitar / Deshab.")
    btn_toggle.clicked.connect(toggle_proveedor)
    btn_toggle_visible = PushButton(FluentIcon.VIEW, "Visible / Oculto")
    btn_toggle_visible.clicked.connect(toggle_visible)
    btn_probar_sel = PushButton(FluentIcon.PLAY, "Probar ahora (visible)")
    btn_probar_sel.clicked.connect(probar_ahora_seleccionado)
    btn_agregar = TransparentPushButton(FluentIcon.ADD, "Agregar proveedor")
    btn_agregar.clicked.connect(agregar_proveedor)
    btn_editar = PushButton(FluentIcon.EDIT, "Editar proveedor")
    btn_editar.clicked.connect(editar_proveedor)
    btn_eliminar = PushButton(FluentIcon.DELETE, "Eliminar proveedor")
    pintar_boton_peligro(btn_eliminar)  # rojo, sin romper la posición del ícono -ver gui/widgets.py-
    btn_eliminar.clicked.connect(eliminar_proveedor)
    btn_subir = PushButton(FluentIcon.UP, "Subir")
    btn_subir.clicked.connect(mover_arriba)
    btn_bajar = PushButton(FluentIcon.DOWN, "Bajar")
    btn_bajar.clicked.connect(mover_abajo)

    # Texto+ícono alineados a la izquierda (no centrado) en los botones del
    # panel -reportado 2026-10-28-. "Eliminar proveedor" ya queda alineado
    # a través de pintar_boton_peligro() de arriba (ver gui/widgets.py).
    for b in (btn_configurar, btn_toggle, btn_toggle_visible, btn_probar_sel, btn_agregar, btn_editar, btn_subir, btn_bajar):
        alinear_boton_izquierda(b)

    for b in (btn_configurar, btn_toggle, btn_toggle_visible, btn_probar_sel):
        btn_panel.addWidget(b)
    separador1 = QFrame()
    separador1.setFrameShape(QFrame.Shape.HLine)
    separador1.setStyleSheet("color: #3a3a3c;")
    btn_panel.addWidget(separador1)
    for b in (btn_agregar, btn_editar, btn_eliminar):
        btn_panel.addWidget(b)
    btn_panel.addWidget(StrongBodyLabel("Orden:"))
    btn_panel.addWidget(btn_subir)
    btn_panel.addWidget(btn_bajar)
    btn_panel.addStretch(1)

    btn_panel_widget = QWidget()
    btn_panel_widget.setLayout(btn_panel)
    btn_panel_widget.setFixedWidth(220)
    main_row.addWidget(btn_panel_widget)

    main_row_widget = QWidget()
    main_row_widget.setLayout(main_row)
    # OJO: nada de setMinimumHeight() con un valor fijo acá -un 320 puesto a
    # ojo en una vuelta anterior terminó siendo MENOR que lo que el panel de
    # botones (9 botones + separador + "Orden:") realmente necesita
    # (~390-400px). Eso le mentía al layout padre sobre cuánto espacio
    # mínimo haría falta, así que cuando la página (ya con scroll, ver
    # pagina_con_scroll) le daba justo esos 320px, el QVBoxLayout de
    # btn_panel no tenía más remedio que apretar/recortar los botones
    # -reportado 2026-10-28 con captura mostrando los botones cortados al
    # expandir Horarios de ejecución-. Sin este mínimo fijo, Qt calcula solo
    # el mínimo real a partir del contenido de main_row (máximo entre la
    # tabla y btn_panel_widget), que SIEMPRE alcanza para los botones.
    layout.addWidget(main_row_widget)

    # ============================================================
    #  Horarios de ejecución
    # ============================================================
    CHIP_ALTO = 32
    # Ancho fijo de "Lunes a Viernes" / "Sábado" / etc -para que las 4 filas
    # (Lunes a Viernes, Sábado, Domingo, Tolerancia) queden con sus chips
    # arrancando en la misma columna, igual que en la imagen de referencia
    # del usuario (reportado 2026-10-28 como "no alineados").
    LABEL_ANCHO = 130

    def _fila_horario(etiqueta):
        """Fila tipo tabla: [Etiqueta de ancho fijo] [...contenido...], con
        una línea divisoria abajo -mismo patrón para las 4 filas de esta
        sección, calcado del diseño de referencia (imagen prueba,
        2026-10-28). Devuelve el QHBoxLayout donde el llamador agrega su
        contenido (chips, texto, lo que sea)."""
        fila_widget = QWidget()
        fila_v = QVBoxLayout(fila_widget)
        fila_v.setContentsMargins(0, 0, 0, 0)
        fila_v.setSpacing(10)

        fila_h = QHBoxLayout()
        fila_h.setContentsMargins(0, 0, 0, 0)
        fila_h.setSpacing(10)
        lbl = StrongBodyLabel(etiqueta)
        lbl.setFixedWidth(LABEL_ANCHO)
        fila_h.addWidget(lbl)
        fila_v.addLayout(fila_h)

        linea = QFrame()
        linea.setFixedHeight(1)
        linea.setStyleSheet("background-color: #3a3a3f; border: none;")
        fila_v.addWidget(linea)

        hor_content.addWidget(fila_widget)
        return fila_h

    def _crear_chip_horario(idx, times, redraw):
        """Un horario: caja con la hora editable + botón separado para
        quitarlo -dos piezas con un pequeño espacio entre sí, calcado de la
        imagen de referencia del usuario, en vez de un solo bloque fusionado
        con rojo sólido (reportado 2026-10-28 como "muy fuerte"). El ícono
        de reloj decorativo que tenía antes se sacó -al usuario no le gustó
        cómo se veía, mismo reporte-."""
        wrapper = QWidget()
        wrapper_layout = QHBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.setSpacing(6)

        caja = QFrame()
        caja.setFixedHeight(CHIP_ALTO)
        caja.setStyleSheet(
            f"QFrame{{background-color:{ENTRY_BG}; border:1px solid #585b70; border-radius:8px;}}"
        )
        caja_layout = QHBoxLayout(caja)
        caja_layout.setContentsMargins(12, 0, 12, 0)

        edit = LineEdit()
        edit.setFixedSize(46, CHIP_ALTO - 6)
        edit.setClearButtonEnabled(False)
        edit.setText(times[idx])
        edit.setStyleSheet("QLineEdit{border:none; background: transparent; padding: 0px; font-weight: 600;}")

        def on_edit(texto, idx=idx):
            times[idx] = texto.strip()
        edit.textChanged.connect(on_edit)
        caja_layout.addWidget(edit)
        wrapper_layout.addWidget(caja)

        def quitar(_checked=False, idx=idx):
            # "_checked" absorbe el bool que manda clicked(bool) -mismo
            # patrón que _activar en gui/app.py, reportado 2026-10-28-.
            del times[idx]
            redraw()

        # Fondo granate apagado (no el rojo sólido de ROJO -reportado "se ve
        # muy fuerte"-) con el ícono recoloreado a ROJO vía
        # FluentIcon.CLOSE.icon(color=...) -pasar un QIcon ya coloreado a
        # TransparentToolButton hace que drawIcon() lo pinte tal cual, en vez
        # de re-pintarlo con el color del tema-. TransparentToolButton (no
        # PushButton) porque centra su ícono según el ancho real del botón
        # en vez de minimumSizeHint() -lo que sí rompe a PushButton con
        # stylesheet propio, ver pintar_boton_peligro() en gui/widgets.py-,
        # así que tolera fondo + tamaño chico sin desplazar la X.
        # Más chico que el alto completo del chip (CHIP_ALTO) -pegado a los
        # bordes del chip se veía muy grande/pesado, reportado 2026-10-28-;
        # al quedar más chico que "caja", el QHBoxLayout de "wrapper" lo
        # centra solo verticalmente.
        ANCHO_BOTON_ELIMINAR = CHIP_ALTO - 6
        cerrar = TransparentToolButton(FluentIcon.CLOSE.icon(color=QColor(ROJO)))
        cerrar.setFixedSize(ANCHO_BOTON_ELIMINAR, ANCHO_BOTON_ELIMINAR)
        cerrar.setIconSize(QSize(11, 11))
        cerrar.setStyleSheet(
            "QToolButton{background-color:#4a2b2d; border:none; border-radius:7px;} "
            "QToolButton:hover{background-color:#5c3537;}"
        )
        cerrar.clicked.connect(quitar)
        wrapper_layout.addWidget(cerrar)

        return wrapper

    def _build_time_chips(fila_h, times):
        """Dibuja 'times' (lista mutable de strings 'HH:MM') como chips en
        fila dentro de 'fila_h', con un botón "+ Agregar horario" al final.
        'times' queda sincronizada en todo momento, así que al guardar solo
        hace falta leer esa lista."""
        chips_widget = QWidget()
        chips_layout = QHBoxLayout(chips_widget)
        chips_layout.setContentsMargins(0, 0, 0, 0)
        chips_layout.setSpacing(8)
        fila_h.addWidget(chips_widget)
        fila_h.addStretch(1)

        def redraw():
            while chips_layout.count():
                item = chips_layout.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.deleteLater()

            for idx in range(len(times)):
                chips_layout.addWidget(_crear_chip_horario(idx, times, redraw))

            def agregar():
                times.append("08:00")
                redraw()

            btn_agregar_horario = PushButton(FluentIcon.ADD, "Agregar horario")
            btn_agregar_horario.clicked.connect(agregar)
            chips_layout.addWidget(btn_agregar_horario)

        redraw()

    horarios_lv = [str(t).strip() for t in config.get("horarios_lun_vie", []) if str(t).strip()]
    horarios_sab = [str(t).strip() for t in config.get("horarios_sabado", []) if str(t).strip()]

    tol_edit = LineEdit()
    tol_edit.setFixedWidth(80)
    tol_edit.setText(str(config.get("tolerancia_min", 1.5)))

    def guardar_horarios():
        patron = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")
        lv_final = [t for t in horarios_lv if t]
        sab_final = [t for t in horarios_sab if t]
        for t in lv_final + sab_final:
            if not patron.match(t):
                messagebox.showerror("Error", f"'{t}' no es una hora válida (usa HH:MM, ej: 08:10).")
                return
        try:
            tolerancia = float(tol_edit.text())
        except ValueError:
            messagebox.showerror("Error", "La tolerancia debe ser un número.")
            return

        config["horarios_lun_vie"] = lv_final
        config["horarios_sabado"] = sab_final
        config["tolerancia_min"] = tolerancia
        save_config(config)
        messagebox.showinfo("Guardado", "Horarios actualizados.")

    hor_content_widget = QWidget()
    hor_content = QVBoxLayout(hor_content_widget)
    hor_content.setContentsMargins(0, 0, 0, 0)
    hor_content.setSpacing(10)

    fila_ayuda_horarios = QWidget()
    fila_ayuda_horarios_layout = QHBoxLayout(fila_ayuda_horarios)
    fila_ayuda_horarios_layout.setContentsMargins(0, 0, 0, 0)
    fila_ayuda_horarios_layout.setSpacing(8)
    fila_ayuda_horarios_layout.addWidget(boton_ayuda(
        "Momentos del día en que se consulta el saldo automáticamente. Usa el (+) para agregar otro horario.",
        titulo="Horarios de ejecución",
    ))
    fila_ayuda_horarios_layout.addWidget(texto_informativo("Momentos del día en que se consulta el saldo automáticamente."))
    fila_ayuda_horarios_layout.addStretch(1)
    hor_content.addWidget(fila_ayuda_horarios)

    fila_lv = _fila_horario("Lunes a Viernes")
    _build_time_chips(fila_lv, horarios_lv)

    fila_sab = _fila_horario("Sábado")
    _build_time_chips(fila_sab, horarios_sab)

    fila_dom = _fila_horario("Domingo")
    fila_dom.addWidget(texto_informativo("Sin ejecuciones"))
    fila_dom.addStretch(1)

    fila_tol = _fila_horario("Tolerancia (min)")
    fila_tol.addWidget(tol_edit)
    fila_tol.addWidget(boton_ayuda(
        "Minutos de margen permitidos entre el horario programado y la hora real de ejecución.",
        titulo="Tolerancia",
    ))
    fila_tol.addStretch(1)

    btn_guardar_horarios = PrimaryPushButton(FluentIcon.SAVE, "Guardar Horarios")
    btn_guardar_horarios.clicked.connect(guardar_horarios)
    fila_guardar_hor = QHBoxLayout()
    fila_guardar_hor.addStretch(1)
    fila_guardar_hor.addWidget(btn_guardar_horarios)
    hor_content.addLayout(fila_guardar_hor)

    hor_card = ctx.seccion_plegable(FluentIcon.STOP_WATCH, "Horarios de ejecución", hor_content_widget, "proveedores.horarios")
    layout.addWidget(hor_card)
    layout.addStretch(1)

    return page
