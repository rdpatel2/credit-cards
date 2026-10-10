from datetime import date
from PySide6.QtCore import Qt,QPointF
from PySide6.QtGui import QPainter,QColor,QPen,QPainterPath
from PySide6.QtWidgets import QWidget
from ..theme import color
from .widget_registry import money,numeric

class PriceChart(QWidget):
    def __init__(self, points, currency='USD', parent=None):
        super().__init__(parent)
        self.points=points
        self.currency=currency
        self.setMinimumHeight(120)
        self.setAccessibleName('Historical closing prices')
        if points:
            self.setAccessibleDescription(f'{len(points)} points; first {money(points[0][1])}; last {money(points[-1][1])}; low {money(min(p[1] for p in points))}; high {money(max(p[1] for p in points))}')

    def paintEvent(self,event):
        p=QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QColor(color('#52665a')))
        if len(self.points)<2:
            p.drawText(self.rect(),Qt.AlignmentFlag.AlignCenter,'Refresh market data to load this chart.')
            return
        values=[v for _,v in self.points]
        low,high=min(values),max(values)
        scale=high-low or 1
        area=self.rect().adjusted(65,24,-16,-28)
        for fraction in (0,.5,1):
            y=area.bottom()-area.height()*fraction
            p.setPen(QPen(QColor(color('#e1e8e2')),1))
            p.drawLine(QPointF(area.left(),y),QPointF(area.right(),y))
            p.setPen(QColor(color('#52665a')))
            value=low+fraction*scale
            p.drawText(0,int(y)+5, money(value) if self.currency=='USD' else numeric(value))
        path=QPainterPath()
        first,last=self.points[0][0],self.points[-1][0]
        for i,(when,value) in enumerate(self.points):
            point=QPointF(area.left()+area.width()*(when-first)/(last-first or 1), area.bottom()-area.height()*(value-low)/scale)
            if i==0:path.moveTo(point)
            else:path.lineTo(point)
        p.setPen(QPen(QColor(color('#236347')),2))
        p.drawPath(path)
        p.setPen(QColor(color('#52665a')))
        p.drawText(area.left(),self.height()-4,date.fromtimestamp(first).isoformat())
        p.drawText(area.right()-80,self.height()-4,date.fromtimestamp(last).isoformat())

