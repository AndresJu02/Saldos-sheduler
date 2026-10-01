"""Pestaña "Tienda DID": comprar líneas DIDWW nuevas desde la misma
aplicación, sin depender de un segundo programa aparte (antes era la app
WinForms/C# "TIENDA DID", github.com/AndresJu02/TIENDA-DID). La lógica de
la API vive en core/didww_store.py — este archivo solo arma la pestaña.

Reutiliza la MISMA API Key de DIDWW que ya está configurada en la pestaña
Proveedores -> DIDWW -> Configurar (providers_config.DIDWW.api_key), igual
que hace Renovación DIDWW. A diferencia del original en C#, esta pestaña
NUNCA carga la clave desde un archivo público de GitHub.

Toda compra (número puntual o aleatorio) es una acción REAL con costo en
la cuenta de DIDWW, por eso siempre se pide confirmación antes de comprar.
"""
import threading
import tkinter as tk
from datetime import datetime
from tkinter import ttk

from core import didww_store as store
from gui import dialogs as messagebox
from gui.collapsible import crear_seccion_plegable


def build(notebook, ctx):
    root = ctx.root
    ACCENT = ctx.ACCENT
    BG = ctx.BG
    FG = ctx.FG
    ENTRY_BG = ctx.ENTRY_BG
    config = ctx.config

    def estilizar_listbox(lb):
        """Los tk.Listbox no son ttk, así que no heredan el tema oscuro solo
        -quedan blancos-; se colorean a mano con la misma paleta que ya usan
        Combobox/Treeview (gui/theme.py): fondo azul tenue, texto claro,
        selección en el color de acento."""
        lb.configure(
            bg=ENTRY_BG, fg=FG,
            selectbackground=ACCENT, selectforeground=BG,
            highlightthickness=1, highlightbackground="#585b70", highlightcolor="#585b70",
            relief="flat", bd=0, activestyle="none", font=("Segoe UI", 10),
        )
        return lb

    tab = ttk.Frame(notebook)
    notebook.add(tab, text="   Tienda DID   ")

    def api_key():
        return config.get("providers_config", {}).get("DIDWW", {}).get("api_key", "").strip()

    SALDO_REFRESH_MS = 30 * 60 * 1000  # 30 minutos

    top_frame = ttk.Frame(tab)
    top_frame.pack(fill="x", padx=10, pady=(15, 0))

    aviso_frame = ttk.Frame(top_frame)
    aviso_frame.pack(side="left", fill="x", expand=True)
    ttk.Label(
        aviso_frame,
        text="",
        foreground="#9399b2", wraplength=600, justify="left", font=("Segoe UI", 9)
    ).pack(anchor="w")

    saldo_frame = ttk.Frame(top_frame)
    saldo_frame.pack(side="right", anchor="n")

    saldo_row = ttk.Frame(saldo_frame)
    saldo_row.pack(anchor="e")
    saldo_var = tk.StringVar(value="Saldo DIDWW: —")
    ttk.Label(saldo_row, textvariable=saldo_var, font=("Segoe UI", 10, "bold"), foreground=ACCENT).pack(
        side="left", padx=(0, 6))
    btn_saldo_refresh = ttk.Button(saldo_row, text="🔄", width=3)
    btn_saldo_refresh.pack(side="left")

    saldo_estado_var = tk.StringVar(value="")
    lbl_saldo_estado = ttk.Label(saldo_frame, textvariable=saldo_estado_var, font=("Segoe UI", 8))
    lbl_saldo_estado.pack(anchor="e")
    VERDE_OK = "#a6e3a1"
    GRIS_ESTADO = "#9399b2"

    inner = ttk.Notebook(tab)
    inner.pack(fill="both", expand=True, padx=10, pady=10)

    tab_puntual = ttk.Frame(inner)
    tab_random = ttk.Frame(inner)
    inner.add(tab_puntual, text="  Número específico  ")
    inner.add(tab_random, text="  Aleatorio  ")

    # ------------------------------------------------------------------
    # Helpers comunes
    # ------------------------------------------------------------------
    def run_bg(tarea, on_done):
        """Corre `tarea` (sin argumentos) en un hilo aparte y entrega su
        resultado (o excepción) a `on_done` ya en el hilo de la GUI."""
        def wrapper():
            try:
                resultado = tarea()
                error = None
            except Exception as e:
                resultado = None
                error = e
            root.after(0, lambda: on_done(resultado, error))
        threading.Thread(target=wrapper, daemon=True).start()

    def set_combo_items(combo, items, text_fn):
        """Llena un Combobox con `items` (lista de objetos JSON:API con
        "id"/"attributes"), guardando la lista completa sin filtrar en el
        propio widget: combo.data (los items originales) y combo.all_values
        (sus textos), en el mismo orden. selected_item()/selected_id()
        resuelven el elegido comparando el texto actual contra all_values,
        así que siguen funcionando aunque el desplegable esté mostrando
        solo un subconjunto filtrado (ver hacer_combobox_buscable)."""
        combo.data = items
        combo.all_values = [text_fn(it) for it in items]
        combo["values"] = combo.all_values
        combo.set("")

    def selected_item(combo):
        texto = combo.get().strip()
        todas = getattr(combo, "all_values", None) or []
        datos = getattr(combo, "data", None)
        if not texto or not datos or texto not in todas:
            return None
        return datos[todas.index(texto)]

    def selected_id(combo):
        item = selected_item(combo)
        return item["id"] if item else None

    def nombre_attr(it):
        return (it.get("attributes") or {}).get("name") or "(sin nombre)"

    def hacer_combobox_buscable(combo):
        """Al hacer clic en la flechita (▼) se abre el desplegable nativo
        de siempre (con la lista completa); al escribir en el campo, en
        vez de eso aparece un desplegable propio (un Toplevel sin bordes,
        debajo del combo) que se va filtrando en vivo con cada letra.

        No se usa el desplegable nativo para esto porque re-"postearlo"
        (ttk::combobox::Post, la única forma de refrescar sus opciones
        mientras está abierto) hace un grab interno que manda las
        siguientes teclas a esa lista en vez de al campo de texto -se
        probó y se quedaba pegado en la primera letra-. Este desplegable
        propio no toma foco ni grab, así que se puede seguir escribiendo
        sin cortes; un clic sobre una opción la selecciona igual."""
        combo.configure(state="normal")

        popup = tk.Toplevel(root)
        popup.withdraw()
        popup.overrideredirect(True)
        popup.wm_attributes("-topmost", True)
        popup_list = estilizar_listbox(tk.Listbox(popup, exportselection=False))
        popup_list.pack(fill="both", expand=True)
        popup_items = []
        resaltado = {"idx": -1}  # cuál opción quedaría elegida con Enter/clic

        def ocultar(event=None):
            popup.withdraw()
            resaltado["idx"] = -1

        def resaltar(idx):
            """Marca con el color de acento la opción `idx` -la que se
            elegiría si se confirma ahora-, para poder identificarla antes
            de hacer clic. Se usa .selection_set en vez de dejarlo al
            <<ListboxSelect>> nativo porque ese evento también se dispara
            solo con el hover/las flechas y confirmaría de una la opción
            con solo pasar el mouse por encima, que no es lo que se quiere:
            resaltar y confirmar quedan separados a propósito (ver
            on_click_item/confirmar)."""
            if not (0 <= idx < popup_list.size()):
                return
            popup_list.selection_clear(0, tk.END)
            popup_list.selection_set(idx)
            popup_list.see(idx)
            resaltado["idx"] = idx

        def mostrar(items):
            if not items:
                ocultar()
                return
            popup_items.clear()
            popup_items.extend(items)
            popup_list.delete(0, tk.END)
            for it in items:
                popup_list.insert(tk.END, it)
            popup_list.configure(height=min(len(items), 8))
            popup.update_idletasks()
            x = combo.winfo_rootx()
            y = combo.winfo_rooty() + combo.winfo_height()
            w = combo.winfo_width()
            h = popup_list.winfo_reqheight()
            popup.geometry(f"{w}x{h}+{x}+{y}")
            popup.deiconify()
            popup.lift()
            resaltar(0)

        def elegir_indice(idx):
            if not (0 <= idx < len(popup_items)):
                return
            combo.set(popup_items[idx])
            ocultar()
            combo.focus_set()
            combo.event_generate("<<ComboboxSelected>>")

        def on_click_item(event):
            elegir_indice(popup_list.nearest(event.y))
            return "break"

        def on_motion_item(event):
            resaltar(popup_list.nearest(event.y))

        def mover(delta):
            if not popup.winfo_ismapped():
                return
            nuevo = resaltado["idx"] + delta
            resaltar(max(0, min(nuevo, popup_list.size() - 1)))
            return "break"

        def confirmar(event=None):
            if popup.winfo_ismapped() and resaltado["idx"] >= 0:
                elegir_indice(resaltado["idx"])
                return "break"

        def calcular_filtradas():
            texto = combo.get().strip().lower()
            todas = getattr(combo, "all_values", None) or []
            return [v for v in todas if texto in v.lower()] if texto else todas

        def filtrar(event=None):
            if event is not None and event.keysym in ("Up", "Down", "Return", "Tab", "Escape"):
                return
            filtradas = calcular_filtradas()
            combo["values"] = filtradas
            mostrar(filtradas)

        def al_enfocar(event=None):
            if not combo.get().strip():
                combo["values"] = getattr(combo, "all_values", None) or []

        def al_perder_foco(event=None):
            # Se retrasa el cierre: si lo que pasó fue un clic sobre una
            # opción del propio desplegable, "elegir" ya lo cierra antes
            # de que este retraso corra; si no, se termina cerrando solo.
            combo.after(150, ocultar)

        def al_click(event):
            # ttk identifica la zona del clic dentro del propio widget:
            # "textarea" es donde se escribe; todo lo demás -"downarrow"
            # (la flechita ▼) pero también una franja angosta de ~3px
            # ("padding"/"Combobox.field") justo antes de la flecha- es la
            # zona de abrir/cerrar el desplegable. Ahí se abre el propio
            # (filtrado con lo que ya esté escrito) en vez del nativo de
            # ttk, para que sea el mismo desplegable con búsqueda tanto
            # escribiendo como con el mouse; un segundo clic ahí lo cierra
            # (antes solo se cerraba eligiendo una opción).
            if combo.identify(event.x, event.y) != "textarea":
                if popup.winfo_ismapped():
                    ocultar()
                else:
                    filtradas = calcular_filtradas()
                    combo["values"] = filtradas
                    mostrar(filtradas)
                return "break"

        popup_list.bind("<Button-1>", on_click_item)
        popup_list.bind("<Motion>", on_motion_item)
        combo.bind("<KeyRelease>", filtrar)
        combo.bind("<FocusIn>", al_enfocar)
        combo.bind("<FocusOut>", al_perder_foco)
        combo.bind("<Button-1>", al_click)
        combo.bind("<Down>", lambda e: mover(1))
        combo.bind("<Up>", lambda e: mover(-1))
        combo.bind("<Return>", confirmar)
        combo.bind("<Escape>", ocultar)

    # ------------------------------------------------------------------
    # Saldo DIDWW (esquina superior derecha): se refresca solo cada 30
    # minutos, y también con el botón 🔄 (que fuerza una consulta ahora).
    # ------------------------------------------------------------------
    def actualizar_saldo(manual=False):
        if not api_key():
            saldo_var.set("Saldo DIDWW: (falta API Key)")
            saldo_estado_var.set("")
            return

        if manual:
            btn_saldo_refresh.config(state="disabled")
        lbl_saldo_estado.config(foreground=GRIS_ESTADO)
        saldo_estado_var.set("Actualizando...")

        def tarea():
            return store.get_balance(api_key())

        def on_done(resultado, error):
            if manual:
                btn_saldo_refresh.config(state="normal")
            if error:
                if isinstance(error, store.DidwwApiKeyError):
                    saldo_var.set("Saldo DIDWW: ⚠️ API Key inválida")
                else:
                    saldo_var.set("Saldo DIDWW: error al consultar")
                saldo_estado_var.set("")
                return
            saldo_var.set(f"Saldo DIDWW: $ {resultado:,.2f}")
            lbl_saldo_estado.config(foreground=VERDE_OK)
            saldo_estado_var.set(f"✅ Actualizado a las {datetime.now().strftime('%H:%M')}")

        run_bg(tarea, on_done)

    def _auto_refresh_saldo():
        actualizar_saldo(manual=False)
        root.after(SALDO_REFRESH_MS, _auto_refresh_saldo)

    btn_saldo_refresh.config(command=lambda: actualizar_saldo(manual=True))

    # ------------------------------------------------------------------
    # Carga inicial (países / tipos DID) — igual en ambas sub-pestañas
    # ------------------------------------------------------------------
    def cargar_iniciales(cmb_country, cmb_type, status_var):
        if not api_key():
            status_var.set("⚠️ Falta configurar la API Key de DIDWW (Proveedores → DIDWW → Configurar).")
            return

        status_var.set("Cargando países y tipos de DID...")

        def tarea():
            return store.get_countries(api_key()), store.get_did_group_types(api_key())

        def on_done(resultado, error):
            if error:
                status_var.set(f"⚠️ Error al cargar países/tipos: {error}")
                return
            countries, types = resultado
            set_combo_items(cmb_country, countries, nombre_attr)
            set_combo_items(cmb_type, types, nombre_attr)
            status_var.set("")

        run_bg(tarea, on_done)

    # ==================================================================
    # SUB-PESTAÑA: Número específico
    # ==================================================================
    frm_wrapper, frm = crear_seccion_plegable(tab_puntual, "🔎  Buscar números disponibles")
    frm_wrapper.pack(fill="x", padx=10, pady=(15, 10))

    ttk.Label(frm, text="País:", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", pady=5)
    cmb_p_country = ttk.Combobox(frm, state="readonly", width=28)
    cmb_p_country.grid(row=0, column=1, padx=10, pady=5, sticky="w")

    ttk.Label(frm, text="Tipo DID:", font=("Segoe UI", 10, "bold")).grid(row=0, column=2, sticky="w", pady=5, padx=(20, 0))
    cmb_p_type = ttk.Combobox(frm, state="readonly", width=22)
    cmb_p_type.grid(row=0, column=3, padx=10, pady=5, sticky="w")

    ttk.Label(frm, text="Ciudad:", font=("Segoe UI", 10, "bold")).grid(row=1, column=0, sticky="w", pady=5)
    cmb_p_city = ttk.Combobox(frm, state="readonly", width=28)
    cmb_p_city.grid(row=1, column=1, padx=10, pady=5, sticky="w")

    ttk.Label(frm, text="Región:", font=("Segoe UI", 10, "bold")).grid(row=1, column=2, sticky="w", pady=5, padx=(20, 0))
    cmb_p_region = ttk.Combobox(frm, state="readonly", width=22)
    cmb_p_region.grid(row=1, column=3, padx=10, pady=5, sticky="w")

    p_status_var = tk.StringVar()
    ttk.Label(frm, textvariable=p_status_var, foreground="#9399b2", font=("Segoe UI", 9)).grid(
        row=2, column=0, columnspan=4, sticky="w", pady=(8, 0))

    def p_on_country_changed(event=None):
        cmb_p_city.set("")
        cmb_p_region.set("")
        country_id = selected_id(cmb_p_country)
        if not country_id:
            return

        def tarea():
            return store.get_cities(api_key(), country_id), store.get_regions(api_key(), country_id)

        def on_done(resultado, error):
            if error:
                p_status_var.set(f"⚠️ Error al cargar ciudades/regiones: {error}")
                return
            cities, regions = resultado
            set_combo_items(cmb_p_city, cities, nombre_attr)
            set_combo_items(cmb_p_region, regions, nombre_attr)

        run_bg(tarea, on_done)

    def p_on_region_changed(event=None):
        # Región y ciudad son mutuamente excluyentes, igual que el original.
        if selected_id(cmb_p_region):
            cmb_p_city.set("")
            cmb_p_city.configure(state="disabled")
        else:
            cmb_p_city.configure(state="normal")

    cmb_p_country.bind("<<ComboboxSelected>>", p_on_country_changed)
    cmb_p_region.bind("<<ComboboxSelected>>", p_on_region_changed)

    lst_frame = ttk.Frame(tab_puntual)
    lst_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
    lst_frame.columnconfigure(0, weight=1)
    lst_frame.columnconfigure(1, weight=1)

    ttk.Label(lst_frame, text="Prefijos encontrados:", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
    ttk.Label(lst_frame, text="Números disponibles:", font=("Segoe UI", 10, "bold")).grid(row=0, column=1, sticky="w", padx=(15, 0))

    lst_groups = estilizar_listbox(tk.Listbox(lst_frame, height=10, exportselection=False))
    lst_groups.grid(row=1, column=0, sticky="nsew", pady=5)
    lst_numbers = estilizar_listbox(tk.Listbox(lst_frame, height=10, exportselection=False))
    lst_numbers.grid(row=1, column=1, sticky="nsew", pady=5, padx=(15, 0))
    lst_frame.rowconfigure(1, weight=1)

    groups_data = []  # paralelo a lst_groups
    numbers_data = []  # paralelo a lst_numbers

    def buscar_prefijos():
        if not api_key():
            messagebox.showerror("Falta la API Key", "Configura la API Key de DIDWW en Proveedores → DIDWW → Configurar.")
            return
        country_id = selected_id(cmb_p_country)
        type_id = selected_id(cmb_p_type)
        if not country_id or not type_id:
            messagebox.showinfo("Faltan datos", "Selecciona País y Tipo DID.")
            return
        city_id = selected_id(cmb_p_city) if str(cmb_p_city["state"]) != "disabled" else None
        region_id = selected_id(cmb_p_region)

        lst_groups.delete(0, tk.END)
        lst_numbers.delete(0, tk.END)
        groups_data.clear()
        numbers_data.clear()
        btn_comprar_puntual.config(state="disabled")
        p_status_var.set("Buscando prefijos...")

        def tarea():
            return store.get_did_groups(api_key(), country_id, city_id=city_id, region_id=region_id, type_id=type_id)

        def on_done(resultado, error):
            if error:
                p_status_var.set("")
                messagebox.showerror("Error", f"Error en búsqueda de prefijos: {error}")
                return
            groups_data.extend(resultado)
            for g in resultado:
                attrs = g.get("attributes") or {}
                prefix = attrs.get("prefix") or ""
                area = attrs.get("area_name") or ""
                texto = f"📍 {prefix}" + (f" — {area}" if area else "")
                lst_groups.insert(tk.END, texto)
            p_status_var.set("" if resultado else "No hay prefijos disponibles para esos filtros.")

        run_bg(tarea, on_done)

    def on_group_selected(event=None):
        sel = lst_groups.curselection()
        lst_numbers.delete(0, tk.END)
        numbers_data.clear()
        btn_comprar_puntual.config(state="disabled")
        if not sel:
            return
        group_id = groups_data[sel[0]]["id"]
        p_status_var.set("Cargando numeraciones disponibles...")

        def tarea():
            return store.get_available_dids(api_key(), group_id)

        def on_done(resultado, error):
            if error:
                p_status_var.set("")
                messagebox.showerror("Error", f"Error al cargar numeraciones: {error}")
                return
            numbers_data.extend(resultado)
            for n in resultado:
                numero = (n.get("attributes") or {}).get("number") or "(sin número)"
                lst_numbers.insert(tk.END, numero)
            p_status_var.set("" if resultado else "No hay numeraciones disponibles para ese prefijo.")

        run_bg(tarea, on_done)

    def on_number_selected(event=None):
        btn_comprar_puntual.config(state="normal" if lst_numbers.curselection() else "disabled")

    lst_groups.bind("<<ListboxSelect>>", on_group_selected)
    lst_numbers.bind("<<ListboxSelect>>", on_number_selected)

    def comprar_puntual():
        sel_g = lst_groups.curselection()
        sel_n = lst_numbers.curselection()
        if not sel_g or not sel_n:
            return
        group_id = groups_data[sel_g[0]]["id"]
        did = numbers_data[sel_n[0]]
        numero = (did.get("attributes") or {}).get("number") or did["id"]

        if not messagebox.askyesno("Confirmar compra", f"¿Comprar el número {numero}? Esta acción tiene costo real."):
            return

        btn_comprar_puntual.config(state="disabled")
        p_status_var.set(f"Comprando {numero}...")

        def tarea():
            sku_id = store.get_valid_sku_id(api_key(), group_id)
            if not sku_id:
                raise RuntimeError("No se encontró un SKU válido con 2 canales para este grupo DID.")
            return store.create_order(api_key(), did["id"], sku_id)

        def on_done(resultado, error):
            p_status_var.set("")
            if error:
                messagebox.showerror("Error al comprar", str(error))
                btn_comprar_puntual.config(state="normal")
                return
            messagebox.showinfo("Compra realizada", f"✅ Se compró el número {numero} correctamente.")
            lst_numbers.delete(0, tk.END)
            numbers_data.clear()

        run_bg(tarea, on_done)

    ttk.Button(frm, text="🔎  Buscar prefijos", command=buscar_prefijos, style="Accent.TButton").grid(
        row=0, column=4, rowspan=2, padx=(30, 0))

    btn_comprar_puntual = ttk.Button(frm, text="🛒  Comprar número seleccionado", command=comprar_puntual,
                                      style="Accent.TButton", state="disabled")
    btn_comprar_puntual.grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))

    # ==================================================================
    # SUB-PESTAÑA: Aleatorio
    # ==================================================================
    frm_r_wrapper, frm_r = crear_seccion_plegable(tab_random, "🎲  Buscar prefijos para compra aleatoria")
    frm_r_wrapper.pack(fill="x", padx=10, pady=(15, 10))

    ttk.Label(frm_r, text="País:", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", pady=5)
    cmb_r_country = ttk.Combobox(frm_r, state="readonly", width=28)
    cmb_r_country.grid(row=0, column=1, padx=10, pady=5, sticky="w")

    ttk.Label(frm_r, text="Tipo DID:", font=("Segoe UI", 10, "bold")).grid(row=0, column=2, sticky="w", pady=5, padx=(20, 0))
    cmb_r_type = ttk.Combobox(frm_r, state="readonly", width=22)
    cmb_r_type.grid(row=0, column=3, padx=10, pady=5, sticky="w")

    ttk.Label(frm_r, text="Ciudad:", font=("Segoe UI", 10, "bold")).grid(row=1, column=0, sticky="w", pady=5)
    cmb_r_city = ttk.Combobox(frm_r, state="readonly", width=28)
    cmb_r_city.grid(row=1, column=1, padx=10, pady=5, sticky="w")

    ttk.Label(frm_r, text="Región:", font=("Segoe UI", 10, "bold")).grid(row=1, column=2, sticky="w", pady=5, padx=(20, 0))
    cmb_r_region = ttk.Combobox(frm_r, state="readonly", width=22)
    cmb_r_region.grid(row=1, column=3, padx=10, pady=5, sticky="w")

    ttk.Label(frm_r, text="Cantidad:", font=("Segoe UI", 10, "bold")).grid(row=2, column=0, sticky="w", pady=5)
    cmb_r_qty = ttk.Combobox(frm_r, state="readonly", width=10, values=["1", "2", "5", "10", "20"])
    cmb_r_qty.current(0)
    cmb_r_qty.grid(row=2, column=1, padx=10, pady=5, sticky="w")

    r_status_var = tk.StringVar()
    ttk.Label(frm_r, textvariable=r_status_var, foreground="#9399b2", font=("Segoe UI", 9)).grid(
        row=3, column=0, columnspan=4, sticky="w", pady=(8, 0))

    def _es_movil(combo_type):
        item = selected_item(combo_type)
        return bool(item) and "mobile" in nombre_attr(item).lower()

    def r_on_country_changed(event=None):
        cmb_r_city.set("")
        cmb_r_region.set("")
        country_id = selected_id(cmb_r_country)
        if not country_id:
            return

        def tarea():
            return store.get_cities(api_key(), country_id), store.get_regions(api_key(), country_id)

        def on_done(resultado, error):
            if error:
                r_status_var.set(f"⚠️ Error al cargar ciudades/regiones: {error}")
                return
            cities, regions = resultado
            set_combo_items(cmb_r_city, cities, nombre_attr)
            set_combo_items(cmb_r_region, regions, nombre_attr)

        run_bg(tarea, on_done)

    def r_on_region_changed(event=None):
        if selected_id(cmb_r_region):
            cmb_r_city.set("")
            cmb_r_city.configure(state="disabled")
        else:
            cmb_r_city.configure(state="normal")

    cmb_r_country.bind("<<ComboboxSelected>>", r_on_country_changed)
    cmb_r_region.bind("<<ComboboxSelected>>", r_on_region_changed)

    ttk.Label(tab_random, text="Prefijos encontrados:", font=("Segoe UI", 10, "bold")).pack(
        anchor="w", padx=10)
    lst_groups_r = estilizar_listbox(tk.Listbox(tab_random, height=10, exportselection=False))
    lst_groups_r.pack(fill="both", expand=True, padx=10, pady=(5, 10))
    groups_data_r = []

    def buscar_prefijos_random():
        if not api_key():
            messagebox.showerror("Falta la API Key", "Configura la API Key de DIDWW en Proveedores → DIDWW → Configurar.")
            return
        country_id = selected_id(cmb_r_country)
        type_id = selected_id(cmb_r_type)
        if not country_id or not type_id:
            messagebox.showinfo("Faltan datos", "Selecciona País y Tipo DID.")
            return

        es_movil = _es_movil(cmb_r_type)
        city_id = None
        region_id = None
        if not es_movil:
            city_id = selected_id(cmb_r_city) if str(cmb_r_city["state"]) != "disabled" else None
            region_id = selected_id(cmb_r_region)
            if not city_id and not region_id:
                messagebox.showinfo("Faltan datos", "Selecciona Ciudad o Región (o un Tipo DID móvil, que no las necesita).")
                return

        lst_groups_r.delete(0, tk.END)
        groups_data_r.clear()
        btn_comprar_random.config(state="disabled")
        r_status_var.set("Buscando prefijos...")

        def tarea():
            return store.get_did_groups(api_key(), country_id, city_id=city_id, region_id=region_id, type_id=type_id)

        def on_done(resultado, error):
            if error:
                r_status_var.set("")
                messagebox.showerror("Error", f"Error en búsqueda de prefijos: {error}")
                return
            groups_data_r.extend(resultado)
            for g in resultado:
                attrs = g.get("attributes") or {}
                prefix = attrs.get("prefix") or ""
                area = attrs.get("area_name") or ""
                lst_groups_r.insert(tk.END, f"{prefix} — {area}" if area else prefix)
            r_status_var.set("" if resultado else "No hay prefijos disponibles para esos filtros.")

        run_bg(tarea, on_done)

    def on_group_r_selected(event=None):
        btn_comprar_random.config(state="normal" if lst_groups_r.curselection() else "disabled")

    lst_groups_r.bind("<<ListboxSelect>>", on_group_r_selected)

    def comprar_random():
        sel = lst_groups_r.curselection()
        if not sel:
            return
        group = groups_data_r[sel[0]]
        texto = lst_groups_r.get(sel[0])
        qty = int(cmb_r_qty.get() or "1")

        if not messagebox.askyesno("Confirmar compra",
                                    f"¿Comprar {qty} número(s) aleatorio(s) del prefijo {texto}? "
                                    "Esta acción tiene costo real."):
            return

        btn_comprar_random.config(state="disabled")
        r_status_var.set("Comprando...")

        def tarea():
            sku_id = store.get_valid_sku_id(api_key(), group["id"])
            if not sku_id:
                raise RuntimeError("No se encontró un SKU válido con 2 canales para este grupo DID.")
            return store.create_order_random(api_key(), sku_id, qty)

        def on_done(resultado, error):
            r_status_var.set("")
            if error:
                messagebox.showerror("Error al comprar", str(error))
                btn_comprar_random.config(state="normal")
                return
            messagebox.showinfo("Compra realizada", f"✅ Se compraron {qty} número(s) correctamente.")

        run_bg(tarea, on_done)

    ttk.Button(frm_r, text="🔎  Buscar prefijos", command=buscar_prefijos_random, style="Accent.TButton").grid(
        row=0, column=4, rowspan=2, padx=(30, 0))

    btn_comprar_random = ttk.Button(frm_r, text="🛒  Comprar aleatorio", command=comprar_random,
                                     style="Accent.TButton", state="disabled")
    btn_comprar_random.grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))

    # ------------------------------------------------------------------
    # País/Tipo/Ciudad/Región: se puede escribir para filtrar (más fácil
    # de encontrar la opción que con listas largas de países/ciudades).
    # La cantidad (cmb_r_qty) se deja como estaba: son solo 5 opciones.
    # ------------------------------------------------------------------
    for _combo in (cmb_p_country, cmb_p_type, cmb_p_city, cmb_p_region,
                   cmb_r_country, cmb_r_type, cmb_r_city, cmb_r_region):
        hacer_combobox_buscable(_combo)

    # ------------------------------------------------------------------
    # Carga inicial de ambas sub-pestañas (países / tipos DID)
    # ------------------------------------------------------------------
    cargar_iniciales(cmb_p_country, cmb_p_type, p_status_var)
    cargar_iniciales(cmb_r_country, cmb_r_type, r_status_var)

    actualizar_saldo(manual=False)
    root.after(SALDO_REFRESH_MS, _auto_refresh_saldo)
