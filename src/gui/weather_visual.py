"""Local neutral weather illustrations, never weather forecasts."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter,QColor,QPen
from PySide6.QtWidgets import QWidget,QLabel,QHBoxLayout,QProgressBar

class WeatherIcon(QWidget):
    def __init__(self,h):
        super().__init__();self.h=h;self.kind={'T0':'sun','T1':'partly','T2':'cloud'}[h];self.decorative_only=True;self.setFixedSize(65,68)
        self.setToolTip('中性天气视觉装饰，不代表晴、云或降水预测。')
    def set_evidence(self,hours,day):
        from .adapters import utc,BJT
        points=[r for r in hours if utc(r['target_time_utc']).astimezone(BJT).date().isoformat()==day and isinstance(r.get('temperature_2m_c'),(int,float))]
        peak=max(points,key=lambda r:r['temperature_2m_c']) if points else {}
        cloud=peak.get('cloud_cover_pct');rain=peak.get('precipitation_mm')
        self.decorative_only=not isinstance(cloud,(int,float)) or not isinstance(rain,(int,float))
        if self.decorative_only:
            self.kind={'T0':'sun','T1':'partly','T2':'cloud'}[self.h]
            self.setToolTip('中性天气视觉装饰；当前缺少对应时点的云量或降水证据，不代表天气预测。')
        else:
            self.kind='rain' if rain>0 else 'sun' if cloud<20 else 'partly' if cloud<80 else 'cloud'
            self.setToolTip('图标依据目标日ECMWF温度峰值时刻的真实云量及降水，仅作该时点辅助显示，不代表全天正式天气预测。\n北京时间：'+utc(peak['target_time_utc']).astimezone(BJT).strftime('%m月%d日 %H:%M')+f'\n云量：{cloud:.1f}% · 降水：{rain:.2f}毫米')
        self.update()
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        if self.kind in ('sun','partly'):
            p.setPen(QPen(QColor('#ffc45c'),3));p.setBrush(QColor('#ffc45c'))
            center=(32,33) if self.kind=='sun' else (23,24)
            p.save();p.translate(*center)
            for angle in range(0,360,45):
                p.save();p.rotate(angle);p.drawLine(0,-22,0,-28);p.restore()
            p.drawEllipse(-14,-14,28,28);p.restore()
        if self.kind in ('partly','cloud','rain'):
            p.setPen(Qt.NoPen);p.setBrush(QColor('#c1e4ff'))
            p.drawEllipse(9,33,27,24);p.drawEllipse(22,23,30,33);p.drawEllipse(39,36,23,21)
            p.drawRoundedRect(17,39,39,18,8,8)
            if self.kind=='rain':
                p.setPen(QPen(QColor('#45bdf0'),2))
                for x in (22,36,50):p.drawLine(x,60,x-3,66)
        p.end()

class ProbabilityRow(QWidget):
    def __init__(self,rank):
        super().__init__();self.rank=rank;self.probability=None
        box=QHBoxLayout(self);box.setContentsMargins(0,0,0,0);box.setSpacing(8)
        self.temperature=QLabel('--');self.temperature.setFixedWidth(48)
        self.bar=QProgressBar();self.bar.setRange(0,10000);self.bar.setTextVisible(False)
        self.bar.setFixedHeight(16);self.bar.setMinimumWidth(45);self.bar.setMaximumWidth(140)
        self.percent=QLabel('--');self.percent.setFixedWidth(53);self.percent.setAlignment(Qt.AlignRight|Qt.AlignVCenter)
        box.addWidget(self.temperature);box.addWidget(self.bar,1);box.addWidget(self.percent)
        color='#ffad5c' if rank==0 else '#349dde'
        self.bar.setStyleSheet('QProgressBar{background:#19384b;border:0;border-radius:4px;min-height:16px;max-height:16px;} QProgressBar::chunk{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 '+color+',stop:1 '+('#ffbd74' if rank==0 else '#65c6ff')+');border-radius:4px;}')
        self.temperature.setStyleSheet('font-size:14px;');self.percent.setStyleSheet('font-size:14px;color:'+color+';font-weight:600;')
    def set_probability(self,item):
        self.probability=item[1] if item else None
        self.temperature.setText(f'{item[0]}°C' if item else '--')
        self.percent.setText(f'{item[1]*100:.1f}%' if item else '--')
        self.bar.setValue(round(item[1]*10000) if item else 0)
        self.setToolTip('真实整数温度概率：'+self.temperature.text()+' · '+self.percent.text())
