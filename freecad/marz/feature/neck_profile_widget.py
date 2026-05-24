# -*- coding: utf-8 -*-
# +---------------------------------------------------------------------------+
# |  Copyright (c) 2020 Frank Martinez <mnesarco at gmail.com>                |
# |                                                                           |
# |  This file is part of Marz Workbench.                                     |
# |                                                                           |
# |  Marz Workbench is free software: you can redistribute it and/or modify   |
# |  it under the terms of the GNU General Public License as published by     |
# |  the Free Software Foundation, either version 3 of the License, or        |
# |  (at your option) any later version.                                      |
# |                                                                           |
# |  Marz Workbench is distributed in the hope that it will be useful,        |
# |  but WITHOUT ANY WARRANTY; without even the implied warranty of           |
# |  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the            |
# |  GNU General Public License for more details.                             |
# |                                                                           |
# |  You should have received a copy of the GNU General Public License        |
# |  along with Marz Workbench.  If not, see <https://www.gnu.org/licenses/>. |
# +---------------------------------------------------------------------------+

from functools import lru_cache
from dataclasses import dataclass
import freecad.marz.extension.fcui as ui
from freecad.marz.model.parametric_neck_profile import ParametricNeckProfile

from freecad.marz.extension.qt import (
    Qt, 
    QtGui, 
    QRect, 
    QRectF, 
    QPointF, 
    QPainter, 
    QColor)


@dataclass
class NeckProfilePreview:
    """
    Neck Profile preview Qt geometry
    """
    profile_path: QtGui.QPainterPath
    channel_rect: QRectF
    head_channel_rect: QRectF
    translate: QPointF


@lru_cache(maxsize=100)
def get_neck_profile_preview(
        name: str, 
        width: float, 
        height: float, 
        channel_depth: float, 
        channel_width: float,
        head_channel_depth: float, 
        head_channel_width: float,
        scale: float,
        coreWidthRatio: float = 0.6,
        coreThicknessRatio: float = 0.35,
        coreOffsetRatio: float = 0.0,
        radiusTreble: float = 20.0,
        radiusBass: float = 20.0) -> NeckProfilePreview:
    
    """
    Convert OCCT Neck profile geometry to Qt 2D geometry
    """
    profile = ParametricNeckProfile(
        coreWidthRatio=coreWidthRatio,
        coreThicknessRatio=coreThicknessRatio,
        coreOffsetRatio=coreOffsetRatio,
        radiusTreble=radiusTreble,
        radiusBass=radiusBass
    )
    wire = profile.wire(width, height)

    # Qt coordinates: X:Horizontal(Across), Y:Vertical(Depth)
    # Parametric profile wire is in Depth-Across plane (X:Depth, Y:Across).
    # In Qt, we want X:Across, Y:Depth.
    # X=0 is fretboard top. Neck is at negative X.
    # Qt Y = -X (so neck depth is positive Qt Y)
    channel_rect = QRectF(-channel_width/2, 0, channel_width, channel_depth)
    head_channel_rect = QRectF(-head_channel_width/2, 0, head_channel_width, head_channel_depth)

    path = QtGui.QPainterPath()
    started = False

    # Extract points from wire edges
    for edge in wire.Edges:
        pts = edge.discretize(Number=20)
        # local profile coords: X:Depth, Y:Lateral
        # Qt coords: X:Across, Y:Depth
        # Qt Y = -X (so neck depth is positive Qt Y)
        qpts = [QPointF(v.y, -v.x) for v in pts]
        if not started:
            path.moveTo(qpts[0])
            started = True
        for p in qpts[1:]:
            path.lineTo(p)
    path.closeSubpath()

    # Center Across (Y) and Depth (X)
    bbox = wire.BoundBox
    center_x = (bbox.YMax + bbox.YMin) / 2.0
    center_y = (-bbox.XMax - bbox.XMin) / 2.0

    # Simple centering translation (Qt coordinates)
    pos = QPointF(-center_x, -center_y)

    return NeckProfilePreview(path, channel_rect, head_channel_rect, pos)


def paint_neck_profile(form, painter: QPainter, ch: ui.CanvasHelper):
    painter.setRenderHint(QPainter.Antialiasing, True)
    ch.setBackgroundColor(QColor.fromRgb(255, 255, 255))

    width = form.nut_width.value()
    height = form.neck_startThickness.value()
    # Fit width and height with a generous margin
    # Widgets are ~200x100.
    scale = (ch.event.rect().width() - 60) / width
    scale_h = (ch.event.rect().height() - 40) / height
    scale = min(scale, scale_h) * 0.8

    preview = get_neck_profile_preview(
        "Parametric",
        width, 
        height, 
        form.trussRod_depth.value(),
        form.trussRod_width.value(),
        form.trussRod_headDepth.value(),
        form.trussRod_headWidth.value(),
        scale,
        form.neck_coreWidthRatio.value(),
        form.neck_coreThicknessRatio.value(),
        form.neck_coreOffsetRatio.value(),
        form.neck_radiusTreble.value(),
        form.neck_radiusBass.value())
    
    # Center of widget
    painter.translate(ch.event.rect().width()/2.0, ch.event.rect().height()/2.0)
    painter.scale(scale, scale)
    painter.translate(preview.translate)

    with ch.pen(color=Qt.black, width=1, cosmetic=True):
        painter.fillPath(preview.profile_path, QtGui.QBrush(Qt.lightGray))
        painter.drawPath(preview.profile_path)

    with ch.pen(color=Qt.blue, width=1, cosmetic=True):
        painter.fillRect(preview.head_channel_rect, QtGui.QBrush(Qt.darkGray))
        painter.drawRect(preview.head_channel_rect)

    with ch.pen(color=Qt.gray, width=1, cosmetic=True):
        painter.fillRect(preview.channel_rect, QtGui.QBrush(Qt.white))


def NeckProfileWidget(form, width: int=200, height: int=100):
    def paint(widget, painter: QPainter, ch: ui.CanvasHelper):
        paint_neck_profile(form, painter, ch)
    return ui.Canvas(paint, width=width, height=height)