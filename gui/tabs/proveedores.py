"""Pestaña "Proveedores": tabla de proveedores (habilitar/deshabilitar,
visible/oculto, orden) y formulario para agregar proveedores genéricos
por selectores web (sin escribir un .py a medida)."""
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

from core.providers_registry import get_all_providers, sort_providers_by_order, get_custom_provider_names
from core.chrome_tools import ensure_chrome, ensure_chromedriver, get_robust_driver
from generic_provider import GenericWebProvider, DEFAULT_BALANCE_REGEX


def build(notebook, ctx):
    root = ctx.root
    BG = ctx.BG
    ACCENT = ctx.ACCENT
    config = ctx.config
    save_config = ctx.save_config

    tab_prov = ttk.Frame(notebook)
    notebook.add(tab_prov, text="   Proveedores   ")

    main_frame = ttk.Frame(tab_prov)
    main_frame.pack(fill="both", expand=True, padx=5, pady=5)

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
    tree.pack(side="left", fill="both", expand=True)

    scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=tree.yview)
    scrollbar.pack(side="right", fill="y")
    tree.configure(yscrollcommand=scrollbar.set)

    btn_panel = ttk.Frame(main_frame)
    btn_panel.pack(side="right", fill="y", padx=(15, 5))

    def refresh_tree():
        tree.delete(*tree.get_children())
        providers = get_all_providers()
        providers = sort_providers_by_order(providers, config)
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
        win.geometry("560x640")
        win.configure(bg=BG)
        win.transient(root)
        win.grab_set()

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

        def add_selector_combo(label, key, default_type="name"):
            valor = datos_existentes.get(key, default_type)
            ttk.Label(form, text=label, font=("Segoe UI", 10)).grid(
                row=row[0], column=0, sticky="w", padx=15, pady=6)
            var = tk.StringVar(value=valor)
            combo = ttk.Combobox(form, textvariable=var, values=["name", "id", "css", "xpath"],
                                  width=10, state="readonly")
            combo.grid(row=row[0], column=1, padx=15, pady=6, sticky="w")
            vars_[key] = var
            row[0] += 1
            return var

        ttk.Label(form, text="Datos del sitio", font=("Segoe UI", 11, "bold"),
                  foreground=ACCENT).grid(row=row[0], column=0, columnspan=2, sticky="w", padx=15, pady=(10, 4))
        row[0] += 1

        add_field("Nombre del proveedor *", "name")
        add_field("URL de inicio de sesión *", "url", width=48)
        add_field("Usuario (por defecto)", "usuario_default")
        add_field("Contraseña (por defecto)", "password_default")

        ttk.Label(form, text="Selector campo Usuario", font=("Segoe UI", 11, "bold"),
                  foreground=ACCENT).grid(row=row[0], column=0, columnspan=2, sticky="w", padx=15, pady=(14, 4))
        row[0] += 1
        add_selector_combo("Tipo", "user_selector_type", "name")
        add_field("Valor (ej: username)", "user_selector")

        ttk.Label(form, text="Selector campo Contraseña", font=("Segoe UI", 11, "bold"),
                  foreground=ACCENT).grid(row=row[0], column=0, columnspan=2, sticky="w", padx=15, pady=(14, 4))
        row[0] += 1
        add_selector_combo("Tipo", "pass_selector_type", "name")
        add_field("Valor (ej: password)", "pass_selector")

        ttk.Label(form, text="Botón enviar (opcional; si se deja vacío, se usa ENTER)",
                  font=("Segoe UI", 11, "bold"), foreground=ACCENT).grid(
            row=row[0], column=0, columnspan=2, sticky="w", padx=15, pady=(14, 4))
        row[0] += 1
        add_selector_combo("Tipo", "submit_selector_type", "css")
        add_field("Valor (ej: button[type=submit])", "submit_selector")

        ttk.Label(form, text="Dónde leer el saldo", font=("Segoe UI", 11, "bold"),
                  foreground=ACCENT).grid(row=row[0], column=0, columnspan=2, sticky="w", padx=15, pady=(14, 4))
        row[0] += 1
        add_selector_combo("Tipo", "balance_selector_type", "xpath")
        add_field("Valor (ej: //span[@class='balance'])", "balance_selector", width=48)
        add_field("Regex de extracción (opcional)", "balance_regex", default=DEFAULT_BALANCE_REGEX, width=48)
        add_field("Prefijo (ej: '$ ')", "prefix")
        add_field("Sufijo (ej: ' USD')", "suffix")
        add_field("Espera tras login (segundos)", "wait_after_login", default="1.5", width=10)
        add_field("Timeout de carga (segundos)", "timeout", default="30", width=10)

        ttk.Label(form, text="Ubicación en la hoja de cálculo", font=("Segoe UI", 11, "bold"),
                  foreground=ACCENT).grid(row=row[0], column=0, columnspan=2, sticky="w", padx=15, pady=(14, 4))
        row[0] += 1
        add_field("Fila (Sheet Row)", "sheet_row", default="1", width=10)
        add_field("Columna (Sheet Col)", "sheet_col", default="1", width=10)

        aviso = ("Nota: este formulario cubre sitios con login simple de usuario/contraseña "
                 "en una sola página. Sitios con captcha, varios pasos o iframes anidados "
                 "todavía requieren un archivo .py a medida en la carpeta 'providers'.")
        ttk.Label(form, text=aviso, foreground="#9399b2", wraplength=440, justify="left",
                  font=("Segoe UI", 9)).grid(row=row[0], column=0, columnspan=2, sticky="w", padx=15, pady=(14, 4))
        row[0] += 1

        def leer_definicion():
            nombre = vars_["name"].get().strip()
            url = vars_["url"].get().strip()
            if not nombre or not url:
                messagebox.showerror("Faltan datos", "El nombre y la URL son obligatorios.")
                return None
            if not vars_["user_selector"].get().strip() or not vars_["pass_selector"].get().strip():
                messagebox.showerror("Faltan datos", "Debes indicar el selector de usuario y de contraseña.")
                return None
            if not vars_["balance_selector"].get().strip():
                messagebox.showerror("Faltan datos", "Debes indicar el selector donde está el saldo.")
                return None
            try:
                sheet_row = int(vars_["sheet_row"].get().strip())
                sheet_col = int(vars_["sheet_col"].get().strip())
            except ValueError:
                messagebox.showerror("Error", "Fila y columna deben ser números.")
                return None

            return {
                "name": nombre,
                "url": url,
                "usuario_default": vars_["usuario_default"].get(),
                "password_default": vars_["password_default"].get(),
                "user_selector_type": vars_["user_selector_type"].get(),
                "user_selector": vars_["user_selector"].get().strip(),
                "pass_selector_type": vars_["pass_selector_type"].get(),
                "pass_selector": vars_["pass_selector"].get().strip(),
                "submit_selector_type": vars_["submit_selector_type"].get(),
                "submit_selector": vars_["submit_selector"].get().strip(),
                "balance_selector_type": vars_["balance_selector_type"].get(),
                "balance_selector": vars_["balance_selector"].get().strip(),
                "balance_regex": vars_["balance_regex"].get().strip() or DEFAULT_BALANCE_REGEX,
                "prefix": vars_["prefix"].get(),
                "suffix": vars_["suffix"].get(),
                "wait_after_login": vars_["wait_after_login"].get().strip() or "1.5",
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
                    provider = GenericWebProvider(definicion)
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
            win.destroy()
            refresh_tree()

        btns = ttk.Frame(form)
        btns.grid(row=row[0], column=0, columnspan=2, pady=(18, 4), padx=15, sticky="ew")
        btn_probar = ttk.Button(btns, text="🧪  Probar ahora", command=probar_ahora)
        btn_probar.pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="💾  Guardar", command=guardar_proveedor, style="Accent.TButton").pack(side="left")
        row[0] += 1
        ttk.Label(form, text="La prueba abre el navegador visible para que puedas ver qué hace paso a paso.\n"
                              "Al guardar, la ejecución real siempre corre oculta, como los demás proveedores.",
                  foreground="#9399b2", font=("Segoe UI", 8), justify="left").grid(
            row=row[0], column=0, columnspan=2, sticky="w", padx=15, pady=(0, 10))

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
