"""Render the code-native SVG into a Windows ICO, using existing Qt only."""
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage,QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

def main():
    app=QApplication([])
    folder=Path(__file__).resolve().parents[1]/'assets'
    renderer=QSvgRenderer(str(folder/'zuuu.svg'))
    if not renderer.isValid():raise ValueError('ICON_SVG_INVALID')
    image=QImage(256,256,QImage.Format_ARGB32);image.fill(Qt.transparent)
    painter=QPainter(image);renderer.render(painter);painter.end()
    if not image.save(str(folder/'zuuu.ico'),'ICO'):raise ValueError('ICON_ICO_WRITE_FAILED')
    print('GUI icon generated')

if __name__=='__main__':main()
