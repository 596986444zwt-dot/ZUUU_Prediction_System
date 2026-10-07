"""Reference-driven Chinese dashboard; independent Qt presentation process only."""
import json,logging,math
from datetime import datetime,timedelta
from PySide6.QtCore import Qt,QTimer,QThread,Signal,QRectF,QPointF
from PySide6.QtGui import QPainter,QColor,QLinearGradient,QPixmap,QPen,QFont
from PySide6.QtWidgets import (QMainWindow,QWidget,QFrame,QLabel,QVBoxLayout,QHBoxLayout,QGridLayout,
 QScrollArea,QComboBox,QPushButton,QStackedWidget,QProgressBar,QTableWidget,QTableWidgetItem,QHeaderView,QSpinBox,QApplication)
from .adapters import Adapter,ROOT,fmt,age,freshness,now,utc,BJT
from .paths import resource_path
from .chinese import zh,clock,duration,date_title,targets
from .v2_theme import STYLE
from .weather_visual import WeatherIcon,ProbabilityRow

PAGES=['首页总览','72小时预测','逐小时详情','每日最高温','概率分布','ZUUU实况','ECMWF预报','历史对比','模型表现','T0成长状态','数据状态','系统设置']
def text(value='',name=None):
    w=QLabel(value);w.setTextFormat(Qt.PlainText)
    if name:w.setObjectName(name)
    return w
def metric(value):return '--' if value is None else fmt(value,'°C')

class Header(QFrame):
    def __init__(self,image_path=None):
        super().__init__();self.setFixedHeight(110)
        self.image_path=image_path or resource_path('resources/images/header_airport.png')
        self.image=QPixmap(str(self.image_path))
        if self.image.isNull():logging.getLogger('phase11').warning('HEADER_IMAGE_LOAD_FAILED %s',self.image_path)
        layout=QHBoxLayout(self);layout.setContentsMargins(22,6,20,6)
        left=QVBoxLayout();left.setSpacing(4);title=text('ZUUU 成都双流国际机场温度预测系统');title.setStyleSheet('font-size:30px;font-weight:700;color:#f3f8ff;')
        left.addWidget(title);left.addWidget(text('成都双流国际机场 · 智能温度预测   V1.0（实验运行）','muted'));layout.addLayout(left,1)
        self.controls=QHBoxLayout();left.addLayout(self.controls)
        self.status_panel=QFrame();self.status_panel.setObjectName('headerStatus');right=QVBoxLayout(self.status_panel);right.setContentsMargins(12,5,12,5);right.setSpacing(2);self.time=text();self.time.setStyleSheet('font-size:16px;font-weight:700;')
        self.state=text('正在读取后台状态');self.soak=text('72小时连续运行测试：正在读取')
        self.refresh=text('只读运行 · 正在读取','muted')
        self.elapsed=text('已运行：正在读取','muted')
        for w in (self.time,self.state,self.soak,self.elapsed):right.addWidget(w)
        self.actions=QHBoxLayout();self.actions.addWidget(self.refresh,1);right.addLayout(self.actions)
        layout.addWidget(self.status_panel)
    def paintEvent(self,event):
        p=QPainter(self);r=self.rect()
        if not self.image.isNull():
            scaled=self.image.scaled(r.size(),Qt.KeepAspectRatioByExpanding,Qt.SmoothTransformation)
            # Bottom-biased cover retains terminal, aircraft and runway without distortion.
            p.drawPixmap(0,0,scaled,0,min(scaled.height()-r.height(),int(scaled.height()*.60)),r.width(),r.height())
        else:
            g=QLinearGradient(0,0,self.width(),0);g.setColorAt(0,QColor('#05223b'));g.setColorAt(1,QColor('#14384d'));p.fillRect(r,g)
        g=QLinearGradient(0,0,self.width(),0)
        for pos,color in ((0,QColor(2,17,34,195)),(.45,QColor(2,17,34,95)),(.7,QColor(2,17,34,45)),(1,QColor(2,17,34,150))):g.setColorAt(pos,color)
        p.fillRect(r,g);p.end()

class Panel(QFrame):
    def __init__(self,title,orange=False):
        super().__init__();self.setObjectName('t0panel' if orange else 'panel')
        self.box=QVBoxLayout(self);self.box.setContentsMargins(14,8,14,8);self.box.setSpacing(4)
        self.heading=text(title,'title');self.box.addWidget(self.heading)

class ForecastCard(Panel):
    def __init__(self,h):
        super().__init__('',h=='T0');self.h=h;self.setFixedHeight(252)
        self.setObjectName({'T0':'t0panel','T1':'t1panel','T2':'t2panel'}[h])
        self.badge=text('正在读取','muted');self.box.removeWidget(self.heading)
        titles=QHBoxLayout();titles.addWidget(self.heading,1);titles.addWidget(self.badge)
        self.box.insertLayout(0,titles)
        self.badge.setStyleSheet('font-size:12px;color:#ffcc80;background:#523b25;padding:5px;border-radius:12px;' if h=='T0' else 'font-size:12px;color:#a8d9ff;background:#143d60;padding:5px;border-radius:12px;' if h=='T1' else 'font-size:12px;color:#d7b4ff;background:#392950;padding:5px;border-radius:12px;')
        row=QHBoxLayout();row.setSpacing(14);left=QVBoxLayout();hero=QHBoxLayout();hero.setSpacing(8);self.icon=WeatherIcon(h);hero.addWidget(self.icon);self.value=text('--','value');hero.addWidget(self.value,1);left.addLayout(hero)
        self.caption=text('今日最高温预测' if h=='T0' else '最高温预测','muted');left.addWidget(self.caption);left.addStretch();self.center=text('','warning')
        if h!='T0':left.addWidget(self.center)
        row.addLayout(left,1);self.divider=QFrame();self.divider.setFixedWidth(1);self.divider.setStyleSheet('background:#28516a;border:0;');row.addWidget(self.divider);self.rows=[];right=QVBoxLayout();right.setSpacing(10)
        if h!='T0':right.addWidget(text('整数温度概率 TOP 3','muted'))
        for i in range(5 if h=='T0' else 3):
            l=text('--') if h=='T0' else ProbabilityRow(i)
            if h=='T0':l.setStyleSheet('font-size:15px;');l.setWordWrap(True)
            right.addWidget(l);self.rows.append(l)
        row.addLayout(right,1);self.box.addLayout(row,1)
        self.footer=text('正在读取数据','warning');self.footer.setWordWrap(True);self.box.addWidget(self.footer)
    def resizeEvent(self,event):
        if event is not None:super().resizeEvent(event)
        pixels=64 if self.width()>=560 else 46
        if self.value.text()=='暂无预测':pixels=28
        self.value.setStyleSheet(f'font-size:{pixels}px;color:#ffad5c;font-weight:700;')

