"""Normal Windows launch geometry; no fullscreen or automatic maximization."""
from PySide6.QtCore import QSize,QPoint

def initial_geometry(available,preferred=QSize(1600,900)):
    size=QSize(preferred)
    # Leave room for native Windows frame/title bar.
    if size.width()+16>available.width() or size.height()+40>available.height():
        size=QSize(int(available.width()*.92),int(available.height()*.93))
    position=QPoint(available.x()+(available.width()-size.width())//2,
                   available.y()+(available.height()-size.height()-40)//2)
    return size,position

def show_normal(window):
    size,position=initial_geometry(window.screen().availableGeometry())
    window.resize(size);window.move(position);window.show()
