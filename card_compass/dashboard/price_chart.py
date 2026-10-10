"""Responsive historical prices with native pointer and keyboard inspection."""
from bisect import bisect_left
from datetime import datetime, timezone
import math
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from PySide6.QtCore import Qt, QPointF, QRectF, QEvent, Signal
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath, QAccessible, QAccessibleEvent
from PySide6.QtWidgets import QWidget
from ..theme import color
from .widget_registry import price


class PriceChart(QWidget):
    pointHovered = Signal(object)

    def __init__(self, points, currency='USD', parent=None):
        super().__init__(parent)
        self.setMinimumHeight(120)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents)
        self.setAccessibleName('Historical closing prices')
        self.hover_index=None
        self.hover_text=''
        self.set_data(points,currency)

    def set_data(self,points,currency='USD',timezone_name='UTC'):
        validated={}
        for when,value in points:
            when,value=float(when),float(value)
            if not math.isfinite(when) or not math.isfinite(value):
                raise ValueError('Invalid chart points. Refresh to reload this widget.')
            datetime.fromtimestamp(when,timezone.utc)
            validated[when]=value
        validated=sorted(validated.items())
        changed=validated!=getattr(self,'points',None)
        self.points,self.currency=validated,currency
        self.times=[point[0] for point in validated]
        try:self.timezone=ZoneInfo(timezone_name or 'UTC')
        except (ZoneInfoNotFoundError,ValueError):self.timezone=timezone.utc
        if changed:self.hover_index=None
        self.low=min((p[1] for p in validated),default=0)
        self.high=max((p[1] for p in validated),default=0)
        padding=max(abs(self.low)*.01,.01) if self.low==self.high else 0
        self.axis_low,self.axis_high=self.low-padding/2,self.high+padding/2
        self.intraday=bool(validated and validated[-1][0]-validated[0][0]<86400)
        self.summary=(f'{len(validated)} points; first {price(validated[0][1],currency)}; last {price(validated[-1][1],currency)}; '
                      f'low {price(self.low,currency)}; high {price(self.high,currency)}. Hover or use Left/Right arrows to inspect prices.'
                      if validated else 'No historical prices loaded. Refresh to load data.')
        self.update_detail()

    def plot_rect(self):
        labels=[price(self.axis_low,self.currency),price(self.axis_high,self.currency)]
        margin=min(max(self.fontMetrics().horizontalAdvance(label) for label in labels)+12,max(20,self.width()*.4))
        return QRectF(margin,42,max(1,self.width()-margin-14),max(1,self.height()-72))

    def point_position(self,index):
        area=self.plot_rect();when,value=self.points[index]
        fraction=(when-self.times[0])/(self.times[-1]-self.times[0]) if self.times[-1]!=self.times[0] else .5
        return QPointF(area.left()+area.width()*fraction,area.bottom()-area.height()*(value-self.axis_low)/(self.axis_high-self.axis_low))

    def inspect_at(self,position):
        area=self.plot_rect()
        if not self.points or not area.contains(position):self.select_point(None);return
        when=self.times[0]+(position.x()-area.left())/area.width()*(self.times[-1]-self.times[0])
        index=bisect_left(self.times,when)
        if index==len(self.times):index-=1
        elif index and when-self.times[index-1]<=self.times[index]-when:index-=1
        self.select_point(index)

    def select_point(self,index):
        if self.hover_index==index:return
        self.hover_index=index;self.update_detail()
        self.pointHovered.emit(self.points[index] if index is not None else None)

    def update_detail(self):
        self.hover_text=''
        if self.hover_index is not None:
            when,value=self.points[self.hover_index]
            day=datetime.fromtimestamp(when,self.timezone)
            label=day.strftime('%b %d, %Y · %H:%M %Z' if self.intraday else '%b %d, %Y')
            self.hover_text=label+'  ·  '+price(value,self.currency)
        self.setToolTip(self.hover_text or 'Hover to inspect a closing price. Focus the chart and use Left/Right, Home/End, or Escape.')
        self.setAccessibleDescription(self.hover_text or self.summary)
        self.update()

    def mouseMoveEvent(self,event):
        self.inspect_at(event.position());event.accept()

    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason);self.inspect_at(event.position());event.accept()
        else:super().mousePressEvent(event)

    def leaveEvent(self,event):
        self.select_point(None);super().leaveEvent(event)

    def focusOutEvent(self,event):
        self.select_point(None);super().focusOutEvent(event)

    def keyPressEvent(self,event):
        key=event.key()
        if key==Qt.Key.Key_Escape:self.select_point(None);event.accept();return
        if self.points and key in (Qt.Key.Key_Left,Qt.Key.Key_Right,Qt.Key.Key_Home,Qt.Key.Key_End):
            index=self.hover_index if self.hover_index is not None else 0
            if key==Qt.Key.Key_Home:index=0
            elif key==Qt.Key.Key_End:index=len(self.points)-1
            elif self.hover_index is not None:index+=1 if key==Qt.Key.Key_Right else -1
            self.select_point(max(0,min(len(self.points)-1,index)))
            QAccessible.updateAccessibility(QAccessibleEvent(self,QAccessible.Event.DescriptionChanged))
            event.accept();return
        super().keyPressEvent(event)

    def event(self,event):
        if event.type()==QEvent.Type.EnabledChange and not self.isEnabled() and hasattr(self,'hover_index'):
            self.select_point(None)
        if event.type() in (QEvent.Type.TouchBegin,QEvent.Type.TouchUpdate,QEvent.Type.TouchEnd):
            if event.points():self.inspect_at(event.points()[0].position())
            event.accept();return True
        return super().event(event)

    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor(color('#52665a')))
        if not self.points:
            painter.drawText(self.rect(),Qt.AlignmentFlag.AlignCenter,'No historical points available. Use Refresh to retry.')
            return
        area=self.plot_rect();spread=self.axis_high-self.axis_low
        for fraction in (0,.5,1):
            y=area.bottom()-area.height()*fraction
            painter.setPen(QPen(QColor(color('#e1e8e2')),1))
            painter.drawLine(QPointF(area.left(),y),QPointF(area.right(),y))
            painter.setPen(QColor(color('#52665a')))
            value=self.axis_low+fraction*spread
            label=self.fontMetrics().elidedText(price(value,self.currency),Qt.TextElideMode.ElideRight,max(1,int(area.left()-6)))
            painter.drawText(QRectF(0,y-10,area.left()-6,20),Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,label)
        path=QPainterPath()
        for index in range(len(self.points)):
            point=self.point_position(index)
            if index==0:path.moveTo(point)
            else:path.lineTo(point)
        painter.setPen(QPen(QColor(color('#236347')),2));painter.drawPath(path)
        if len(self.points)==1:painter.drawEllipse(self.point_position(0),3,3)
        painter.setPen(QColor(color('#52665a')))
        first=datetime.fromtimestamp(self.times[0],self.timezone);last=datetime.fromtimestamp(self.times[-1],self.timezone)
        left=first.strftime('%H:%M' if self.intraday else '%Y-%m-%d');right=last.strftime('%H:%M %Z' if self.intraday else '%Y-%m-%d')
        label_area=QRectF(area.left(),self.height()-24,area.width(),20)
        painter.drawText(label_area,Qt.AlignmentFlag.AlignLeft,left)
        if area.width()>self.fontMetrics().horizontalAdvance(left+right)+12:painter.drawText(label_area,Qt.AlignmentFlag.AlignRight,right)
        if self.hover_index is not None:
            point=self.point_position(self.hover_index)
            painter.setPen(QPen(QColor(color('#236347')),1,Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(point.x(),area.top()),QPointF(point.x(),area.bottom()))
            painter.drawLine(QPointF(area.left(),point.y()),QPointF(area.right(),point.y()))
            painter.setBrush(QColor(color('#236347')));painter.setPen(QPen(QColor(color('#ffffff')),2));painter.drawEllipse(point,4,4)
            width=min(self.width()-12,self.fontMetrics().horizontalAdvance(self.hover_text)+20)
            callout=QRectF(max(6,min(point.x()-width/2,self.width()-width-6)),5,width,28)
            painter.setBrush(QColor(color('#edf4ed')));painter.setPen(QPen(QColor(color('#236347')),1));painter.drawRoundedRect(callout,5,5)
            painter.setPen(QColor(color('#233d38')))
            label=self.fontMetrics().elidedText(self.hover_text,Qt.TextElideMode.ElideRight,max(1,int(width-12)))
            painter.drawText(callout.adjusted(6,0,-6,0),Qt.AlignmentFlag.AlignCenter,label)
        elif area.width()>210:
            painter.drawText(QRectF(area.left(),5,area.width(),28),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,'Hover or use ← / → to inspect prices')