class Graph(QWidget):
    """Qt painter charts: missing points break lines; mode controls point shape."""
    def __init__(self,kind='line',time_axis=False):
        super().__init__();self.kind=kind;self.time_axis=time_axis;self.series=[];self.labels=[];self.message='暂无数据';self.current=now();self.coords=[]
        self.setMinimumSize(160,130);self.setMouseTracking(True);self.x_bounds=None
    def set_data(self,series,labels=None,message='暂无数据'):
        self.series=series;self.labels=labels or [];self.message=message;self.update()
    def paintEvent(self,event):
        p=QPainter(self)
        try:self.draw(p)
        except Exception:
            logging.getLogger('phase11').exception('GUI_CHART_ERROR');p.setPen(QColor('#ffd16b'));p.drawText(self.rect(),Qt.AlignCenter,'图表暂不可用')
        finally:p.end()
    def draw(self,p):
        p.setRenderHint(QPainter.Antialiasing);p.setFont(QFont('Microsoft YaHei UI',9));self.coords=[]
        legend=[];x=44;y=14
        for name,pts,mode,color in self.series:
            if name in [v[2] for v in legend]:continue
            width=p.fontMetrics().horizontalAdvance(name)+24
            if x+width>self.width()-10:x=44;y+=20
            legend.append((x,y,name,color));x+=width
        for x,y,name,col in legend:
            if self.kind=='bar':continue
            p.setPen(QPen(QColor(col),2));p.drawLine(x,y-4,x+12,y-4);p.setPen(QColor(col));p.drawText(x+17,y,name)
        if self.kind=='bar':
            # The panel title already identifies this single series. Give the
            # PMF its own readable plot area instead of a redundant legend.
            y=0
        plot=QRectF(46,y+22,self.width()-60,max(20,self.height()-y-64))
        points=[(x,v) for _,pts,_,_ in self.series for x,v in pts if v is not None and math.isfinite(v)]
        if not points:p.setPen(QColor('#9cbed3'));p.drawText(plot,Qt.AlignCenter,self.message);return
        xs=[x for _,pts,_,_ in self.series for x,v in pts];xmin,xmax=self.x_bounds or (min(xs),max(xs))
        if self.kind=='bar':xmin-=.65;xmax+=.65
        xmax=max(xmin+1,xmax);ys=[v for x,v in points]
        lo,hi=(0,max(ys)*1.25) if self.kind=='bar' else (math.floor(min(ys)-2),math.ceil(max(ys)+2));hi=max(lo+1,hi)
        def pt(x,v):return QPointF(plot.left()+(x-xmin)/(xmax-xmin)*plot.width(),plot.bottom()-(v-lo)/(hi-lo)*plot.height())
        if self.time_axis:
            day=datetime.fromtimestamp(xmin,BJT).replace(hour=0,minute=0,second=0,microsecond=0)
            today=self.current.astimezone(BJT).date()
            while day.timestamp()<xmax:
                l=max(xmin,day.timestamp());r=min(xmax,(day+timedelta(days=1)).timestamp())
                if r>l:
                    px=pt(l,lo).x();pr=pt(r,lo).x();p.fillRect(QRectF(px,plot.top(),pr-px,plot.height()),QColor('#0b2c43' if day.day%2 else '#0a2439'))
                    p.setPen(QPen(QColor('#47768d'),1,Qt.DashLine));p.drawLine(QPointF(px,plot.top()),QPointF(px,plot.bottom()))
                    delta=(day.date()-today).days;name={0:'今天',1:'明天',2:'后天'}.get(delta,'')
                    p.setPen(QColor('#b1d4e9'));p.drawText(QRectF(px+5,plot.top()+3,pr-px-8,20),Qt.AlignLeft,f'{name} {day.month}月{day.day}日')
                day+=timedelta(days=1)
            # Civil clock bands only, not astronomical sunrise/sunset or weather claims.
            band=datetime.fromtimestamp(xmin,BJT).replace(hour=0,minute=0,second=0,microsecond=0)
            while band.timestamp()<xmax:
                for start_hour,end_hour,daylight in ((0,6,False),(6,18,True),(18,24,False)):
                    start=max(xmin,(band+timedelta(hours=start_hour)).timestamp());end=min(xmax,(band+timedelta(hours=end_hour)).timestamp())
                    if end>start:
                        px=pt(start,lo).x();pr=pt(end,lo).x()
                        p.fillRect(QRectF(px,plot.top(),pr-px,plot.height()),QColor(112,137,156,12) if daylight else QColor(0,9,25,35))
                band+=timedelta(days=1)
        if getattr(self,'intraday',False):
            t=self.current.astimezone(BJT);hour=t.hour+t.minute/60+t.second/3600
            if hour<xmax:
                future_x=pt(max(xmin,hour),lo).x()
                p.fillRect(QRectF(future_x,plot.top(),plot.right()-future_x,plot.height()),QColor(0,7,22,65))
                p.setPen(QColor('#a5b5ca'));p.drawText(QRectF(future_x+8,plot.top()+3,max(10,plot.right()-future_x-16),18),Qt.AlignRight,'未来时段（尚未到达）')
        tick_count=3 if getattr(self,'compact',False) else 5
        for i in range(tick_count):
            v=lo+(hi-lo)*i/(tick_count-1);yy=pt(xmin,v).y();p.setPen(QPen(QColor('#234258'),1));p.drawLine(QPointF(plot.left(),yy),QPointF(plot.right(),yy));p.setPen(QColor('#a3bed0'));p.drawText(QRectF(0,yy-9,39,18),Qt.AlignRight,f'{v:.0f}')
        for name,pts,mode,col in self.series:
            prev=None;peak=max((v for x,v in pts if v is not None),default=0)
            for point_index,(x,v) in enumerate(pts):
                if v is None or not math.isfinite(v):prev=None;continue
                q=pt(x,v);self.coords.append((q,name,x,v))
                if self.kind=='bar':
                    width=plot.width()/max(1,len(pts))*.66;p.fillRect(QRectF(q.x()-width/2,q.y(),width,plot.bottom()-q.y()),QColor('#ffad5c' if v==peak else '#2b90d4'))
                    label_step=max(1,math.ceil(48/(plot.width()/max(1,len(pts)))))
                    peak_x=next((xx for xx,vv in pts if vv==peak),x)
                    near_peak=abs(pt(peak_x,peak).x()-q.x())<48
                    if v==peak or (point_index%label_step==0 and not near_peak):
                        p.setPen(QColor('#ffe1ad' if v==peak else '#b4d4ed'));p.drawText(QRectF(q.x()-24,q.y()-19,48,18),Qt.AlignCenter,f'{v:.1f}%')
                else:
                    p.setPen(QPen(QColor(col),2))
                    if prev is not None and mode=='line':p.drawLine(prev,q)
                    p.setBrush(QColor('#082239') if mode=='line' else QColor(col));p.drawEllipse(q,3.5 if mode=='line' else 4.5,3.5 if mode=='line' else 4.5);prev=q
                    if mode=='marker':p.drawText(QRectF(q.x()-34,q.y()-23,68,18),Qt.AlignCenter,f'{v:.1f}°C')
        p.setPen(QColor('#9bbdd3'))
        labels=self.labels or [(x,str(x)) for x in sorted(set(xs))];step=max(1,math.ceil(len(labels)/max(2,plot.width()/70)))
        for x,s in labels[::step]:p.drawText(QRectF(max(0,min(self.width()-72,pt(x,lo).x()-36)),plot.bottom()+5,72,22),Qt.AlignCenter,s)
        p.drawText(QRectF(plot.left(),self.height()-19,plot.width(),18),Qt.AlignCenter,'整数温度（°C）' if self.kind=='bar' else '北京时间（BJT）')
        p.save();p.translate(12,plot.center().y());p.rotate(-90);p.drawText(QRectF(-60,-10,120,20),Qt.AlignCenter,'概率（%）' if self.kind=='bar' else '温度（°C）');p.restore()
        if self.time_axis and xmin<=self.current.timestamp()<=xmax:
            q=pt(self.current.timestamp(),lo);p.setPen(QPen(QColor('#67d5fa'),2,Qt.DashLine));p.drawLine(QPointF(q.x(),plot.top()),q);p.drawText(QRectF(min(q.x()+4,plot.right()-115),plot.top()+23,115,18),'当前 '+self.current.astimezone(BJT).strftime('%H:%M'))
        if getattr(self,'intraday',False):
            t=self.current.astimezone(BJT);hour=t.hour+t.minute/60+t.second/3600
            label_x=min(max(plot.left(),pt(hour,lo).x()-58),plot.right()-122)
            if xmin<=hour<=xmax:
                px=pt(hour,lo).x();p.setPen(QPen(QColor('#ff9474'),2,Qt.DashLine));p.drawLine(QPointF(px,plot.top()),QPointF(px,plot.bottom()))
            p.setPen(QColor('#ffb39e'));p.drawText(QRectF(label_x,plot.top()-20,122,18),Qt.AlignCenter,'当前时间 '+t.strftime('%H:%M'))
    def mouseMoveEvent(self,event):
        if self.coords:
            _,name,x,v=min(self.coords,key=lambda r:(r[0]-event.position()).manhattanLength())
            stamp=clock(datetime.fromtimestamp(x,BJT).isoformat()) if self.time_axis else dict(self.labels).get(x,f'{x:g}')
            if self.kind=='line' and not self.time_axis:
                seconds=round(x*3600);stamp=f'{seconds//3600:02}:{seconds//60%60:02}:{seconds%60:02} 北京时间'
            self.setToolTip(f'{name} · {stamp}：{v:.3f}'+('％' if self.kind=='bar' else '°C'))

