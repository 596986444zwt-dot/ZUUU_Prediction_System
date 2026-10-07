"""Passive terminal widgets. Decorative runway is vector art, never weather evidence."""
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QPainter, QColor, QPen, QLinearGradient, QFont
from PySide6.QtWidgets import QFrame, QLabel, QGridLayout

class TerminalHeader(QFrame):
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        gradient = QLinearGradient(0, 0, self.width(), 0)
        gradient.setColorAt(0, QColor('#0b2540'))
        gradient.setColorAt(1, QColor('#071421'))
        p.fillRect(self.rect(), gradient)
        # Runway and tower occupy the empty middle/right background at low contrast.
        x = self.width() * .69
        p.setPen(QPen(QColor('#164665'), 1))
        p.drawLine(QPointF(x-140,self.height()),QPointF(x+5,14))
        p.drawLine(QPointF(x+190,self.height()),QPointF(x+35,14))
        p.setPen(QPen(QColor('#215779'), 2, Qt.DashLine))
        p.drawLine(QPointF(x+22,22),QPointF(x+22,self.height()))
        p.setPen(QPen(QColor('#1b4660'), 1))
        p.drawRect(QRectF(x+110,35,18,45))
        p.drawRect(QRectF(x+103,24,32,12))
        p.drawLine(QPointF(x+119,24),QPointF(x+119,10))
        p.setPen(QPen(QColor('#2674a1'), 1))
        p.drawLine(0,self.height()-1,self.width(),self.height()-1)
        p.end()

class MetricRows(QFrame):
    def __init__(self, names, columns=2):
        super().__init__()
        self.setObjectName('metrics')
        grid = QGridLayout(self)
        grid.setContentsMargins(0,0,0,0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(4)
        self.fields = {}
        for i, name in enumerate(names):
            key = QLabel(name); key.setObjectName('muted')
            val = QLabel('--'); val.setWordWrap(True); val.setObjectName('metricValue')
            row, col = divmod(i, columns)
            grid.addWidget(key,row*2,col)
            grid.addWidget(val,row*2+1,col)
            self.fields[name] = val
        for col in range(columns):grid.setColumnStretch(col,1)

    def update_values(self, values):
        for key, widget in self.fields.items():widget.setText(str(values.get(key,'--')))

class StageRoute(QFrame):
    stages = [('EXPERIMENTAL','N < 30'),('FIRST EVALUATION','N ≥ 30'),
              ('CALIBRATION RESEARCH','N ≥ 60'),('FORMAL CANDIDATE','N ≥ 90'),
              ('LONG-TERM STABILITY','N ≥ 180')]
    def __init__(self):
        super().__init__();self.n=0;self.setMinimumHeight(94)
        self.setToolTip('30 / 60 / 90 / 180 仅为数据门槛。研究、候选与正式模型身份均须后台独立授权。')

    def set_count(self,n):self.n=n;self.update()

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        current=sum(self.n>=x for x in (30,60,90,180))
        p.setFont(QFont('Microsoft YaHei UI',8))
        for i,(title,threshold) in enumerate(self.stages):
            y=9+i*19
            if i<4:
                p.setPen(QPen(QColor('#27475d'),1));p.drawLine(8,y+5,8,y+20)
            p.setPen(Qt.NoPen);p.setBrush(QColor('#ffbe5c' if i==current else '#28546c' if i<current else '#1b3446'))
            p.drawEllipse(QPointF(8,y),4,4)
            p.setPen(QColor('#ffd16b' if i==current else '#80a3bc' if i<current else '#516c83'))
            p.drawText(QRectF(21,y-9,max(10,self.width()-110),20),Qt.AlignVCenter,title)
            p.drawText(QRectF(self.width()-84,y-9,80,20),Qt.AlignRight|Qt.AlignVCenter,threshold)
        p.end()
