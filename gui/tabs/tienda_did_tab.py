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

Los países/tipos/ciudades/regiones se buscan escribiendo -EditableComboBox
+ QCompleter (MatchContains) hacen el filtrado en vivo; reemplaza al
desplegable propio a medida (Toplevel + Listbox) que tenía la versión
ttk de este archivo, innecesario ahora que Qt ya trae esto resuelto."""
import threading
from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QStackedWidget, QCompleter
from qfluentwidgets import (
    EditableComboBox, ComboBox, PrimaryPushButton, PushButton, TransparentToolButton,
    BodyLabel, CaptionLabel, StrongBodyLabel, ListWidget, FluentIcon, SegmentedWidget,
)

from core import didww_store as store
from gui import dialogs as messagebox
from gui.widgets import fila_campo, columna, linea_divisoria

VERDE_OK = "#32d74b"
GRIS_ESTADO = "#9399b2"


def build(ctx):
    root = ctx.root
    config = ctx.config

    def api_key():
        return config.get("providers_config", {}).get("DIDWW", {}).get("api_key", "").strip()

    SALDO_REFRESH_MS = 30 * 60 * 1000  # 30 minutos

    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(15, 15, 15, 15)
    layout.setSpacing(10)

    # ------------------------------------------------------------------
    # Franja superior: modo (Número específico / Aleatorio) + Saldo DIDWW
    # ------------------------------------------------------------------
    top_row = QHBoxLayout()

    modo_pivot = SegmentedWidget()
    top_row.addWidget(modo_pivot)
    top_row.addStretch(1)

    saldo_label = StrongBodyLabel("Saldo DIDWW: —")
    btn_saldo_refresh = TransparentToolButton(FluentIcon.SYNC)
    btn_saldo_refresh.setFixedSize(26, 26)
    top_row.addWidget(saldo_label)
    top_row.addWidget(btn_saldo_refresh)
    layout.addLayout(top_row)

    saldo_estado_label = CaptionLabel("")
    saldo_estado_row = QHBoxLayout()
    saldo_estado_row.addStretch(1)
    saldo_estado_row.addWidget(saldo_estado_label)
    layout.addLayout(saldo_estado_row)

    # ------------------------------------------------------------------
    # Los dos modos apilados (igual que el tkraise() de la versión ttk):
    # un QStackedWidget con una página por modo, mismo lugar y ancho.
    # ------------------------------------------------------------------
    modo_stack = QStackedWidget()
    layout.addWidget(modo_stack, 1)

    tab_puntual = QWidget()
    tab_random = QWidget()
    modo_stack.addWidget(tab_puntual)
    modo_stack.addWidget(tab_random)

    modo_pivot.addItem(routeKey="puntual", text="Número específico", onClick=lambda: modo_stack.setCurrentWidget(tab_puntual))
    modo_pivot.addItem(routeKey="random", text="Aleatorio", onClick=lambda: modo_stack.setCurrentWidget(tab_random))
    modo_pivot.setCurrentItem("puntual")

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

    def nombre_attr(it):
        return (it.get("attributes") or {}).get("name") or "(sin nombre)"

    def set_combo_items(combo, items, text_fn):
        combo.clear()
        textos = []
        for it in items:
            texto = text_fn(it)
            combo.addItem(texto, userData=it)
            textos.append(texto)
        completer = QCompleter(textos, combo)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        combo.setCompleter(completer)
        combo.setCurrentIndex(-1)
        combo.clearEditText() if hasattr(combo, "clearEditText") else combo.setText("")

    def selected_item(combo):
        return combo.currentData()

    def selected_id(combo):
        item = selected_item(combo)
        return item["id"] if item else None

    def nueva_combo(ancho=220):
        combo = EditableComboBox()
        combo.setFixedWidth(ancho)
        combo.setPlaceholderText("Escribí para buscar...")
        return combo

    # ------------------------------------------------------------------
    # Saldo DIDWW (esquina superior): se refresca solo cada 30 minutos, y
    # también con el botón 🔄 (que fuerza una consulta ahora).
    # ------------------------------------------------------------------
    def actualizar_saldo(manual=False):
        if not api_key():
            saldo_label.setText("Saldo DIDWW: (falta API Key)")
            saldo_estado_label.setText("")
            return

        if manual:
            btn_saldo_refresh.setEnabled(False)
        saldo_estado_label.setStyleSheet(f"color: {GRIS_ESTADO};")
        saldo_estado_label.setText("Actualizando...")

        def tarea():
            return store.get_balance(api_key())

        def on_done(resultado, error):
            if manual:
                btn_saldo_refresh.setEnabled(True)
            if error:
                if isinstance(error, store.DidwwApiKeyError):
                    saldo_label.setText("Saldo DIDWW: ⚠️ API Key inválida")
                else:
                    saldo_label.setText("Saldo DIDWW: error al consultar")
                saldo_estado_label.setText("")
                return
            saldo_label.setText(f"Saldo DIDWW: $ {resultado:,.2f}")
            saldo_estado_label.setStyleSheet(f"color: {VERDE_OK};")
            saldo_estado_label.setText(f"✅ Actualizado a las {datetime.now().strftime('%H:%M')}")

        run_bg(tarea, on_done)

    def _auto_refresh_saldo():
        actualizar_saldo(manual=False)
        root.after(SALDO_REFRESH_MS, _auto_refresh_saldo)

    btn_saldo_refresh.clicked.connect(lambda: actualizar_saldo(manual=True))

    # ------------------------------------------------------------------
    # Carga inicial (países / tipos DID) — igual en ambas sub-pestañas
    # ------------------------------------------------------------------
    def cargar_iniciales(cmb_country, cmb_type, status_label):
        if not api_key():
            status_label.setText("⚠️ Falta configurar la API Key de DIDWW (Proveedores → DIDWW → Configurar).")
            return

        status_label.setText("Cargando países y tipos de DID...")

        def tarea():
            return store.get_countries(api_key()), store.get_did_group_types(api_key())

        def on_done(resultado, error):
            if error:
                status_label.setText(f"⚠️ Error al cargar países/tipos: {error}")
                return
            countries, types = resultado
            set_combo_items(cmb_country, countries, nombre_attr)
            set_combo_items(cmb_type, types, nombre_attr)
            status_label.setText("")

        run_bg(tarea, on_done)

    # ==================================================================
    # SUB-PESTAÑA: Número específico
    # ==================================================================
    layout_puntual = QVBoxLayout(tab_puntual)
    layout_puntual.setContentsMargins(0, 0, 0, 0)

    cmb_p_country = nueva_combo()
    cmb_p_type = nueva_combo()
    cmb_p_city = nueva_combo()
    cmb_p_region = nueva_combo()
    p_status_label = CaptionLabel("")
    p_status_label.setStyleSheet(f"color: {GRIS_ESTADO};")

    btn_buscar_p = PrimaryPushButton(FluentIcon.SEARCH, "Buscar prefijos")

    # Grilla con línea divisoria entre "País/Tipo DID" y "Ciudad/Región", y
    # el botón "Buscar prefijos" en su propia fila de abajo (alineado a la
    # izquierda, bajo País/Ciudad) en vez de flotando altísimo al lado
    # derecho de toda la grilla -reportado 2026-10-28, con una imagen
    # marcando exactamente este acomodo (cuadros/líneas amarillas, cuadro
    # rojo para el botón)-.
    busqueda_p_grid = QGridLayout()
    busqueda_p_grid.setHorizontalSpacing(12)
    busqueda_p_grid.setVerticalSpacing(10)
    busqueda_p_grid.addWidget(StrongBodyLabel("País:"), 0, 0)
    busqueda_p_grid.addWidget(cmb_p_country, 0, 1)
    busqueda_p_grid.addWidget(StrongBodyLabel("Tipo DID:"), 0, 2)
    busqueda_p_grid.addWidget(cmb_p_type, 0, 3)
    busqueda_p_grid.addWidget(linea_divisoria(), 1, 0, 1, 4)
    busqueda_p_grid.addWidget(StrongBodyLabel("Ciudad:"), 2, 0)
    busqueda_p_grid.addWidget(cmb_p_city, 2, 1)
    busqueda_p_grid.addWidget(StrongBodyLabel("Región:"), 2, 2)
    busqueda_p_grid.addWidget(cmb_p_region, 2, 3)
    busqueda_p_grid.addWidget(linea_divisoria(), 3, 0, 1, 4)
    busqueda_p_grid.addWidget(btn_buscar_p, 4, 0, 1, 2, Qt.AlignmentFlag.AlignLeft)
    busqueda_p_grid_widget = QWidget()
    busqueda_p_grid_widget.setLayout(busqueda_p_grid)

    buscar_p_card = ctx.seccion_plegable(
        FluentIcon.SEARCH, "Buscar números disponibles",
        columna(busqueda_p_grid_widget, p_status_label),
        "tienda_did.puntual_buscar",
    )
    layout_puntual.addWidget(buscar_p_card)

    def p_on_country_changed(*_a):
        cmb_p_city.setCurrentIndex(-1)
        cmb_p_region.setCurrentIndex(-1)
        country_id = selected_id(cmb_p_country)
        if not country_id:
            return

        def tarea():
            return store.get_cities(api_key(), country_id), store.get_regions(api_key(), country_id)

        def on_done(resultado, error):
            if error:
                p_status_label.setText(f"⚠️ Error al cargar ciudades/regiones: {error}")
                return
            cities, regions = resultado
            set_combo_items(cmb_p_city, cities, nombre_attr)
            set_combo_items(cmb_p_region, regions, nombre_attr)

        run_bg(tarea, on_done)

    def p_on_region_changed(*_a):
        # Región y ciudad son mutuamente excluyentes, igual que el original.
        if selected_id(cmb_p_region):
            cmb_p_city.setCurrentIndex(-1)
            cmb_p_city.setEnabled(False)
        else:
            cmb_p_city.setEnabled(True)

    cmb_p_country.currentIndexChanged.connect(p_on_country_changed)
    cmb_p_region.currentIndexChanged.connect(p_on_region_changed)

    lst_groups = ListWidget()
    lst_numbers = ListWidget()
    groups_data = []  # paralelo a lst_groups
    numbers_data = []  # paralelo a lst_numbers

    listas_grid = QGridLayout()
    listas_grid.addWidget(StrongBodyLabel("Prefijos encontrados:"), 0, 0)
    listas_grid.addWidget(StrongBodyLabel("Números disponibles:"), 0, 1)
    listas_grid.addWidget(lst_groups, 1, 0)
    listas_grid.addWidget(lst_numbers, 1, 1)
    listas_grid_widget = QWidget()
    listas_grid_widget.setLayout(listas_grid)
    layout_puntual.addWidget(listas_grid_widget, 1)

    def buscar_prefijos():
        if not api_key():
            messagebox.showerror("Falta la API Key", "Configura la API Key de DIDWW en Proveedores → DIDWW → Configurar.")
            return
        country_id = selected_id(cmb_p_country)
        type_id = selected_id(cmb_p_type)
        if not country_id or not type_id:
            messagebox.showinfo("Faltan datos", "Selecciona País y Tipo DID.")
            return
        city_id = selected_id(cmb_p_city) if cmb_p_city.isEnabled() else None
        region_id = selected_id(cmb_p_region)

        lst_groups.clear()
        lst_numbers.clear()
        groups_data.clear()
        numbers_data.clear()
        btn_comprar_puntual.setEnabled(False)
        p_status_label.setText("Buscando prefijos...")

        def tarea():
            return store.get_did_groups(api_key(), country_id, city_id=city_id, region_id=region_id, type_id=type_id)

        def on_done(resultado, error):
            if error:
                p_status_label.setText("")
                messagebox.showerror("Error", f"Error en búsqueda de prefijos: {error}")
                return
            groups_data.extend(resultado)
            for g in resultado:
                attrs = g.get("attributes") or {}
                prefix = attrs.get("prefix") or ""
                area = attrs.get("area_name") or ""
                texto = f"📍 {prefix}" + (f" — {area}" if area else "")
                lst_groups.addItem(texto)
            p_status_label.setText("" if resultado else "No hay prefijos disponibles para esos filtros.")

        run_bg(tarea, on_done)

    def on_group_selected():
        idx = lst_groups.currentRow()
        lst_numbers.clear()
        numbers_data.clear()
        btn_comprar_puntual.setEnabled(False)
        if idx < 0:
            return
        group_id = groups_data[idx]["id"]
        p_status_label.setText("Cargando numeraciones disponibles...")

        def tarea():
            return store.get_available_dids(api_key(), group_id)

        def on_done(resultado, error):
            if error:
                p_status_label.setText("")
                messagebox.showerror("Error", f"Error al cargar numeraciones: {error}")
                return
            numbers_data.extend(resultado)
            for n in resultado:
                numero = (n.get("attributes") or {}).get("number") or "(sin número)"
                lst_numbers.addItem(numero)
            p_status_label.setText("" if resultado else "No hay numeraciones disponibles para ese prefijo.")

        run_bg(tarea, on_done)

    def on_number_selected():
        btn_comprar_puntual.setEnabled(lst_numbers.currentRow() >= 0)

    lst_groups.currentRowChanged.connect(lambda _i: on_group_selected())
    lst_numbers.currentRowChanged.connect(lambda _i: on_number_selected())

    def comprar_puntual():
        idx_g = lst_groups.currentRow()
        idx_n = lst_numbers.currentRow()
        if idx_g < 0 or idx_n < 0:
            return
        group_id = groups_data[idx_g]["id"]
        did = numbers_data[idx_n]
        numero = (did.get("attributes") or {}).get("number") or did["id"]

        if not messagebox.askyesno("Confirmar compra", f"¿Comprar el número {numero}? Esta acción tiene costo real."):
            return

        btn_comprar_puntual.setEnabled(False)
        p_status_label.setText(f"Comprando {numero}...")

        def tarea():
            sku_id = store.get_valid_sku_id(api_key(), group_id)
            if not sku_id:
                raise RuntimeError("No se encontró un SKU válido con 2 canales para este grupo DID.")
            return store.create_order(api_key(), did["id"], sku_id)

        def on_done(resultado, error):
            p_status_label.setText("")
            if error:
                messagebox.showerror("Error al comprar", str(error))
                btn_comprar_puntual.setEnabled(True)
                return
            messagebox.showinfo("Compra realizada", f"✅ Se compró el número {numero} correctamente.")
            lst_numbers.clear()
            numbers_data.clear()

        run_bg(tarea, on_done)

    btn_buscar_p.clicked.connect(buscar_prefijos)
    btn_comprar_puntual = PrimaryPushButton(FluentIcon.SHOPPING_CART, "Comprar número seleccionado")
    btn_comprar_puntual.setEnabled(False)
    btn_comprar_puntual.clicked.connect(comprar_puntual)
    fila_comprar_p = QHBoxLayout()
    fila_comprar_p.addWidget(btn_comprar_puntual)
    fila_comprar_p.addStretch(1)
    layout_puntual.addLayout(fila_comprar_p)

    # ==================================================================
    # SUB-PESTAÑA: Aleatorio
    # ==================================================================
    layout_random = QVBoxLayout(tab_random)
    layout_random.setContentsMargins(0, 0, 0, 0)

    cmb_r_country = nueva_combo()
    cmb_r_type = nueva_combo()
    cmb_r_city = nueva_combo()
    cmb_r_region = nueva_combo()
    cmb_r_qty = ComboBox()
    cmb_r_qty.addItems(["1", "2", "5", "10", "20"])
    cmb_r_qty.setCurrentIndex(0)
    r_status_label = CaptionLabel("")
    r_status_label.setStyleSheet(f"color: {GRIS_ESTADO};")

    btn_buscar_r = PrimaryPushButton(FluentIcon.SEARCH, "Buscar prefijos")

    # Mismo acomodo que busqueda_p_grid más arriba: línea divisoria entre
    # cada fila y el botón abajo, alineado a la izquierda.
    busqueda_r_grid = QGridLayout()
    busqueda_r_grid.setHorizontalSpacing(12)
    busqueda_r_grid.setVerticalSpacing(10)
    busqueda_r_grid.addWidget(StrongBodyLabel("País:"), 0, 0)
    busqueda_r_grid.addWidget(cmb_r_country, 0, 1)
    busqueda_r_grid.addWidget(StrongBodyLabel("Tipo DID:"), 0, 2)
    busqueda_r_grid.addWidget(cmb_r_type, 0, 3)
    busqueda_r_grid.addWidget(linea_divisoria(), 1, 0, 1, 4)
    busqueda_r_grid.addWidget(StrongBodyLabel("Ciudad:"), 2, 0)
    busqueda_r_grid.addWidget(cmb_r_city, 2, 1)
    busqueda_r_grid.addWidget(StrongBodyLabel("Región:"), 2, 2)
    busqueda_r_grid.addWidget(cmb_r_region, 2, 3)
    busqueda_r_grid.addWidget(linea_divisoria(), 3, 0, 1, 4)
    busqueda_r_grid.addWidget(StrongBodyLabel("Cantidad:"), 4, 0)
    busqueda_r_grid.addWidget(cmb_r_qty, 4, 1)
    busqueda_r_grid.addWidget(linea_divisoria(), 5, 0, 1, 4)
    busqueda_r_grid.addWidget(btn_buscar_r, 6, 0, 1, 2, Qt.AlignmentFlag.AlignLeft)
    busqueda_r_grid_widget = QWidget()
    busqueda_r_grid_widget.setLayout(busqueda_r_grid)

    buscar_r_card = ctx.seccion_plegable(
        FluentIcon.SEARCH, "Buscar prefijos para compra aleatoria",
        columna(busqueda_r_grid_widget, r_status_label),
        "tienda_did.random_buscar",
    )
    layout_random.addWidget(buscar_r_card)

    def _es_movil(combo_type):
        item = selected_item(combo_type)
        return bool(item) and "mobile" in nombre_attr(item).lower()

    def r_on_country_changed(*_a):
        cmb_r_city.setCurrentIndex(-1)
        cmb_r_region.setCurrentIndex(-1)
        country_id = selected_id(cmb_r_country)
        if not country_id:
            return

        def tarea():
            return store.get_cities(api_key(), country_id), store.get_regions(api_key(), country_id)

        def on_done(resultado, error):
            if error:
                r_status_label.setText(f"⚠️ Error al cargar ciudades/regiones: {error}")
                return
            cities, regions = resultado
            set_combo_items(cmb_r_city, cities, nombre_attr)
            set_combo_items(cmb_r_region, regions, nombre_attr)

        run_bg(tarea, on_done)

    def r_on_region_changed(*_a):
        if selected_id(cmb_r_region):
            cmb_r_city.setCurrentIndex(-1)
            cmb_r_city.setEnabled(False)
        else:
            cmb_r_city.setEnabled(True)

    cmb_r_country.currentIndexChanged.connect(r_on_country_changed)
    cmb_r_region.currentIndexChanged.connect(r_on_region_changed)

    lst_groups_r = ListWidget()
    groups_data_r = []
    layout_random.addWidget(StrongBodyLabel("Prefijos encontrados:"))
    layout_random.addWidget(lst_groups_r, 1)

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
            city_id = selected_id(cmb_r_city) if cmb_r_city.isEnabled() else None
            region_id = selected_id(cmb_r_region)
            if not city_id and not region_id:
                messagebox.showinfo("Faltan datos", "Selecciona Ciudad o Región (o un Tipo DID móvil, que no las necesita).")
                return

        lst_groups_r.clear()
        groups_data_r.clear()
        btn_comprar_random.setEnabled(False)
        r_status_label.setText("Buscando prefijos...")

        def tarea():
            return store.get_did_groups(api_key(), country_id, city_id=city_id, region_id=region_id, type_id=type_id)

        def on_done(resultado, error):
            if error:
                r_status_label.setText("")
                messagebox.showerror("Error", f"Error en búsqueda de prefijos: {error}")
                return
            groups_data_r.extend(resultado)
            for g in resultado:
                attrs = g.get("attributes") or {}
                prefix = attrs.get("prefix") or ""
                area = attrs.get("area_name") or ""
                lst_groups_r.addItem(f"{prefix} — {area}" if area else prefix)
            r_status_label.setText("" if resultado else "No hay prefijos disponibles para esos filtros.")

        run_bg(tarea, on_done)

    def on_group_r_selected():
        btn_comprar_random.setEnabled(lst_groups_r.currentRow() >= 0)

    lst_groups_r.currentRowChanged.connect(lambda _i: on_group_r_selected())

    def comprar_random():
        idx = lst_groups_r.currentRow()
        if idx < 0:
            return
        group = groups_data_r[idx]
        texto = lst_groups_r.item(idx).text()
        qty = int(cmb_r_qty.currentText() or "1")

        if not messagebox.askyesno("Confirmar compra",
                                    f"¿Comprar {qty} número(s) aleatorio(s) del prefijo {texto}? "
                                    "Esta acción tiene costo real."):
            return

        btn_comprar_random.setEnabled(False)
        r_status_label.setText("Comprando...")

        def tarea():
            sku_id = store.get_valid_sku_id(api_key(), group["id"])
            if not sku_id:
                raise RuntimeError("No se encontró un SKU válido con 2 canales para este grupo DID.")
            return store.create_order_random(api_key(), sku_id, qty)

        def on_done(resultado, error):
            r_status_label.setText("")
            if error:
                messagebox.showerror("Error al comprar", str(error))
                btn_comprar_random.setEnabled(True)
                return
            messagebox.showinfo("Compra realizada", f"✅ Se compraron {qty} número(s) correctamente.")

        run_bg(tarea, on_done)

    btn_buscar_r.clicked.connect(buscar_prefijos_random)
    btn_comprar_random = PrimaryPushButton(FluentIcon.SHOPPING_CART, "Comprar aleatorio")
    btn_comprar_random.setEnabled(False)
    btn_comprar_random.clicked.connect(comprar_random)
    fila_comprar_r = QHBoxLayout()
    fila_comprar_r.addWidget(btn_comprar_random)
    fila_comprar_r.addStretch(1)
    layout_random.addLayout(fila_comprar_r)

    # ------------------------------------------------------------------
    # Carga inicial de ambas sub-pestañas (países / tipos DID)
    # ------------------------------------------------------------------
    cargar_iniciales(cmb_p_country, cmb_p_type, p_status_label)
    cargar_iniciales(cmb_r_country, cmb_r_type, r_status_label)

    actualizar_saldo(manual=False)
    root.after(SALDO_REFRESH_MS, _auto_refresh_saldo)

    return page