class Reader(QThread):
    ready=Signal(dict)
    def __init__(self,adapter,parent):super().__init__(parent);self.adapter=adapter
    def run(self):
        try:self.ready.emit(self.adapter.read())
        except Exception:logging.getLogger('phase11').exception('GUI_READ_ERROR');self.ready.emit({'errors':{'all':'读取暂不可用'}})

class Window(QMainWindow):
    loaded=Signal()
    def __init__(self,root=ROOT,autorefresh=True):
        super().__init__();self.adapter=Adapter(root);self.data={};self.reader=None;self.refresh_count=0;self.interval=45;self.selected='T1';self.last_dates=None;self.fresh_states={}
        self.setWindowTitle('ZUUU 成都双流国际机场温度预测系统 · 只读运行');self.setMinimumSize(1100,720)
        QApplication.instance().setStyleSheet(STYLE);QApplication.instance().setFont(QFont('Microsoft YaHei UI',9))
        outer=QWidget();box=QVBoxLayout(outer);box.setContentsMargins(10,6,10,12);box.setSpacing(8);self.setCentralWidget(outer)
        self.header=Header();box.addWidget(self.header)
        self.nav=QComboBox();self.nav.addItems(PAGES);self.nav.setFixedHeight(24);self.nav.setFixedWidth(114)
        self.quick_navigation={}
        for title,index in (('首页总览',0),('模型信息',8),('实时数据',10),('系统状态',10)):
            button=QPushButton(title);button.setFixedHeight(26);button.clicked.connect(lambda checked,i=index:self.nav.setCurrentIndex(i));self.header.controls.addWidget(button);self.quick_navigation[title]=button
        self.header.controls.addWidget(self.nav);self.header.controls.addStretch()
        self.refresh_text=self.header.refresh
        self.refresh_button=QPushButton('刷新数据');self.refresh_button.setFixedHeight(22);self.header.actions.addWidget(self.refresh_button)
        self.pages=QStackedWidget();box.addWidget(self.pages,1);scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff);scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        dashboard=QWidget();grid=QGridLayout(dashboard);grid.setContentsMargins(0,0,0,0);grid.setSpacing(8);self.home_grid=grid;self.dashboard=dashboard
        for col,stretch in enumerate((37,33,30)):grid.setColumnStretch(col,stretch)
        self.cards={h:ForecastCard(h) for h in ('T0','T1','T2')};self.t0=self.cards['T0'];self.t1=self.cards['T1'];self.t2=self.cards['T2']
        core=QWidget();core.setFixedHeight(252);corebox=QHBoxLayout(core);corebox.setContentsMargins(0,0,0,0);corebox.setSpacing(12)
        for h in self.cards:corebox.addWidget(self.cards[h],1)
        grid.addWidget(core,0,0,1,3);self.core=core
        self.trend=Panel('未来72小时温度趋势');self.trend_dates=text('ZUUU实况 + ECMWF预报 · 每日预测最高温','muted');self.trend.box.addWidget(self.trend_dates);self.trajectory=Graph(time_axis=True);self.trend.box.addWidget(self.trajectory,1);grid.addWidget(self.trend,1,0,1,2)
        self.prob=Panel('最高温整数概率分布');switch=QHBoxLayout();self.prob_buttons={}
        for h in self.cards:
            b=QPushButton();b.setCheckable(True);b.clicked.connect(lambda checked,key=h:self.select_probability(key));switch.addWidget(b);self.prob_buttons[h]=b
        self.prob.box.addLayout(switch);self.prob_chart=Graph('bar');self.prob.box.addWidget(self.prob_chart,1);self.prob_note=text('暂无数据','muted');self.prob.box.addWidget(self.prob_note);grid.addWidget(self.prob,1,2)
        self.evolution=Panel('T0 日内预测演化');self.evolution.box.addWidget(text('○ 定时预测    ● 事件触发 / 启动触发','muted'));self.t0chart=Graph();self.evolution.box.addWidget(self.t0chart,1);grid.addWidget(self.evolution,2,0,1,2)
        self.models=Panel('模型信息');self.models.box.setAlignment(Qt.AlignTop);self.model_table=QTableWidget(4,4);self.model_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff);self.model_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.model_table.verticalHeader().hide();self.model_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch);self.model_table.setEditTriggers(QTableWidget.NoEditTriggers);self.model_table.setSelectionMode(QTableWidget.NoSelection);self.model_table.setFixedHeight(148);self.models.box.addWidget(self.model_table)
        self.growth=text('T0实验模型成长：正在读取','muted');self.growth.setWordWrap(True);self.models.box.addWidget(self.growth);self.growthbar=QProgressBar();self.models.box.addWidget(self.growthbar)
        right=QWidget();rightbox=QVBoxLayout(right);rightbox.setContentsMargins(0,0,0,0);rightbox.setSpacing(10)
        self.realtime=Panel('实时数据状态');self.obs=text();self.obs.setWordWrap(True);self.ecmwf=text();self.ecmwf.setWordWrap(True)
        self.obs.setParent(self.realtime);self.obs.hide();self.ecmwf.setParent(self.realtime);self.ecmwf.hide()
        self.obs_primary=text();self.obs_primary.setStyleSheet('font-size:16px;font-weight:700;');self.obs_secondary=text('','muted');self.obs_secondary.setWordWrap(True)
        self.ecmwf_primary=text();self.ecmwf_primary.setStyleSheet('font-size:15px;font-weight:600;');self.ecmwf_secondary=text('','muted');self.ecmwf_secondary.setWordWrap(True)
        for widget in (self.obs_primary,self.obs_secondary,self.ecmwf_primary,self.ecmwf_secondary):self.realtime.box.addWidget(widget)
        rightbox.addWidget(self.realtime,3)
        self.system=Panel('系统运行状态');self.workers=text();self.workers.setParent(self.system);self.workers.hide();self.worker_lines={}
        for key,name in (('t0','T0后台任务'),('formal','T+1/T+2后台任务')):
            line=QHBoxLayout();label=text(name);state=text();heartbeat=text('','muted');line.addWidget(label);line.addWidget(state);line.addStretch();line.addWidget(heartbeat);self.system.box.addLayout(line);self.worker_lines[key]=(state,heartbeat)
        self.soak=text();self.soak.setWordWrap(True);self.soakbar=QProgressBar();self.system.box.addWidget(self.soak);self.system.box.addWidget(self.soakbar);rightbox.addWidget(self.system,2)
        self.summary=Panel('运行状态摘要');self.summary_labels={}
        for key,name in (('ZUUU','ZUUU实况'),('ECMWF','ECMWF数值预报'),('t0','T0后台任务'),('formal','T+1/T+2后台任务')):
            name={'ZUUU':'ZUUU实况（METAR）','t0':'T0后台任务（实验模型）'}.get(key,name)
            line=QHBoxLayout();line.addWidget(text('● '+name));value=text('正在读取');line.addStretch();line.addWidget(value);self.summary.box.addLayout(line);self.summary_labels[key]=value
            if key=='ZUUU':self.summary_obs_detail=text('','muted');self.summary.box.addWidget(self.summary_obs_detail)
            if key=='ECMWF':
                self.summary_run_detail=text('','muted');self.summary_request=text('','warning');self.summary.box.addWidget(self.summary_run_detail);self.summary.box.addWidget(self.summary_request)
        self.summary_soak=text('连续运行测试：正在读取','muted');self.summary.box.addWidget(self.summary_soak);self.summary_progress=QProgressBar();self.summary.box.addWidget(self.summary_progress)
        detail_button=QPushButton('运行详情');detail_button.setFixedHeight(22);detail_button.clicked.connect(lambda:self.nav.setCurrentIndex(10))
        self.summary.box.removeWidget(self.summary.heading);summary_title=QHBoxLayout();summary_title.addWidget(self.summary.heading,1);summary_title.addWidget(detail_button);self.summary.box.insertLayout(0,summary_title)
        grid.addWidget(self.summary,2,2)
        for row,stretch in enumerate((0,58,42)):grid.setRowStretch(row,stretch)
        scroll.setWidget(dashboard);self.pages.addWidget(scroll);self.scroll=scroll;self.details={}
        for i,title in enumerate(PAGES[1:],1):
            page=Panel(title);body=text('正在读取');body.setWordWrap(True);body.setAlignment(Qt.AlignTop);page.box.addWidget(body,1)
            self.details[i]=body
            if i==11:
                self.refresh_spin=QSpinBox();self.refresh_spin.setRange(30,300);self.refresh_spin.setValue(self.interval);self.refresh_spin.setSuffix('秒');page.box.addWidget(text('界面刷新间隔（仅影响显示）'));page.box.addWidget(self.refresh_spin)
                self.refresh_spin.valueChanged.connect(self.set_interval)
            self.pages.addWidget(page)
        model_page=self.pages.widget(8);model_page.box.insertWidget(1,self.models);self.details[8].setMaximumHeight(100)
        data_page=self.pages.widget(10);data_page.box.insertWidget(1,right);self.details[10].hide()
        self.layout_mode=None;self.apply_height_mode()
        self.nav.currentIndexChanged.connect(self.pages.setCurrentIndex);self.refresh_button.clicked.connect(self.refresh)
        self.timer=QTimer(self);self.timer.timeout.connect(self.refresh)
        if autorefresh:self.timer.start(self.interval*1000)
        self.tick=QTimer(self);self.tick.timeout.connect(self.update_clock);self.tick.start(1000)
        QTimer.singleShot(0,self.refresh)
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'summary'):self.apply_height_mode()
    def apply_height_mode(self):
        compact=self.height()<900;self.layout_mode='COMPACT_LAYOUT' if compact else 'NORMAL_LAYOUT'
        self.header.setFixedHeight(112 if compact else 140)
        self.home_grid.setSpacing(6 if compact else 12)
        for panel in (self.trend,self.prob,self.evolution,self.summary):
            panel.box.setContentsMargins(12,5 if compact else 9,12,5 if compact else 9);panel.box.setSpacing(2 if compact else 5)
        intermediate=not compact and self.height()<980
        if intermediate:
            # Preserve cards and typography; reclaim secondary panel padding.
            for panel in (self.trend,self.prob,self.evolution,self.summary):
                panel.box.setContentsMargins(12,6,12,6);panel.box.setSpacing(3)
        graph_min=90 if compact else 105 if intermediate else 150
        self.trajectory.setMinimumHeight(graph_min);self.prob_chart.setMinimumHeight(graph_min);self.t0chart.setMinimumHeight(105 if compact or intermediate else 130);self.t0chart.compact=compact
        self.prob_chart.compact=compact
        tight=self.height()<790;self.summary_obs_detail.setVisible(not tight);self.summary_run_detail.setVisible(not tight)
        for button in self.prob_buttons.values():
            button.setFixedHeight(30 if compact else 42)
            title=getattr(button,'full_title','')
            if title:
                button.setText(title if compact else title.replace('｜','\n'))
                button.setStyleSheet('font-size:12px;padding:3px 4px;')
        self.trend_dates.setVisible(not compact)
        self.summary_progress.setFixedHeight(12 if compact else 16)
        self.summary.setMinimumHeight(0);self.evolution.setMinimumHeight(0)
    def set_interval(self,value):self.interval=value;self.timer.setInterval(value*1000)
    def refresh(self):
        if self.reader and self.reader.isRunning():return
        if self.reader:self.reader.deleteLater()
        self.reader=Reader(self.adapter,self);self.reader.ready.connect(self.render);self.reader.start()
    def update_clock(self):
        current=now();self.header.time.setText(clock(current.isoformat(),True)+' · 北京时间')
        for graph in (self.trajectory,self.t0chart):graph.current=current;graph.update()
        remaining=max(0,self.timer.remainingTime()//1000) if self.timer.isActive() else 0
        self.refresh_text.setText('只读运行 · '+(f'下次刷新：{remaining//60:02}:{remaining%60:02}' if self.timer.isActive() else '自动刷新已暂停'))
        days=targets(current)
        if self.last_dates and days!=self.last_dates:
            # Clear previous-day presentation immediately, then fetch matching target records.
            self.render({},current=current);self.refresh()
    def render(self,data,current=None):
        current=current or now();self.display_current=current;self.data=data;self.refresh_count+=1;self.last_dates=targets(current)
        f=data.get('formal',{});t=data.get('t0',{});s=data.get('soak',{});pred=f.get('predictions',{})
        for h,card in self.cards.items():
            r=pred.get(h,{}) if h!='T0' else {};snap=r.get('snapshot',{}) if h!='T0' else t.get('snapshot',{})
            title,mismatch=date_title(h,snap.get('target_date') if h=='T0' else snap.get('target_business_date'),current)
            card.heading.setText(title);valid=not mismatch
            try:card.icon.set_evidence(f.get('hours',[]),snap.get('target_date') if h=='T0' else snap.get('target_business_date') or self.last_dates[h])
            except Exception:
                logging.getLogger('phase11').exception('GUI_WEATHER_ICON_UNAVAILABLE');card.icon.set_evidence([],None)
            btn_title,_=date_title(h,snap.get('target_date') if h=='T0' else snap.get('target_business_date'),current);button=self.prob_buttons[h];button.full_title=btn_title;button.setToolTip(btn_title)
            button.setText(btn_title if self.layout_mode=='COMPACT_LAYOUT' else btn_title.replace('｜','\n'))
            card.setToolTip('预测目标：'+(snap.get('target_date') if h=='T0' else snap.get('target_business_date') or self.last_dates[h] or '--')+'\n更新时间：'+clock(snap.get('prediction_time') if h=='T0' else snap.get('prediction_issue_time'))+'\n状态：'+zh(snap.get('status')) if snap else '当前目标日尚未生成预测；保留真实日期。')
            if h=='T0':
                outputs={r['method']:r for r in snap.get('outputs',[])} if valid else {};value=outputs.get('LEVEL0',{}).get('prediction');card.value.setText(f'{value:.1f}°C' if value is not None else '--');card.badge.setText('实验模型')
                values=[('当前实况',metric(t.get('observation',{}).get('temperature_c'))),('观测时间',clock(t.get('observation',{}).get('observation_time'))),('今日已观测最高温',metric(snap.get('tmax_so_far') if valid else None)),('基础预测（Level0）',metric(value)),('实验修正预测（L1_A）',metric(outputs.get('L1_A',{}).get('prediction')))]
                for label,(name,v) in zip(card.rows,values):label.setText(f'{name}  {v}')
                card.footer.setText('概率尚未校准 · 暂不提供正式概率分布')
            else:
                pmf=r.get('pmf',{});top=pmf.get('top',[]) if valid else [];model=r.get('model',{}).get('model_family');card.badge.setText((model+'模型') if model else '模型身份尚无数据')
                card.value.setText(f'{top[0][0]}°C' if top else '暂无预测');card.resizeEvent(None)
                for i,label in enumerate(card.rows):
                    label.set_probability(top[i] if len(top)>i else None)
                error=r.get('error',{});reason=error.get('reason') if error.get('time') and utc(error['time']).astimezone(BJT).date()==current.astimezone(BJT).date() else None
                note=zh(reason) if not top and reason else zh(pmf.get('status'))
                card.center.setText('连续预测中心：'+metric(r.get('continuous',{}).get('continuous_prediction_c') if valid else None))
                card.footer.setText(f"{note} · {zh(snap.get('status')) if snap else '暂无预测'}")
        for callback,args,panel,title in ((self.render_trend,(f,t,current),self.trend,'未来72小时温度趋势'),(self.render_evolution,(t,),self.evolution,'T0 日内预测演化'),(self.render_model,(f,t),self.models,'模型信息'),(self.render_sources,(f,t,s),self.realtime,'实时数据状态')):
            try:
                callback(*args);panel.heading.setText(title)
            except Exception:
                logging.getLogger('phase11').exception('GUI_MODULE_RENDER_UNAVAILABLE %s',title);panel.heading.setText(title+' · 暂不可用')
        self.select_probability(self.selected);self.update_details();self.update_clock()
        if data.get('errors'):
            self.refresh_text.setText('读取暂不可用 · 保留上次成功数据');self.refresh_text.setToolTip('部分数据读取失败；后台继续运行，界面使用已有缓存。')
        self.loaded.emit()
    def select_probability(self,h):
        self.selected=h
        for key,b in self.prob_buttons.items():b.setChecked(key==h)
        if h=='T0':self.prob_chart.set_data([],message='T0概率尚未校准\n暂不提供正式概率分布');self.prob_note.setText('实验模型不提供正式概率');return
        r=self.data.get('formal',{}).get('predictions',{}).get(h,{});p=r.get('pmf',{});snap=r.get('snapshot',{})
        _,bad=date_title(h,snap.get('target_business_date'),getattr(self,'display_current',None))
        bars=[] if bad else p.get('bars',[])
        self.prob_chart.set_data([('整数最高温概率',bars,'bar','#2b90d4')],[(x,str(x)) for x,y in bars],message='日期不一致，暂停展示概率' if bad else zh(p.get('status')))
        self.prob_note.setText(f"{zh(p.get('status'))} · 概率总和：{p['total']:.3f}" if p.get('total') is not None and not bad else '暂无正式概率分布')
    def render_trend(self,f,t,current):
        start=current.astimezone(BJT).replace(hour=0,minute=0,second=0,microsecond=0).timestamp();end=start+72*3600
        hours=[r for r in f.get('hours',[]) if start<=utc(r['target_time_utc']).timestamp()<end];obs=[r for r in f.get('observations',[]) if start<=utc(r['observation_time']).timestamp()<end];markers=[]
        self.trajectory.x_bounds=(start,end)
        self.trend_dates.setText('ZUUU实况 + ECMWF预报 · '+ ' / '.join(date_title(h,None,current)[0].replace(' T0','').replace(' T+1','').replace(' T+2','') for h in ('T0','T1','T2')))
        for h,r in f.get('predictions',{}).items():
            snap=r.get('snapshot',{});day=snap.get('target_business_date');_,bad=date_title(h,day,current)
            value=r.get('continuous',{}).get('continuous_prediction_c')
            curve=[r for r in hours if utc(r['target_time_utc']).astimezone(BJT).date().isoformat()==day and r.get('temperature_2m_c') is not None]
            if not bad and curve and value is not None:
                peak=max(curve,key=lambda r:r['temperature_2m_c']);markers.append((utc(peak['target_time_utc']).timestamp(),value))
        snap=t.get('snapshot',{});out={r['method']:r for r in snap.get('outputs',[])};day=snap.get('target_date');curve=[r for r in hours if utc(r['target_time_utc']).astimezone(BJT).date().isoformat()==day and r.get('temperature_2m_c') is not None]
        if curve and day==self.last_dates['T0'] and out.get('LEVEL0',{}).get('prediction') is not None:markers.append((utc(max(curve,key=lambda r:r['temperature_2m_c'])['target_time_utc']).timestamp(),out['LEVEL0']['prediction']))
        series=[('ZUUU实况',[(utc(r['observation_time']).timestamp(),r.get('temperature_c')) for r in obs],'line','#ffad5c'),('ECMWF小时预报',[(utc(r['target_time_utc']).timestamp(),r.get('temperature_2m_c')) for r in hours],'line','#35c2ed'),('每日预测最高温',markers,'marker','#ffad5c')]
        self.trajectory.current=current;labels=[(start+i*12*3600,datetime.fromtimestamp(start+i*12*3600,BJT).strftime('%m/%d %H时')) for i in range(7)]
        self.trajectory.set_data(series,labels);self.trajectory.setToolTip('每日最高温标记的位置借用ECMWF当日峰值时刻作展示锚点；模型不预测最高温发生时间，也不提供逐小时机器学习轨迹。')
    def render_evolution(self,t):
        self.t0chart.intraday=True;self.t0chart.x_bounds=(8,18);self.t0chart.current=getattr(self,'display_current',now())
        snapshots=[s for s in t.get('snapshots',[]) if s.get('target_date')==self.last_dates['T0']]
        scheduled={s['cutoff_bjt']:s for s in snapshots if s['trigger_type']=='SCHEDULED'};series=[]
        for name,method,col in [('今日已观测最高温',None,'#50bcea'),('基础预测（Level0）','LEVEL0','#ffad5c'),('实验修正预测（L1_A）','L1_A','#c285f1')]:
            def value(s):return s.get('tmax_so_far') if method is None else next((r.get('prediction') for r in s.get('outputs',[]) if r['method']==method),None)
            series.append((name,[(h,value(scheduled.get(f'{h:02}:00',{}))) for h in (8,10,12,14,16,18)],'line',col))
            events=[]
            for s in snapshots:
                if s['trigger_type']=='SCHEDULED':continue
                tm=utc(s['prediction_time']).astimezone(BJT);events.append((tm.hour+tm.minute/60+tm.second/3600,value(s)))
            series.append((name,events,'event',col))
        self.t0chart.set_data(series,[(h,f'{h:02}:00') for h in (8,10,12,14,16,18)],'今日尚未生成T0预测')
    def render_model(self,f,t):
        table=self.model_table;table.setHorizontalHeaderLabels(['项目','T0 今天','T+1 明天','T+2 后天']);snap=t.get('snapshot',{});out={r['method']:r for r in snap.get('outputs',[])} if snap.get('target_date')==self.last_dates['T0'] else {}
        rows=[['使用模型','实验模型'],['模型状态',zh(t.get('growth',{}).get('stage'))],['预测中心',metric(out.get('LEVEL0',{}).get('prediction'))],['整数主预测','--']]
        for h in ('T1','T2'):
            r=f.get('predictions',{}).get(h,{});p=r.get('pmf',{});_,bad=date_title(h,r.get('snapshot',{}).get('target_business_date'),getattr(self,'display_current',None))
            model=r.get('model',{}).get('model_family');vals=[(model+'模型') if model else '暂无身份',zh(p.get('status')),metric(r.get('continuous',{}).get('continuous_prediction_c') if not bad else None),f"{p['top'][0][0]}°C" if p.get('top') and not bad else '--']
            for row,v in zip(rows,vals):row.append(v)
        for i,row in enumerate(rows):
            for j,v in enumerate(row):
                item=QTableWidgetItem(v);item.setTextAlignment(Qt.AlignCenter)
                if j>0:item.setForeground(QColor(('#ffbd70','#66c6ff','#cf9dff')[j-1]))
                table.setItem(i,j,item)
            table.setRowHeight(i,28)
        g=t.get('growth',{});n=g.get('n');target=g.get('target',30);self.growth.setText(f'T0实验模型成长  {fmt(n)} / {target} 有效结算日\n'+('达到首次评估数据门槛，等待独立审计' if n is not None and n>=30 else '达到30个有效结算日后进入首次前向验证审计'))
        self.growthbar.setValue(int(g.get('percent',0)));self.growth.setToolTip('数据门槛达到不等于模型通过；模型身份和校准授权只能来自后台正式状态。')
    def render_sources(self,f,t,s):
        o=f.get('observation',{});fresh=freshness(o.get('observation_time'),self.adapter.cfg['zuuu_delayed_hours']*3600,self.adapter.cfg['zuuu_stale_hours']*3600)
        self.obs.setText(f"ZUUU实况（METAR） · {zh(fresh)} · {metric(o.get('temperature_c'))}\n观测 {clock(o.get('observation_time'))} · 露点 {metric(o.get('dewpoint_c'))}\nQNH {fmt(o.get('qnh_hpa'),'hPa')} · 风 {fmt(o.get('wind_direction_deg'),'°')} / {fmt(o.get('wind_speed_mps'),'米/秒')} · 能见度 {fmt(o.get('visibility_m'),'米')}")
        self.obs.setToolTip('原始机场气象报文：\n'+o.get('raw_report','暂无报文')+'\n收到时间：'+clock(o.get('actual_ingest_time')))
        self.obs_primary.setText('● ZUUU实况（METAR）    '+metric(o.get('temperature_c'))+' · '+zh(fresh))
        self.obs_primary.setStyleSheet('font-size:15px;font-weight:700;color:'+('#62eaa3;' if fresh=='FRESH' else '#ffce70;' if fresh=='STALE' else '#ff8585;'))
        self.obs_secondary.setText('观测 '+clock(o.get('observation_time'))+' · 露点 '+metric(o.get('dewpoint_c'))+'\nQNH '+fmt(o.get('qnh_hpa'),'hPa')+' · 风 '+fmt(o.get('wind_direction_deg'),'°')+' / '+fmt(o.get('wind_speed_mps'),'米/秒')+' · 能见度 '+fmt(o.get('visibility_m'),'米'))
        self.obs_primary.setToolTip(self.obs.toolTip())
        run=f.get('ecmwf',{});a=age(run.get('run_time'));rf=freshness(run.get('run_time'),self.adapter.cfg['ecmwf_delayed_hours']*3600,self.adapter.cfg['ecmwf_stale_hours']*3600);health=f.get('health',{}).get('ECMWF',{});req=health.get('current_status')
        self.ecmwf.setText(f"ECMWF数值预报 · {zh(rf)} · 请求：{zh(req)}\n合法轮次 {clock(run.get('run_time'))} · 距今{duration(a)}\n下载 {clock(run.get('actual_ingest_time'))} · 覆盖{len(f.get('hours',[]))}小时")
        self.ecmwf.setToolTip('数据新鲜度按合法数据轮次时间计算；数据源请求状态读取后台请求状态，二者独立。\n最后成功请求：'+clock(health.get('last_success_time')))
        self.ecmwf_primary.setText('● ECMWF数值预报    '+zh(rf))
        self.ecmwf_primary.setStyleSheet('font-size:15px;font-weight:600;color:'+('#62eaa3;' if rf=='FRESH' else '#ffce70;' if rf=='STALE' else '#ff8585;'))
        self.ecmwf_secondary.setText('合法轮次 '+clock(run.get('run_time'))+' · 距今'+duration(a)+'\n下载 '+clock(run.get('actual_ingest_time'))+' · 覆盖'+str(len(f.get('hours',[])))+'小时\n数据源请求：'+zh(req))
        self.ecmwf_primary.setToolTip(self.ecmwf.toolTip())
        fs=f.get('worker_status');ts=t.get('worker_status');self.workers.setText(f"T0后台任务 · {zh(ts)}  心跳 {clock(t.get('event',{}).get('time'))}\nT+1/T+2后台任务 · {zh(fs)}  心跳 {clock(f.get('scheduler',{}).get('time'))}")
        self.workers.setToolTip(f"T0进程编号：{fmt(t.get('event',{}).get('pid'))}\n正式进程编号：{fmt(f.get('scheduler',{}).get('pid'))}")
        for key,state,record in (('t0',ts,t.get('event',{})),('formal',fs,f.get('scheduler',{}))):
            badge,heartbeat=self.worker_lines[key];badge.setText('● '+zh(state));badge.setStyleSheet('color:'+('#62eaa3;' if state=='RUNNING' else '#ffce70;'))
            heartbeat.setText('心跳 '+clock(record.get('time')).split(' ')[-1]);heartbeat.setToolTip('后台进程编号：'+fmt(record.get('pid'))+'\n最近心跳：'+clock(record.get('time')))
        status=zh(s.get('threshold')) if s.get('threshold')=='READY_FOR_FINAL_SOAK_ACCEPTANCE' else ('进行中' if s.get('status')=='RUNNING' else zh(s.get('status')))
        elapsed=s.get('elapsed');self.soak.setText(f"72小时连续运行测试 · ● {status}\n运行 {elapsed/3600:.1f}小时 / {s.get('target_hours',72)}小时" if elapsed is not None else '72小时连续运行测试：暂无证据')
        self.soakbar.setValue(int(s.get('progress',0)));self.soak.setToolTip('最终验收：'+zh(s.get('acceptance'))+'\n开始时间：'+clock(s.get('start'))+'\n未解决严重问题：'+fmt(s.get('critical'))+'；高优先级问题：'+fmt(s.get('high'))+'\n达到最低时长不会自动通过验收。')
        self.header.state.setText('● 系统正常运行' if fs=='RUNNING' and ts=='RUNNING' else '● 后台状态需关注');self.header.state.setStyleSheet('color:#5bea9a;' if fs=='RUNNING' and ts=='RUNNING' else 'color:#ffcf66;');self.header.soak.setText('72小时连续运行测试：'+status)
        self.header.elapsed.setText('已运行：'+(f'{elapsed/3600:.1f}/{s.get("target_hours",72)}小时（{s.get("progress",0):.0f}%）' if elapsed is not None else '暂无证据'))
        values={'ZUUU':metric(o.get('temperature_c'))+' · '+zh(fresh),'ECMWF':zh(rf),'t0':zh(ts),'formal':zh(fs)}
        good={'ZUUU':fresh=='FRESH','ECMWF':rf=='FRESH' and req in ('ONLINE','NORMAL','OK'),'t0':ts=='RUNNING','formal':fs=='RUNNING'}
        for key,value in values.items():self.summary_labels[key].setText('● '+value);self.summary_labels[key].setStyleSheet('color:'+('#60e69b;' if good[key] else '#ffcf66;'))
        self.summary_obs_detail.setText('观测 '+clock(o.get('observation_time'))+' · QNH '+fmt(o.get('qnh_hpa'),'hPa')+'\n风 '+fmt(o.get('wind_direction_deg'),'°')+'/'+fmt(o.get('wind_speed_mps'),'米/秒')+' · 能见度 '+fmt(o.get('visibility_m'),'米'))
        self.summary_run_detail.setText('轮次 '+clock(run.get('run_time'))+' · 下载 '+clock(run.get('actual_ingest_time')).split(' ')[-1])
        self.summary_request.setText('请求状态：'+zh(req))
        self.summary_labels['ECMWF'].setToolTip(self.summary_run_detail.text()+'\n'+self.summary_request.text());self.summary_labels['ZUUU'].setToolTip(self.summary_obs_detail.text())
        self.summary_soak.setText('● 72小时连续运行测试 · '+status)
        self.summary_progress.setValue(int(s.get('progress',0)))
        self.summary_progress.setFormat(f'{s.get("progress",0):.0f}%'+(f'（{elapsed/3600:.1f}/{s.get("target_hours",72)}小时）' if elapsed is not None else ' · 暂无证据'))
    def update_details(self):
        f=self.data.get('formal',{});t=self.data.get('t0',{});summaries={1:'真实ECMWF小时预报与ZUUU实况见首页趋势图；没有逐小时机器学习预测。',2:'ECMWF逐小时温度（北京时间）\n'+'\n'.join(clock(r['target_time_utc'])+'  '+metric(r.get('temperature_2m_c')) for r in f.get('hours',[])),3:'每日最高温由正式模型和真实整数概率读取。\n'+self.t1.footer.text()+'\n'+self.t2.footer.text(),4:'概率分布见首页日期切换面板；T0尚不提供正式概率。',5:self.obs.text(),6:self.ecmwf.text(),7:'历史记录（只读）\n'+'\n'.join(clock(r.get('prediction_issue_time'))+' · '+r.get('target_business_date','--')+' · '+zh(r.get('status')) for r in f.get('history',[])[:40]),8:'正式模型与实验模型严格区分。\nT0正式模型：尚未授权\n历史研究结果：尚未接入\n前向验证统计：\n'+'\n'.join(('基础预测（Level0）' if r['method']=='LEVEL0' else '实验修正预测（L1_A）')+f" · 已结算 {r['settled']} · 平均绝对误差 {fmt(r['mae'])}" for r in t.get('stats',[])),9:self.growth.text(),10:self.obs.text()+'\n\n'+self.ecmwf.text()+'\n\n'+self.workers.text()+'\n\n'+self.soak.text(),11:'显示设置仅影响界面，不修改后台、模型或数据源配置。'}
        for i,w in self.details.items():w.setText(summaries.get(i,'暂无数据'))
    def closeEvent(self,event):
        self.timer.stop();self.tick.stop()
        if self.reader and self.reader.isRunning():self.reader.wait(6500)
        if self.reader and self.reader.isRunning():event.ignore();QTimer.singleShot(500,self.close);return
        logging.getLogger('phase11').info('GUI shutdown; no backend lifecycle control');event.accept()
