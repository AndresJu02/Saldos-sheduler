"""Helpers de layout chiquitos, reusados por varias pestañas para no
repetir el mismo armado de QHBoxLayout en cada campo "Etiqueta: [campo]"
(con o sin botón de ayuda "?" adelante)."""
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QFrame
from qfluentwidgets import StrongBodyLabel, CaptionLabel, SingleDirectionScrollArea


def linea_divisoria(color="#3a3a3f"):
    """Línea horizontal de 1px, para separar filas dentro de una misma
    tarjeta/grilla (ej. entre "País/Tipo DID" y "Ciudad/Región" en Tienda
    DID, reportado 2026-10-28)."""
    linea = QFrame()
    linea.setFixedHeight(1)
    linea.setStyleSheet(f"background-color: {color}; border: none;")
    return linea


def fila_campo(texto_label, widget, ayuda=None, extra=None, ancho_label=None, ancho_icono=None):
    """Una fila horizontal: [ayuda?] [Etiqueta:] [widget] [extra?] [...stretch].

    `ancho_label`/`ancho_icono` son opcionales: fijan el ancho del label y
    del hueco del ícono de ayuda, para que varias filas seguidas (con
    textos de etiqueta de distinta longitud) terminen con su campo
    arrancando en la misma columna -ej. la sección "Validación" de Cargas
    VOIP, reportado 2026-10-28 como "los números no quedan alineados"-. Si
    `ancho_icono` se pasa pero la fila no tiene `ayuda`, se reserva un
    espacio invisible de ese ancho, para que el label arranque igual que en
    las filas que sí tienen ícono."""
    fila = QWidget()
    h = QHBoxLayout(fila)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(10)
    if ancho_icono is not None:
        if ayuda is not None:
            ayuda.setFixedWidth(ancho_icono)
            h.addWidget(ayuda)
        else:
            espacio = QWidget()
            espacio.setFixedWidth(ancho_icono)
            h.addWidget(espacio)
    elif ayuda is not None:
        h.addWidget(ayuda)
    lbl = StrongBodyLabel(texto_label)
    if ancho_label is not None:
        lbl.setFixedWidth(ancho_label)
    h.addWidget(lbl)
    h.addWidget(widget)
    if extra is not None:
        h.addWidget(extra)
    h.addStretch(1)
    return fila


def texto_informativo(texto):
    """Línea de texto gris chico, tipo "ℹ El Webhook ya se configura en Configuración" -informativa, no interactiva."""
    lbl = CaptionLabel(texto)
    lbl.setWordWrap(True)
    lbl.setStyleSheet("color: #9399b2;")
    return lbl


def columna(*widgets, spacing=10, margins=(16, 12, 16, 16)):
    """QWidget con un QVBoxLayout conteniendo `widgets` en orden -el
    contenido típico de una tarjeta plegable (ctx.seccion_plegable)."""
    contenedor = QWidget()
    v = QVBoxLayout(contenedor)
    v.setContentsMargins(*margins)
    v.setSpacing(spacing)
    for w in widgets:
        v.addWidget(w)
    return contenedor


