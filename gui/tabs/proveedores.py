"""Pestaña "Proveedores": tabla de proveedores (habilitar/deshabilitar,
visible/oculto, orden) y formulario para agregar proveedores genéricos
por selectores web (sin escribir un .py a medida)."""
import threading
import tkinter as tk
from tkinter import ttk
from pathlib import Path

import re

from gui import dialogs as messagebox
from gui.dialogs import boton_ayuda
from gui.collapsible import crear_seccion_plegable

from core.providers_registry import get_all_providers, sort_providers_by_order, get_custom_provider_names
from core.chrome_tools import ensure_chrome, ensure_chromedriver, get_robust_driver
from core.element_picker import elegir_elemento
from core.flow_provider import FlowWebProvider, TIPOS_PASO, describir_paso, migrar_definicion_antigua
from generic_provider import DEFAULT_BALANCE_REGEX


def build(notebook, ctx):
    root = ctx.root
    BG = ctx.BG
    ACCENT = ctx.ACCENT
    ENTRY_BG = ctx.ENTRY_BG
    FG = ctx.FG
    config = ctx.config
    save_config = ctx.save_config

    tab_prov = ttk.Frame(notebook)
    notebook.add(tab_prov, text="   Proveedores   ")

    # Toda la pestaña (tabla de proveedores + horarios de ejecución al
    # final) vive dentro de un canvas con scroll vertical, igual que en
    # Cargas VOIP: así "Horarios" deja de ser una pestaña aparte y basta
    # con bajar el scroll para verlo y editarlo.
    prov_canvas = tk.Canvas(tab_prov, bg=BG, highlightthickness=0)
    prov_vscroll = ttk.Scrollbar(tab_prov, orient="vertical", command=prov_canvas.yview)
    prov_content = ttk.Frame(prov_canvas)
    prov_content_window = prov_canvas.create_window((0, 0), window=prov_content, anchor="nw")
    prov_content.bind("<Configure>", lambda e: prov_canvas.configure(scrollregion=prov_canvas.bbox("all")))
    prov_canvas.bind("<Configure>", lambda e: prov_canvas.itemconfigure(prov_content_window, width=e.width))
    prov_canvas.configure(yscrollcommand=prov_vscroll.set)
    prov_canvas.pack(side="left", fill="both", expand=True)
    prov_vscroll.pack(side="right", fill="y")

    def _prov_on_mousewheel(event):
        prov_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _prov_bind_mousewheel(_event=None):
        prov_canvas.bind_all("<MouseWheel>", _prov_on_mousewheel)

    def _prov_unbind_mousewheel(_event=None):
        prov_canvas.unbind_all("<MouseWheel>")

    prov_canvas.bind("<Enter>", _prov_bind_mousewheel)
    prov_canvas.bind("<Leave>", _prov_unbind_mousewheel)

    main_frame = ttk.Frame(prov_content)
    main_frame.pack(fill="x", padx=5, pady=5)

    # Proveedores que SIEMPRE corren en modo visible por requerir intervención
    # visual obligatoria (captcha), y por lo tanto no aplica alternar la opción.
    PROVEEDORES_VISIBLE_FORZADO = {"SipMovil", "1980"}

    columns = ("Proveedor", "Visible")
    tree = ttk.Treeview(main_frame, columns=columns, show="tree headings", selectmode="extended")
    tree.heading("#0", text="Estado")
    tree.heading("Proveedor", text="Proveedor")
    tree.heading("Visible", text="Visible")
    tree.column("#0", width=60, anchor="center")
    tree.column("Proveedor", width=230, anchor="w")
    tree.column("Visible", width=70, anchor="center")
    # Sin scrollbar propia: quedaba duplicada con la de la pestaña (la que
    # baja hasta "Horarios de ejecución"). La tabla siempre muestra todos
    # los proveedores -su alto se ajusta a la cantidad real en refresh_tree-
    # y si algún día no cupieran todos en la ventana, se usa ese único
    # scroll general de la pestaña.
    tree.pack(side="left", fill="x", expand=True)

    btn_panel = ttk.Frame(main_frame)
    btn_panel.pack(side="right", fill="y", padx=(15, 5))

    def refresh_tree():
        tree.delete(*tree.get_children())
        providers = get_all_providers()
        providers = sort_providers_by_order(providers, config)
        tree.configure(height=max(len(providers), 1))
        enabled = [str(e).strip() for e in config.get("enabled_providers", [])]
        visibles = [str(v).strip() for v in config.get("visible_providers", [])]
        for p in providers:
            nombre_limpio = str(p.name).strip()
            icon = "✓" if nombre_limpio in enabled else "✗"
            if nombre_limpio in PROVEEDORES_VISIBLE_FORZADO:
                icono_visible = "👁 (fijo)"
            elif nombre_limpio in visibles:
                icono_visible = "👁"
            else:
                icono_visible = "—"
            tree.insert("", "end", text=icon, values=(nombre_limpio, icono_visible))

    refresh_tree()

    def configurar_proveedor():
        selecciones = tree.selection()
        if not selecciones:
            messagebox.showinfo("Seleccionar", "Seleccione un proveedor primero.")
            return
        sel = selecciones[0]
        item = tree.item(sel)
        nombre = str(item["values"][0]).strip()
        providers = get_all_providers()
        provider = next((p for p in providers if str(p.name).strip() == nombre), None)
        if not provider:
            return

        win = tk.Toplevel(root)
        win.title(f"Configurar {provider.name}")
        alto = 140 + (len(provider.config_fields) + 3) * 48
        win.geometry(f"420x{min(max(alto, 300), 560)}")
        win.configure(bg=BG)
        win.transient(root)
        win.grab_set()
        entries = {}
        prov_cfg = config.get("providers_config", {}).get(provider.name, {})

        for idx, field in enumerate(provider.config_fields):
            ttk.Label(win, text=field["label"], font=("Segoe UI", 10)).grid(row=idx, column=0, sticky="w", padx=15, pady=8)
            var = tk.StringVar(value=prov_cfg.get(field["key"], field.get("default", "")))
            ancho = 38 if field["key"] == "url" else 30
            ttk.Entry(win, textvariable=var, width=ancho).grid(row=idx, column=1, padx=15, pady=8)
            entries[field["key"]] = var

        row_offset = len(provider.config_fields)
        ttk.Label(win, text="Fila (Sheet Row)", font=("Segoe UI", 10)).grid(row=row_offset, column=0, sticky="w", padx=15, pady=8)
        var_row = tk.StringVar(value=str(prov_cfg.get("sheet_row", provider.sheet_row)))
        ttk.Entry(win, textvariable=var_row, width=10).grid(row=row_offset, column=1, padx=15, pady=8, sticky="w")
        entries["sheet_row"] = var_row

        ttk.Label(win, text="Columna (Sheet Col)", font=("Segoe UI", 10)).grid(row=row_offset+1, column=0, sticky="w", padx=15, pady=8)
        var_col = tk.StringVar(value=str(prov_cfg.get("sheet_col", provider.sheet_col)))
        ttk.Entry(win, textvariable=var_col, width=10).grid(row=row_offset+1, column=1, padx=15, pady=8, sticky="w")
        entries["sheet_col"] = var_col

        def guardar():
            new_cfg = {}
            for k, v in entries.items():
                val = v.get()
                if k in ("sheet_row", "sheet_col"):
                    try:
                        new_cfg[k] = int(val)
                    except ValueError:
                        new_cfg[k] = val
                else:
                    new_cfg[k] = val
            config.setdefault("providers_config", {})[provider.name] = new_cfg
            save_config(config)
            win.destroy()

        ttk.Button(win, text="Guardar", command=guardar, style="Accent.TButton").grid(
            row=row_offset+2, column=1, pady=20, sticky="e", padx=15
        )

    def toggle_proveedor():
        selecciones = tree.selection()
        if not selecciones:
            messagebox.showinfo("Seleccionar", "Seleccione al menos un proveedor.")
            return

        nombres_seleccionados = []
        for sel in selecciones:
            try:
                item = tree.item(sel)
                nombre = str(item["values"][0]).strip()
                nombres_seleccionados.append(nombre)
            except Exception:
                continue

        if not nombres_seleccionados:
            return

        enabled = [str(e).strip() for e in config.get("enabled_providers", [])]
        accion_habilitar = nombres_seleccionados[0] not in enabled

        for nombre in nombres_seleccionados:
            if accion_habilitar:
                if nombre not in enabled:
                    enabled.append(nombre)
            else:
                if nombre in enabled:
                    enabled.remove(nombre)

        config["enabled_providers"] = enabled
        save_config(config)
        refresh_tree()

        for nombre in nombres_seleccionados:
            for child in tree.get_children():
                if str(tree.item(child)["values"][0]).strip() == nombre:
                    tree.selection_add(child)

    def toggle_visible():
        """Alterna el modo 'visible' (headless=False) para los proveedores
        seleccionados. No aplica a los que requieren captcha (SipMovil, 1980),
        ya que esos siempre corren visibles de forma fija."""
        selecciones = tree.selection()
        if not selecciones:
            messagebox.showinfo("Seleccionar", "Seleccione al menos un proveedor.")
            return

        nombres_seleccionados = []
        for sel in selecciones:
            nombre = str(tree.item(sel)["values"][0]).strip()
            if nombre in PROVEEDORES_VISIBLE_FORZADO:
                continue
            nombres_seleccionados.append(nombre)

        if not nombres_seleccionados:
            messagebox.showinfo(
                "No aplica",
                "Los proveedores seleccionados ya corren siempre en modo visible "
                "(requieren captcha) y no se pueden alternar."
            )
            return

        visibles = [str(v).strip() for v in config.get("visible_providers", [])]
        activar = nombres_seleccionados[0] not in visibles

        for nombre in nombres_seleccionados:
            if activar:
                if nombre not in visibles:
                    visibles.append(nombre)
            else:
                if nombre in visibles:
                    visibles.remove(nombre)

        config["visible_providers"] = visibles
        save_config(config)
        refresh_tree()

        for nombre in nombres_seleccionados:
            for child in tree.get_children():
                if str(tree.item(child)["values"][0]).strip() == nombre:
                    tree.selection_add(child)

    def probar_ahora_seleccionado():
        """Corre el proveedor seleccionado (predeterminado o personalizado)
        una sola vez, en modo visible, para poder ver el paso a paso y
        diagnosticar visualmente el error."""
        selecciones = tree.selection()
        if not selecciones:
            messagebox.showinfo("Seleccionar", "Seleccione un proveedor primero.")
            return
        if not Path(config.get("credentials_path", "")).exists():
            messagebox.showerror("Error", "Configura primero las credenciales de Google (pestaña Configuración).")
            return

        nombre = str(tree.item(selecciones[0])["values"][0]).strip()
        provider = next((p for p in get_all_providers() if str(p.name).strip() == nombre), None)
        if not provider:
            return

        prov_cfg = config.get("providers_config", {}).get(provider.name, {})
        test_cfg = {}
        for field in provider.config_fields:
            key = field["key"]
            test_cfg[key] = prov_cfg.get(key, field.get("default", ""))

        btn_probar_sel.config(state="disabled", text="Probando...")
        root.update_idletasks()

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
                btn_probar_sel.config(state="normal", text="🧪  Probar ahora (visible)")
                if ok:
                    messagebox.showinfo("Prueba exitosa", f"Saldo detectado: {msg}\n\n"
                                         "(No se escribió en la hoja, esto fue solo una prueba.)")
                else:
                    messagebox.showerror("Prueba fallida", f"No se pudo leer el saldo:\n{msg}")
            root.after(0, mostrar)

        threading.Thread(target=tarea, daemon=True).start()

    def mover_arriba():
        selecciones = tree.selection()
        if not selecciones:
            return
        sel = selecciones[0]
        item = tree.item(sel)
        nombre = str(item["values"][0]).strip()
        order = config.get("provider_order", [])
        order = [str(o).strip() for o in order]

        if nombre not in order:
            order.append(nombre)
            config["provider_order"] = order
            save_config(config)
            refresh_tree()
            order = config.get("provider_order", [])
            order = [str(o).strip() for o in order]

        if nombre not in order:
            return

        idx = order.index(nombre)
        if idx > 0:
            order[idx], order[idx-1] = order[idx-1], order[idx]
            config["provider_order"] = order
            save_config(config)
            refresh_tree()
            for child in tree.get_children():
                if str(tree.item(child)["values"][0]).strip() == nombre:
                    tree.selection_set(child)
                    tree.focus(child)
                    break

    def mover_abajo():
        selecciones = tree.selection()
        if not selecciones:
            return
        sel = selecciones[0]
        item = tree.item(sel)
        nombre = str(item["values"][0]).strip()
        order = config.get("provider_order", [])
        order = [str(o).strip() for o in order]

        if nombre not in order:
            order.append(nombre)
            config["provider_order"] = order
            save_config(config)
            refresh_tree()
            order = config.get("provider_order", [])
            order = [str(o).strip() for o in order]

        if nombre not in order:
            return

        idx = order.index(nombre)
        if idx < len(order) - 1:
            order[idx], order[idx+1] = order[idx+1], order[idx]
            config["provider_order"] = order
            save_config(config)
            refresh_tree()
            for child in tree.get_children():
                if str(tree.item(child)["values"][0]).strip() == nombre:
                    tree.selection_set(child)
                    tree.focus(child)
                    break

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

        win = tk.Toplevel(root)
        win.title(f"Editar proveedor: {nombre_original}" if nombre_original else "Agregar proveedor")
        win.geometry("760x640")
        win.configure(bg=BG)
        win.transient(root)
        win.grab_set()

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

        def cerrar_ventana():
            cerrar_picker()
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", cerrar_ventana)

        canvas = tk.Canvas(win, bg=BG, highlightthickness=0)
        vscroll = ttk.Scrollbar(win, orient="vertical", command=canvas.yview)
        form = ttk.Frame(canvas)
        form_window = canvas.create_window((0, 0), window=form, anchor="nw")
        form.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        # El frame interno debe ocupar todo el ancho del canvas (para que se
        # vea bien al redimensionar la ventana).
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(form_window, width=e.width))
        canvas.configure(yscrollcommand=vscroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        vscroll.pack(side="right", fill="y")

        # --- Scroll con la rueda del mouse (Windows: <MouseWheel>) ---
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def _bind_mousewheel(_event=None):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)

        def _unbind_mousewheel(_event=None):
            canvas.unbind_all("<MouseWheel>")

        canvas.bind("<Enter>", _bind_mousewheel)
        canvas.bind("<Leave>", _unbind_mousewheel)
        win.bind("<Destroy>", lambda e: _unbind_mousewheel())

        vars_ = {}
        row = [0]

        def add_field(label, key, default="", width=40):
            valor = datos_existentes.get(key, default)
            ttk.Label(form, text=label, font=("Segoe UI", 10)).grid(
                row=row[0], column=0, sticky="w", padx=15, pady=6)
            var = tk.StringVar(value=valor)
            ttk.Entry(form, textvariable=var, width=width).grid(
                row=row[0], column=1, padx=15, pady=6, sticky="w")
            vars_[key] = var
            row[0] += 1
            return var

        ttk.Label(form, text="Datos del sitio", font=("Segoe UI", 11, "bold"),
                  foreground=ACCENT).grid(row=row[0], column=0, columnspan=2, sticky="w", padx=15, pady=(10, 4))
        row[0] += 1

        add_field("Nombre del proveedor *", "name")
        add_field("Usuario (por defecto)", "usuario_default")
        add_field("Contraseña (por defecto)", "password_default")
        add_field("Timeout de carga (segundos)", "timeout", default="30", width=10)

        ttk.Label(form, text="Ubicación en la hoja de cálculo", font=("Segoe UI", 11, "bold"),
                  foreground=ACCENT).grid(row=row[0], column=0, columnspan=2, sticky="w", padx=15, pady=(14, 4))
        row[0] += 1
        add_field("Fila (Sheet Row)", "sheet_row", default="1", width=10)
        add_field("Columna (Sheet Col)", "sheet_col", default="1", width=10)

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

        fila_titulo_flujo = ttk.Frame(form)
        fila_titulo_flujo.grid(row=row[0], column=0, columnspan=3, sticky="w", padx=15, pady=(14, 4))
        row[0] += 1
        boton_ayuda(
            fila_titulo_flujo,
            "Este flujo cubre sitios con login de usuario/contraseña, incluso con varias pantallas o "
            "pasos intermedios. Sitios con captcha todavía requieren un archivo .py a medida en la "
            "carpeta 'providers'.\n\n"
            "Tip: usa el botón \"🎯 Elegir\" de cada paso en vez de escribir el CSS/XPath a mano -abre "
            "el navegador, haz clic en el elemento indicado y se llena solo-. El navegador queda "
            "abierto entre un paso y otro, así que puedes iniciar sesión de verdad antes de elegir "
            "dónde está el saldo (que solo aparece ya logueado).",
            titulo="Flujo (pasos, en orden)",
        ).pack(side="left", padx=(0, 6))
        ttk.Label(fila_titulo_flujo, text="Flujo (pasos, en orden)", font=("Segoe UI", 11, "bold"),
                  foreground=ACCENT).pack(side="left")

        lista_frame = ttk.Frame(form)
        lista_frame.grid(row=row[0], column=0, columnspan=3, sticky="ew", padx=15, pady=(0, 4))
        row[0] += 1

        lista_pasos = tk.Listbox(lista_frame, height=8, exportselection=False,
                                  bg=ENTRY_BG, fg=FG, selectbackground=ACCENT, selectforeground=BG,
                                  highlightthickness=1, highlightbackground="#585b70",
                                  highlightcolor="#585b70", relief="flat", bd=0,
                                  activestyle="none", font=("Segoe UI", 10))
        lista_pasos.pack(fill="both", expand=True)

        def refrescar_lista_pasos(seleccionar=None):
            lista_pasos.delete(0, tk.END)
            for p in pasos:
                lista_pasos.insert(tk.END, describir_paso(p))
            if seleccionar is not None and 0 <= seleccionar < len(pasos):
                lista_pasos.selection_set(seleccionar)
                lista_pasos.see(seleccionar)

        def indice_seleccionado():
            sel = lista_pasos.curselection()
            return sel[0] if sel else None

        def agregar_paso():
            def on_guardar(nuevo):
                pasos.append(nuevo)
                refrescar_lista_pasos(len(pasos) - 1)
            abrir_editor_paso(None, on_guardar)

        def editar_paso_seleccionado(event=None):
            idx = indice_seleccionado()
            if idx is None:
                if event is None:  # solo avisar si vino del botón, no de un doble clic al aire
                    messagebox.showinfo("Seleccionar", "Selecciona un paso primero.")
                return

            def on_guardar(nuevo):
                pasos[idx] = nuevo
                refrescar_lista_pasos(idx)
            abrir_editor_paso(pasos[idx], on_guardar)

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

        lista_pasos.bind("<Double-Button-1>", editar_paso_seleccionado)
        refrescar_lista_pasos()

        botones_pasos = ttk.Frame(form)
        botones_pasos.grid(row=row[0], column=0, columnspan=3, sticky="w", padx=15, pady=(0, 4))
        row[0] += 1
        ttk.Button(botones_pasos, text="➕  Agregar paso", command=agregar_paso).pack(side="left")
        ttk.Button(botones_pasos, text="✏️  Editar", command=editar_paso_seleccionado).pack(side="left", padx=(6, 0))
        ttk.Button(botones_pasos, text="🗑️  Eliminar", command=eliminar_paso).pack(side="left", padx=(6, 0))
        ttk.Button(botones_pasos, text="↑", width=3, command=lambda: mover_paso(-1)).pack(side="left", padx=(12, 0))
        ttk.Button(botones_pasos, text="↓", width=3, command=lambda: mover_paso(1)).pack(side="left", padx=(4, 0))

        def elegir_en_pagina_generico(ventana, combo_tipo, var_valor, mensaje, boton):
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

            texto_original = boton.cget("text")
            boton.config(state="disabled", text="⏳  Haz clic en el navegador...")
            ventana.update_idletasks()

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
                    if not boton.winfo_exists():
                        return  # el editor de paso se cerró mientras se esperaba el clic
                    boton.config(state="normal", text=texto_original)
                    if error:
                        messagebox.showerror("Error", f"No se pudo elegir el elemento en el navegador:\n{error}")
                        return
                    if resultado is None:
                        messagebox.showinfo("Sin selección",
                                             "No se detectó ningún clic (se agotó el tiempo de espera). Intenta de nuevo.")
                        return
                    combo_tipo.set(resultado["tipo"])
                    var_valor.set(resultado["valor"])

                ventana.after(0, terminar)

            threading.Thread(target=tarea, daemon=True).start()

        def abrir_editor_paso(paso_existente, on_guardar):
            """Diálogo para agregar/editar UN paso del flujo. Los campos que
            se muestran cambian según el "Tipo de paso" elegido arriba."""
            paso_actual = dict(paso_existente) if paso_existente else {}

            ed = tk.Toplevel(win)
            ed.title("Editar paso" if paso_existente else "Agregar paso")
            ed.configure(bg=BG)
            ed.transient(win)
            ed.grab_set()
            ed.geometry("480x420")
            ed.resizable(False, False)

            cont = ttk.Frame(ed, padding=15)
            cont.pack(fill="both", expand=True)

            ttk.Label(cont, text="Tipo de paso:", font=("Segoe UI", 10, "bold")).pack(anchor="w")
            opciones_tipo = list(TIPOS_PASO.keys())
            etiquetas_tipo = [TIPOS_PASO[t]["label"] for t in opciones_tipo]
            combo_tipo = ttk.Combobox(cont, state="readonly", values=etiquetas_tipo, width=28)
            combo_tipo.set(TIPOS_PASO.get(paso_actual.get("type", "goto"), TIPOS_PASO["goto"])["label"])
            combo_tipo.pack(anchor="w", pady=(2, 12))

            campos_frame = ttk.Frame(cont)
            campos_frame.pack(fill="both", expand=True)
            campo_vars = {}

            def fila_selector(parent, etiqueta, prefijo, mensaje, ancho=30):
                """`prefijo` solo nombra las claves de `campo_vars` (para que
                los 4 grupos -type/click/key/extract- no se pisen entre sí,
                ya que todos existen a la vez dentro del mismo diálogo). El
                valor INICIAL siempre se lee de "selector"/"selector_type",
                que es como se guarda cualquier paso -sin importar su tipo-;
                si se leyera con el prefijo, reabrir un paso ya guardado
                para editarlo mostraría los campos vacíos."""
                ttk.Label(parent, text=etiqueta, font=("Segoe UI", 10)).pack(anchor="w", pady=(8, 2))
                fila = ttk.Frame(parent)
                fila.pack(fill="x")
                combo = ttk.Combobox(fila, values=["css", "id", "name", "xpath"], state="readonly", width=8)
                combo.set(paso_actual.get("selector_type", "css"))
                combo.pack(side="left")
                var_valor = tk.StringVar(value=paso_actual.get("selector", ""))
                entry = ttk.Entry(fila, textvariable=var_valor, width=ancho)
                entry.pack(side="left", padx=(6, 6))
                btn = ttk.Button(fila, text="🎯 Elegir")
                btn.pack(side="left")
                btn.config(command=lambda: elegir_en_pagina_generico(ed, combo, var_valor, mensaje, btn))
                campo_vars[f"{prefijo}_type"] = combo
                campo_vars[prefijo] = var_valor

            # --- goto ---
            f_goto = ttk.Frame(campos_frame)
            ttk.Label(f_goto, text="URL:", font=("Segoe UI", 10)).pack(anchor="w", pady=(0, 2))
            var_url = tk.StringVar(value=paso_actual.get("url", ""))
            ttk.Entry(f_goto, textvariable=var_url, width=48).pack(anchor="w", fill="x")

            # --- type ---
            f_type = ttk.Frame(campos_frame)
            fila_selector(f_type, "Selector del campo:", "selector", "🎯 Haz clic en el campo donde escribir")
            ttk.Label(f_type, text="Texto a escribir:", font=("Segoe UI", 10)).pack(anchor="w", pady=(10, 2))
            var_valor_type = tk.StringVar(value=paso_actual.get("valor", ""))
            ttk.Entry(f_type, textvariable=var_valor_type, width=40).pack(anchor="w", fill="x")
            fila_ph = ttk.Frame(f_type)
            fila_ph.pack(anchor="w", pady=(4, 0))
            boton_ayuda(
                fila_ph,
                "{usuario} y {password} se reemplazan por lo configurado para este proveedor.",
                titulo="Variables del texto a escribir",
            ).pack(side="left", padx=(0, 6))
            ttk.Button(fila_ph, text="+ {usuario}",
                       command=lambda: var_valor_type.set(var_valor_type.get() + "{usuario}")).pack(side="left")
            ttk.Button(fila_ph, text="+ {password}",
                       command=lambda: var_valor_type.set(var_valor_type.get() + "{password}")).pack(
                side="left", padx=(6, 0))

            # --- click ---
            f_click = ttk.Frame(campos_frame)
            fila_selector(f_click, "Selector del elemento:", "selector_click",
                          "🎯 Haz clic en el elemento que se debe clickear")

            # --- key ---
            f_key = ttk.Frame(campos_frame)
            ttk.Label(f_key, text="Tecla:", font=("Segoe UI", 10)).pack(anchor="w", pady=(0, 2))
            var_tecla = tk.StringVar(value=paso_actual.get("tecla", "ENTER"))
            ttk.Combobox(f_key, textvariable=var_tecla, values=["ENTER", "TAB", "ESCAPE"],
                         state="readonly", width=12).pack(anchor="w")
            fila_selector(f_key, "Elemento (opcional; vacío = el que tenga el foco):", "selector_key",
                          "🎯 Haz clic en el campo donde enviar la tecla")

            # --- wait ---
            f_wait = ttk.Frame(campos_frame)
            ttk.Label(f_wait, text="Segundos a esperar:", font=("Segoe UI", 10)).pack(anchor="w", pady=(0, 2))
            var_segundos = tk.StringVar(value=str(paso_actual.get("segundos", "1")))
            ttk.Entry(f_wait, textvariable=var_segundos, width=10).pack(anchor="w")

            # --- extract ---
            f_extract = ttk.Frame(campos_frame)
            fila_selector(f_extract, "Selector del texto a leer:", "selector_extract",
                          "🎯 Haz clic en el número/texto que se debe leer", ancho=38)
            ttk.Label(f_extract, text="Regex de extracción (opcional):", font=("Segoe UI", 10)).pack(
                anchor="w", pady=(10, 2))
            var_regex = tk.StringVar(value=paso_actual.get("regex", DEFAULT_BALANCE_REGEX))
            ttk.Entry(f_extract, textvariable=var_regex, width=40).pack(anchor="w", fill="x")
            fila_afijos = ttk.Frame(f_extract)
            fila_afijos.pack(fill="x", pady=(8, 0))
            ttk.Label(fila_afijos, text="Prefijo:").pack(side="left")
            var_prefix = tk.StringVar(value=paso_actual.get("prefix", ""))
            ttk.Entry(fila_afijos, textvariable=var_prefix, width=8).pack(side="left", padx=(4, 12))
            ttk.Label(fila_afijos, text="Sufijo:").pack(side="left")
            var_suffix = tk.StringVar(value=paso_actual.get("suffix", ""))
            ttk.Entry(fila_afijos, textvariable=var_suffix, width=8).pack(side="left", padx=(4, 0))
            fila_guardar_como = ttk.Frame(f_extract)
            fila_guardar_como.pack(anchor="w", pady=(10, 2), fill="x")
            boton_ayuda(
                fila_guardar_como,
                'Usa "saldo" salvo que necesites leer varios valores en el mismo flujo.',
                titulo="Guardar como",
            ).pack(side="left", padx=(0, 6))
            ttk.Label(fila_guardar_como, text="Guardar como:", font=("Segoe UI", 10)).pack(side="left")
            var_guardar_como = tk.StringVar(value=paso_actual.get("guardar_como", "saldo"))
            ttk.Entry(f_extract, textvariable=var_guardar_como, width=20).pack(anchor="w")

            grupos = {"goto": f_goto, "type": f_type, "click": f_click,
                      "key": f_key, "wait": f_wait, "extract": f_extract}
            frame_visible = {"actual": None}

            def mostrar_grupo(tipo):
                if frame_visible["actual"] is not None:
                    frame_visible["actual"].pack_forget()
                grupos[tipo].pack(fill="both", expand=True)
                frame_visible["actual"] = grupos[tipo]

            def on_tipo_cambiado(event=None):
                idx = etiquetas_tipo.index(combo_tipo.get())
                mostrar_grupo(opciones_tipo[idx])

            combo_tipo.bind("<<ComboboxSelected>>", on_tipo_cambiado)
            mostrar_grupo(paso_actual.get("type", "goto"))

            def guardar_paso():
                idx = etiquetas_tipo.index(combo_tipo.get())
                tipo = opciones_tipo[idx]

                if tipo == "goto":
                    url = var_url.get().strip()
                    if not url:
                        messagebox.showerror("Faltan datos", "Indica la URL.")
                        return
                    nuevo = {"type": "goto", "url": url}
                elif tipo == "type":
                    selector = campo_vars["selector"].get().strip()
                    if not selector:
                        messagebox.showerror("Faltan datos", "Indica el selector del campo (o elígelo en la página).")
                        return
                    nuevo = {"type": "type", "selector_type": campo_vars["selector_type"].get(),
                             "selector": selector, "valor": var_valor_type.get()}
                elif tipo == "click":
                    selector = campo_vars["selector_click"].get().strip()
                    if not selector:
                        messagebox.showerror("Faltan datos", "Indica el selector del elemento (o elígelo en la página).")
                        return
                    nuevo = {"type": "click", "selector_type": campo_vars["selector_click_type"].get(),
                             "selector": selector}
                elif tipo == "key":
                    nuevo = {"type": "key", "tecla": var_tecla.get(),
                             "selector_type": campo_vars["selector_key_type"].get(),
                             "selector": campo_vars["selector_key"].get().strip()}
                elif tipo == "wait":
                    try:
                        segundos = float(var_segundos.get().strip())
                    except ValueError:
                        messagebox.showerror("Error", "Los segundos deben ser un número.")
                        return
                    nuevo = {"type": "wait", "segundos": segundos}
                elif tipo == "extract":
                    selector = campo_vars["selector_extract"].get().strip()
                    if not selector:
                        messagebox.showerror("Faltan datos",
                                              "Indica el selector del texto a leer (o elígelo en la página).")
                        return
                    nuevo = {"type": "extract", "selector_type": campo_vars["selector_extract_type"].get(),
                             "selector": selector, "regex": var_regex.get().strip() or DEFAULT_BALANCE_REGEX,
                             "prefix": var_prefix.get(), "suffix": var_suffix.get(),
                             "guardar_como": var_guardar_como.get().strip() or "saldo"}
                else:
                    return

                on_guardar(nuevo)
                ed.destroy()

            btns_ed = ttk.Frame(cont)
            btns_ed.pack(fill="x", pady=(14, 0))
            ttk.Button(btns_ed, text="Cancelar", command=ed.destroy).pack(side="right")
            ttk.Button(btns_ed, text="💾 Guardar paso", command=guardar_paso, style="Accent.TButton").pack(
                side="right", padx=(0, 8))

        def leer_definicion():
            nombre = vars_["name"].get().strip()
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
                sheet_row = int(vars_["sheet_row"].get().strip())
                sheet_col = int(vars_["sheet_col"].get().strip())
            except ValueError:
                messagebox.showerror("Error", "Fila y columna deben ser números.")
                return None

            return {
                "name": nombre,
                "steps": [dict(p) for p in pasos],
                "usuario_default": vars_["usuario_default"].get(),
                "password_default": vars_["password_default"].get(),
                "timeout": vars_["timeout"].get().strip() or "30",
                "sheet_row": sheet_row,
                "sheet_col": sheet_col,
            }

        def probar_ahora():
            definicion = leer_definicion()
            if not definicion:
                return
            if not Path(config.get("credentials_path", "")).exists():
                messagebox.showerror("Error", "Configura primero las credenciales de Google (pestaña Configuración).")
                return

            btn_probar.config(state="disabled", text="Probando...")
            win.update_idletasks()

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
                    btn_probar.config(state="normal", text="🧪  Probar ahora")
                    if ok:
                        messagebox.showinfo("Prueba exitosa", f"Saldo detectado: {msg}\n\n"
                                             "(No se escribió en la hoja, esto fue solo una prueba de conexión.)")
                    else:
                        messagebox.showerror("Prueba fallida", f"No se pudo leer el saldo:\n{msg}")
                win.after(0, mostrar)

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

            # Quita cualquier entrada previa con el nombre original (edición)
            # o con el nuevo nombre (por si acaso), y agrega la definición actualizada.
            existentes[:] = [c for c in existentes
                              if c.get("name") not in (nombre_original, nombre_nuevo)]
            existentes.append(definicion)

            # Si se renombró el proveedor, actualiza las referencias existentes
            # conservando su posición y estado (habilitado / orden / config extra).
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
            cerrar_ventana()
            refresh_tree()

        btns = ttk.Frame(form)
        btns.grid(row=row[0], column=0, columnspan=2, pady=(18, 4), padx=15, sticky="ew")
        boton_ayuda(
            btns,
            "La prueba abre el navegador visible para que puedas ver qué hace paso a paso.\n"
            "Al guardar, la ejecución real siempre corre oculta, como los demás proveedores.",
            titulo="Probar / Guardar",
        ).pack(side="left", padx=(0, 8))
        btn_probar = ttk.Button(btns, text="🧪  Probar ahora", command=probar_ahora)
        btn_probar.pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="💾  Guardar", command=guardar_proveedor, style="Accent.TButton").pack(side="left")
        row[0] += 1

    def agregar_proveedor():
        abrir_formulario_proveedor(nombre_original=None)

    def editar_proveedor():
        selecciones = tree.selection()
        if not selecciones:
            messagebox.showinfo("Seleccionar", "Seleccione un proveedor primero.")
            return
        nombre = str(tree.item(selecciones[0])["values"][0]).strip()
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
        selecciones = tree.selection()
        if not selecciones:
            messagebox.showinfo("Seleccionar", "Seleccione un proveedor primero.")
            return
        nombre = str(tree.item(selecciones[0])["values"][0]).strip()
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

    ttk.Button(btn_panel, text="⚙  Configurar", command=configurar_proveedor, style="Accent.TButton", width=20).pack(pady=4, fill="x")
    ttk.Button(btn_panel, text="↻  Habilitar / Deshab.", command=toggle_proveedor, width=20).pack(pady=4, fill="x")
    ttk.Button(btn_panel, text="👁  Visible / Oculto", command=toggle_visible, width=20).pack(pady=4, fill="x")
    btn_probar_sel = ttk.Button(btn_panel, text="🧪  Probar ahora (visible)", command=probar_ahora_seleccionado, width=20)
    btn_probar_sel.pack(pady=4, fill="x")
    ttk.Button(btn_panel, text="＋  Agregar proveedor", command=agregar_proveedor, width=20).pack(pady=4, fill="x")
    ttk.Button(btn_panel, text="✎  Editar proveedor", command=editar_proveedor, width=20).pack(pady=4, fill="x")
    ttk.Button(btn_panel, text="🗑  Eliminar proveedor", command=eliminar_proveedor, width=20).pack(pady=4, fill="x")
    ttk.Separator(btn_panel, orient="horizontal").pack(fill="x", pady=10)
    ttk.Label(btn_panel, text="Orden:", font=("Segoe UI", 10, "bold")).pack()
    ttk.Button(btn_panel, text="↑  Subir", command=mover_arriba, width=20).pack(pady=2, fill="x")
    ttk.Button(btn_panel, text="↓  Bajar", command=mover_abajo, width=20).pack(pady=2, fill="x")

    # ============================================================
    #  Horarios de ejecución (antes era la pestaña independiente
    #  "Horarios"; ahora vive aquí, debajo de la tabla de proveedores —
    #  basta con bajar el scroll de esta pestaña para verla y editarla).
    # ============================================================
    hor_wrapper, hor_frame = crear_seccion_plegable(prov_content, "⏰  Horarios de ejecución")
    hor_wrapper.pack(fill="x", padx=10, pady=(5, 15))

    fila_ayuda_horarios = ttk.Frame(hor_frame)
    fila_ayuda_horarios.pack(anchor="w", pady=(0, 12))
    boton_ayuda(
        fila_ayuda_horarios,
        "Momentos del día en que se consulta el saldo automáticamente. Usa el (+) para agregar otro horario.",
        titulo="Horarios de ejecución",
    ).pack(side="left")

    MAX_HORARIOS_POR_FILA = 8

    def _build_time_chips(parent, times):
        """Dibuja 'times' (lista mutable de strings 'HH:MM') como chips
        editables en fila, con una (✕) para quitar cada uno y un botón (+)
        al final para agregar uno nuevo. 'times' queda sincronizada en todo
        momento, así que al guardar solo hace falta leer esa lista."""
        wrap = ttk.Frame(parent)
        wrap.pack(fill="x", pady=(0, 4))

        def redraw():
            for w in wrap.winfo_children():
                w.destroy()

            for idx in range(len(times)):
                r, c = divmod(idx, MAX_HORARIOS_POR_FILA)
                chip = tk.Frame(wrap, bg=ENTRY_BG, highlightbackground="#585b70",
                                 highlightcolor="#585b70", highlightthickness=1)
                chip.grid(row=r, column=c, padx=(0, 8), pady=4, sticky="w")

                var = tk.StringVar(value=times[idx])

                def on_edit(*_a, idx=idx, var=var):
                    times[idx] = var.get().strip()
                var.trace_add("write", on_edit)

                tk.Label(chip, text="🕐", bg=ENTRY_BG, fg="#9399b2", font=("Segoe UI", 9)).pack(
                    side="left", padx=(10, 2), pady=6)
                tk.Entry(chip, textvariable=var, width=6, relief="flat", bd=0,
                         bg=ENTRY_BG, fg=FG, insertbackground=FG, justify="center",
                         font=("Segoe UI", 10), highlightthickness=0).pack(side="left", pady=6)

                def quitar(idx=idx):
                    del times[idx]
                    redraw()

                cerrar = tk.Label(chip, text="✕", bg=ENTRY_BG, fg="#9399b2",
                                   font=("Segoe UI", 9), cursor="hand2")
                cerrar.pack(side="left", padx=(4, 10), pady=6)
                cerrar.bind("<Button-1>", lambda e, idx=idx: quitar(idx))
                cerrar.bind("<Enter>", lambda e, w=cerrar: w.config(fg="#f38ba8"))
                cerrar.bind("<Leave>", lambda e, w=cerrar: w.config(fg="#9399b2"))

            def agregar():
                times.append("08:00")
                redraw()

            add_r, add_c = divmod(len(times), MAX_HORARIOS_POR_FILA)
            ttk.Button(wrap, text="+", width=3, command=agregar).grid(
                row=add_r, column=add_c, padx=(0, 8), pady=4, sticky="w")

        redraw()

    horarios_lv = [str(t).strip() for t in config.get("horarios_lun_vie", []) if str(t).strip()]
    horarios_sab = [str(t).strip() for t in config.get("horarios_sabado", []) if str(t).strip()]

    ttk.Label(hor_frame, text="Lunes a Viernes:", font=("Segoe UI", 10, "bold")).pack(anchor="w")
    _build_time_chips(hor_frame, horarios_lv)

    ttk.Label(hor_frame, text="Sábados:", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(14, 0))
    _build_time_chips(hor_frame, horarios_sab)

    tol_row = ttk.Frame(hor_frame)
    tol_row.pack(fill="x", pady=(14, 0))
    ttk.Label(tol_row, text="Tolerancia (min):", font=("Segoe UI", 10, "bold")).pack(side="left")
    tol_var = tk.StringVar(value=str(config.get("tolerancia_min", 1.5)))
    ttk.Entry(tol_row, textvariable=tol_var, width=8).pack(side="left", padx=(10, 0))

    def guardar_horarios():
        patron = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")
        lv_final = [t for t in horarios_lv if t]
        sab_final = [t for t in horarios_sab if t]
        for t in lv_final + sab_final:
            if not patron.match(t):
                messagebox.showerror("Error", f"'{t}' no es una hora válida (usa HH:MM, ej: 08:10).")
                return
        try:
            tolerancia = float(tol_var.get())
        except ValueError:
            messagebox.showerror("Error", "La tolerancia debe ser un número.")
            return

        config["horarios_lun_vie"] = lv_final
        config["horarios_sabado"] = sab_final
        config["tolerancia_min"] = tolerancia
        save_config(config)
        messagebox.showinfo("Guardado", "Horarios actualizados.")

    ttk.Button(hor_frame, text="💾  Guardar Horarios", command=guardar_horarios, style="Accent.TButton").pack(
        anchor="e", pady=(16, 0)
    )
