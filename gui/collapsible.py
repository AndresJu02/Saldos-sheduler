"""
Secciones plegables: una tarjeta (QFluentWidgets ExpandGroupSettingCard)
con esquinas redondeadas de verdad, encabezado con ícono + título, y una
flechita que despliega/contrae el contenido -para no tener que scrollear
entre secciones que no se están usando en el momento-.

Uso típico en una pestaña:

    contenido = QWidget()
    layout = QGridLayout(contenido)
    layout.addWidget(QLabel("Chat ID:"), 0, 0)
    ...
    card = ctx.seccion_plegable(FluentIcon.LINK, "Chat de Bitrix24", contenido, "cargas_voip.conexion")
    layout_de_la_pestaña.addWidget(card)

Las secciones "▶  Monitor" quedan A PROPÓSITO fuera de esto -el pedido
explícito fue que esas NO se puedan plegar-: siguen siendo un
HeaderCardWidget o CardWidget común y corriente en cada pestaña.

NOTA 2026-10-28: se intentó redondear más el borde de estas tarjetas (el
que trae la librería por defecto es un outline de 6px, apenas visible) de
tres formas distintas -QSS con border-radius solo, QSS con
background-color + WA_StyledBackground, y envolviendo todo en un QFrame
con setMask()-. Las dos primeras no tuvieron ningún efecto visible, y la
de setMask() rompió la animación de expandir/contraer (la sección quedaba
colapsada sola y aparecía un recuadro suelto en otra parte de la
pantalla). Se descartaron las tres -esta función quedó como estaba
originalmente- para no dejar ese bug; si se retoma, probar por el lado de
parchear ExpandBorderWidget en vez de pelear con QSS o con setMask().
"""
from qfluentwidgets import ExpandGroupSettingCard, SingleDirectionScrollArea


def crear_seccion_plegable(icon, titulo, content_widget, abierta_por_defecto=True, on_toggle=None):
    card = ExpandGroupSettingCard(icon, titulo)
    card.addGroupWidget(content_widget)

    # ExpandSettingCard (la clase base de ExpandGroupSettingCard) es en
    # realidad un QScrollArea -lo usa para animar expandir/contraer, no
    # para scrollear contenido real- y sobreescribe wheelEvent() con un
    # simple "pass". Pero un wheelEvent que no hace nada TAMPOCO deja pasar
    # el evento al padre: la rueda del mouse dejaba de funcionar apenas el
    # puntero entraba al contenido de una tarjeta expandida -había que
    # subirlo hasta la franja del encabezado (fuera del área de scroll
    # interna de la tarjeta) para que la página volviera a scrollear-
    # (reportado 2026-10-28, en Configuración y en Horarios de ejecución
    # -cualquier sección armada con esta función-). Se reenvía el evento a
    # mano al SingleDirectionScrollArea de la página (ver pagina_con_scroll
    # en gui/widgets.py), reusando su propio scroll suave.
    def _wheelEvent(e, card=card):
        area = card.parentWidget()
        while area is not None and not isinstance(area, SingleDirectionScrollArea):
            area = area.parentWidget()
        if area is not None:
            area.wheelEvent(e)
        e.accept()
    card.wheelEvent = _wheelEvent

    if on_toggle is not None:
        # toggleExpand() (conectado a expandButton.clicked en __init__ de
        # ExpandSettingCard) ya corrió y actualizó card.isExpand antes de
        # que se dispare este segundo listener -las conexiones a la misma
        # señal corren en el orden en que se conectaron-, así que leerlo
        # acá adentro ya da el estado NUEVO.
        card.card.expandButton.clicked.connect(lambda: on_toggle(card.isExpand))

    if abierta_por_defecto:
        card.setExpand(True)

    return card