def pintar_boton_peligro(btn, color="#ff453a"):
    """Pone el texto/ícono de un PushButton en rojo (para acciones
    destructivas, ej. "Eliminar proveedor") sin romperle la posición del
    ícono. OJO: un setStyleSheet() cualquiera sobre un PushButton de
    QFluentWidgets le cambia el minimumSizeHint() (probado: de 165x32 pasa
    a 131x29, porque Qt empieza a resolver el tamaño vía QStyleSheetStyle
    en vez del cálculo propio de PushButton) -y el ícono se dibuja
    tomando ESE tamaño como referencia (ver PushButton.paintEvent), así
    que terminaba corrido/pisando el texto (reportado 2026-10-28, dos
    veces: primero quitándole el estilo entero, ahora arreglándolo bien).
    Se soluciona: (1) reescribiendo el mismo padding que ya trae
    PushButton[hasIcon=true] -si no, se pierde al poner un stylesheet
    propio- y (2) fijando minimumSizeHint() de vuelta al valor de ANTES
    de pintarlo, para que el ícono se calcule igual que en cualquier otro
    botón del panel.

    Un PushButton SIN ícono (ej. "Salir") es un caso aparte: el fondo
    redondeado de un PushButton normal lo dibuja su paintEvent en código
    -no CSS-, pero SOLO cuando hasIcon=true; sin ícono, PushButton se
    pinta como texto plano sin caja alrededor (confirmado en pantalla:
    "Salir" quedaba con el texto rojo flotando solo, sin fondo ni borde,
    reportado 2026-10-28, ANTES de tocarle nada -no es algo que esta
    función rompa-). Para que se vea como el resto de los botones
    secundarios del panel, acá se le agrega fondo/borde/radio a mano
    (BUTTON_BG, el mismo gris ya pensado para "botones secundarios
    planos" en gui/theme.py, aunque no se usaba en ningún lado todavía).

    Con ícono, además se alinea a la izquierda (no centrado) con el mismo
    truco de minimumSizeHint() "dinámico" de alinear_boton_izquierda() más
    abajo -"Eliminar proveedor" es uno de los botones del panel lateral de
    Proveedores que deben quedar alineados entre sí desde la izquierda
    (reportado 2026-10-28); con el minimumSizeHint() fijo de antes, su
    ícono quedaba en una columna distinta a la de los demás botones del
    panel, ya pasados por alinear_boton_izquierda()."""
    con_icono = not btn.icon().isNull()
    if not con_icono:
        mw_original = btn.minimumSizeHint()
        btn.setStyleSheet(
            f"PushButton{{color:{color}; background-color:#3a3a3c; "
            "border:1px solid #585b70; border-radius:8px; padding: 5px 12px 6px 12px;} "
            "PushButton:hover{background-color:#48484a;}"
        )
        btn.minimumSizeHint = lambda mw=mw_original: mw
        return

    altura_original = btn.minimumSizeHint().height()
    btn.setStyleSheet(f"PushButton{{color:{color}; text-align:left; padding: 5px 12px 6px 36px;}}")
    btn.minimumSizeHint = lambda: QSize(btn.width(), altura_original)


def alinear_boton_izquierda(btn):
    """Alinea el ícono+texto de un PushButton a la izquierda en vez de
    centrado -por defecto QFluentWidgets centra todo dentro del ancho
    completo del botón, así que en un panel de botones del mismo ancho fijo
    (ej. el panel lateral de Proveedores) cada ícono termina en una
    posición distinta según lo largo que sea su propio texto, en vez de
    quedar todos alineados en la misma columna (reportado 2026-10-28).

    OJO: PushButton.paintEvent centra el ÍCONO según
    `12 + (ancho_botón - minimumSizeHint().width()) // 2` -ver
    pintar_boton_peligro() más arriba sobre por qué un setStyleSheet()
    cualquiera ya le cambia ese minimumSizeHint()-. Para que el ícono quede
    SIEMPRE pegado a la izquierda (no solo el texto, vía text-align) sin
    importar el ancho real que termine teniendo el botón, acá
    minimumSizeHint() se deja devolviendo el ancho ACTUAL del botón en vez
    de un valor fijo -así esa resta siempre da 0-."""
    altura_original = btn.minimumSizeHint().height()
    con_icono = not btn.icon().isNull()
    padding = "5px 12px 6px 36px" if con_icono else "5px 12px 6px 12px"
    btn.setStyleSheet(f"PushButton{{text-align:left; padding: {padding};}}")
    btn.minimumSizeHint = lambda: QSize(btn.width(), altura_original)


def pagina_con_scroll(bg):
    """Para pestañas con varias tarjetas plegables que no siempre caben
    sin agrandar la ventana (Proveedores, Cargas VOIP, Configuración):
    arma una página con scroll vertical, igual que el canvas+scrollbar de
    la versión ttk -sin esto, al expandir una tarjeta el resto del
    contenido se aprieta/superpone en vez de scrollear (reportado
    2026-10-28)-.

    Devuelve (page, layout): `page` es lo que hay que devolver desde
    build(ctx) tal cual; `layout` es el QVBoxLayout donde agregar el
    contenido real de la pestaña (tarjetas, botones, etc.), exactamente
    como si fuera el layout de una página común."""
    page = QWidget()
    outer = QVBoxLayout(page)
    outer.setContentsMargins(0, 0, 0, 0)

    scroll = SingleDirectionScrollArea(orient=Qt.Orientation.Vertical)
    scroll.setWidgetResizable(True)
    scroll.setStyleSheet(f"QScrollArea{{border:none; background:{bg};}}")

    content = QWidget()
    content.setStyleSheet(f"background:{bg};")
    layout = QVBoxLayout(content)
    layout.setContentsMargins(15, 15, 15, 15)
    layout.setSpacing(10)

    scroll.setWidget(content)
    outer.addWidget(scroll)
    return page, layout
