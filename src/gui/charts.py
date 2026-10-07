"""DPI-aware Qt painting; actual points only, explicit missing segments."""
import logging
import math
from PySide6.QtCore import Qt,QPointF,QRectF
from PySide6.QtGui import QColor,QPainter,QPen,QFont
from PySide6.QtWidgets import QWidget

COLORS = ['#42cef0','#ffa34e','#7ba7ff','#66d4a2','#cf9cf3']

class Chart(QWidget):
    def __init__(self,kind='line',parent=None):
        super().__init__(parent)
        self.kind=kind
        self.time_axis=False
        self.series=[]
        self.labels=[]
        self.message='暂无数据'
        self.setMinimumHeight(120)
        self.setMinimumWidth(180)
        self.setMouseTracking(True)

    def set_data(self,series,labels=None,message='暂无数据'):
        self.series=series
        self.labels=labels or []
        self.message=message
        self.update()

    def paintEvent(self,event):
        p=QPainter(self)
        try:
            self.draw(p)
        except Exception:
            logging.getLogger('phase11').exception('chart render error')
            p.setPen(QColor('#ffd16b'))
            p.drawText(self.rect(),Qt.AlignCenter,'CHART UNAVAILABLE')
        finally:
            p.end()

    def draw(self,p):
        p.setRenderHint(QPainter.Antialiasing)
        p.setFont(QFont('Microsoft YaHei UI',9))
        legend=[];legend_x=44;legend_y=13
        for index,(name,_,_) in enumerate(self.series):
            width=p.fontMetrics().horizontalAdvance(name)+20
            if legend_x+width>self.width()-10 and legend_x>44:legend_x=44;legend_y+=17
            legend.append((legend_x,legend_y,name,COLORS[index%len(COLORS)]));legend_x+=width
        plot=QRectF(44,legend_y+9,max(10,self.width()-58),max(20,self.height()-legend_y-42))
        values=[y for _,points,_ in self.series for x,y in points if y is not None and math.isfinite(y)]
        if not values:
            p.setPen(QColor('#8ca8be'))
            p.drawText(self.rect(),Qt.AlignCenter,self.message)
            return
        ymin,ymax=(0,max(values)*1.15) if self.kind=='bar' else (math.floor(min(values)-1),math.ceil(max(values)+1))
        ymax=max(ymin+1,ymax)
        xs=[x for _,points,_ in self.series for x,y in points]
        xmin,xmax=min(xs),max(xs)
        if self.kind=='bar':xmin-=.6;xmax+=.6
        xmax=max(xmin+1,xmax)
        def point(x,y):
            return QPointF(plot.left()+(x-xmin)/(xmax-xmin)*plot.width(),plot.bottom()-(y-ymin)/(ymax-ymin)*plot.height())
        if self.time_axis:
            from datetime import datetime,timedelta
            from .adapters import BJT,now
            day=datetime.fromtimestamp(xmin,BJT).replace(hour=0,minute=0,second=0,microsecond=0)
            today=now().astimezone(BJT).date()
            while day.timestamp()<xmax:
                left=max(xmin,day.timestamp());right=min(xmax,(day+timedelta(days=1)).timestamp())
                if right>left:
                    px=point(left,ymin).x();pr=point(right,ymin).x()
                    p.fillRect(QRectF(px,plot.top(),pr-px,plot.height()),QColor('#102a40' if day.day%2 else '#0a2034'))
                    p.setPen(QPen(QColor('#24506a'),1,Qt.DotLine));p.drawLine(QPointF(px,plot.top()),QPointF(px,plot.bottom()))
                    delta=(day.date()-today).days
                    title={0:'今天',1:'明天',2:'后天'}.get(delta,day.strftime('%m/%d'))
                    p.setPen(QColor('#527b96'));p.drawText(QRectF(px+5,plot.top()+3,max(0,pr-px-10),18),Qt.AlignLeft,title)
                day+=timedelta(days=1)
        p.setPen(QPen(QColor('#19354b'),1))
        for i in range(4):
            y=ymin+(ymax-ymin)*i/3
            py=point(xmin,y).y()
            p.drawLine(QPointF(plot.left(),py),QPointF(plot.right(),py))
            p.setPen(QColor('#8ca8be'))
            p.drawText(QRectF(0,py-9,39,20),Qt.AlignRight,f'{y:.0f}'+('%' if self.kind=='bar' else '°'))
            p.setPen(QPen(QColor('#19354b'),1))
        for lx,ly,name,color in legend:
            p.setPen(QColor(color));p.drawText(lx,ly,name)
        for index,(name,points,mode) in enumerate(self.series):
            color=COLORS[index%len(COLORS)]
            previous=None
            highest=max((y for x,y in points if y is not None),default=0)
            for point_index,(x,y) in enumerate(points):
                if y is None or not math.isfinite(y):
                    previous=None
                    continue
                q=point(x,y)
                if self.kind=='bar':
                    width=plot.width()/max(1,len(points))*0.66
                    p.fillRect(QRectF(q.x()-width/2,q.y(),width,plot.bottom()-q.y()),QColor('#ffa34e' if y==highest else '#268ec4'))
                    label_step=max(1,math.ceil(22/(plot.width()/max(1,len(points)))))
                    if (len(points)<=18 and point_index%label_step==0) or y==highest:
                        p.setFont(QFont('Microsoft YaHei UI',7 if len(points)>12 else 9))
                        p.setPen(QColor('#ffc476' if y==highest else '#a4cee6'))
                        offset=10 if len(points)>12 and point_index%2 else 0
                        p.drawText(QRectF(q.x()-25,q.y()-19-offset,50,18),Qt.AlignCenter,f'{y:.1f}%')
                        p.setFont(QFont('Microsoft YaHei UI',9))
                else:
                    color='#ffa34e' if mode=='marker' else color
                    p.setPen(QPen(QColor(color),2,Qt.DashLine if mode=='event' else Qt.SolidLine))
                    if previous and mode=='line':
                        p.drawLine(previous,q)
                    p.setBrush(QColor(color))
                    p.drawEllipse(q,3 if mode=='line' else 4,3 if mode=='line' else 4)
                    previous=q
                    if mode=='marker':
                        p.drawText(QRectF(q.x()-32,q.y()-23,64,20),Qt.AlignCenter,f'{y:.1f}°C')
        if self.time_axis:
            from .adapters import now
            current=now().timestamp()
            if xmin<=current<=xmax:
                x=point(current,ymin).x();p.setPen(QPen(QColor('#64b9de'),1,Qt.DashLine))
                p.drawLine(QPointF(x,plot.top()),QPointF(x,plot.bottom()))
                p.drawText(QRectF(x+4,plot.top()+21,60,18),Qt.AlignLeft,'NOW')
        p.setPen(QColor('#8ca8be'))
        labels=self.labels or [(x,str(x)) for x in sorted(set(xs))]
        step=max(1,math.ceil(len(labels)/max(2,plot.width()/(24 if self.kind=='bar' else 65))))
        for x,label in labels[::step]:
            left=max(0,min(self.width()-64,point(x,ymin).x()-32))
            p.drawText(QRectF(left,plot.bottom()+6,64,20),Qt.AlignCenter,label)

    def mouseMoveEvent(self,event):
        if not self.series:
            return
        values=[(name,x,y) for name,pts,_ in self.series for x,y in pts if y is not None]
        if values:
            xs=[x for _,x,_ in values]
            target=min(xs)+(event.position().x()-44)/max(1,self.width()-58)*(max(xs)-min(xs))
            name,x,y=min(values,key=lambda v:abs(v[1]-target))
            label=dict(self.labels).get(x,str(x))
            if self.time_axis:
                from datetime import datetime
                from .adapters import BJT
                label=datetime.fromtimestamp(x,BJT).strftime('%m-%d %H:%M BJT')
            self.setToolTip(f'{name} · {label}: {y:.3f}'+(' %' if self.kind=='bar' else ' °C'))
