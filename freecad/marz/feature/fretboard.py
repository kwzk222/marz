# -*- coding: utf-8 -*-
# +---------------------------------------------------------------------------+
# |  Copyright (c) 2020 Frank Martinez <mnesarco at gmail.com>                |
# |  This file is part of Marz Workbench.                                     |
# |                                                                           |
# |  Marz Workbench is free software: you can redistribute it and/or modify   |
# |  it under the terms of the GNU General Public License as published by     |
# |  the Free Software Foundation, either version 3 of the License, or        |
# |  (at your option) any later version.                                      |
# +---------------------------------------------------------------------------+

import math
from typing import List, Tuple

import Part  # type: ignore
from freecad.marz.extension.fc import App, Vector
from freecad.marz.extension.fcdoc import PartFeature
from freecad.marz.extension.fcui import ui_thread
from freecad.marz.feature.progress import ProgressListener
from freecad.marz.model import fretboard_builder as builder
from freecad.marz.utils import geom, traceTime
from freecad.marz.utils.cache import PureFunctionCache, getCachedObject
from freecad.marz.model.linexy import lineIntersection, linexy
from freecad.marz.model.instrument import ModelException, fret, rad_to_deg, get_is_zero_fret
from freecad.marz.extension.threading import Task
from freecad.marz.utils.collections import group_by
from freecad.marz.feature.document import (
    FretboardPart,
    RefBridgePos,
    RefFretboardFrame,
    RefFrets,
    RefMidLine,
    RefNeckFrame,
    RefProjFrame,
    RefScaleFrame
)
from freecad.marz.feature.logging import MarzLogger


# ------------------------------
# Helper Functions
# ------------------------------

def fretPos(f, line, h, edo=12):
    """Compute position for fret-related objects (inlay center)."""
    scale = line.length
    a = fret(f-1, scale, edo) if f - 1 >= 0 else 0.0
    b = fret(f, scale, edo)
    p = line.lerpPointAt((a + b) / 2)
    return Vector(p.x, p.y, h + 1)


def makeInlays(fbd, thickness=-1, inlayDepth=0):
    """Build inlay shapes or pockets."""
    line = fbd.scaleFrame.midLine
    shapes = []
    edo = getattr(fbd, 'EDO', 12)

    with traceTime("Prepare inlay pockets geometry"):
        for i in range(len(fbd.frets)):
            inlay = App.ActiveDocument.getObject(f"Marz_FInlay_Fret{i}")
            if inlay:
                ishape = inlay.Shape.copy()
                ishape.translate(fretPos(i, line, thickness, edo))
                shapes.append(ishape)

    if shapes:
        if thickness <= 0:
            return Part.makeCompound(shapes)
        with traceTime("Build inlay pockets subtractive solid"):
            return Part.makeCompound(shapes).extrude(Vector(0, 0, -inlayDepth-1))


@PureFunctionCache
def fretboardSection(c, r, w, t, v):
    """Create a wire section for fretboard loft."""
    alpha = math.asin(w/(2*r))
    alpha_deg = rad_to_deg(alpha)
    arc = Part.makeCircle(r, c, v, -alpha_deg, alpha_deg)

    for offset in [90, 180, 270]:
        if arc.Vertexes[0].Point.z <= 0:
            arc = Part.makeCircle(r, c, v, offset-alpha_deg, offset+alpha_deg)

    if arc.Vertexes[0].Point.z <= 0:
        raise ModelException("Fretboard's radius inconsistent with geometry")

    a = arc.Vertexes[0].Point
    b = arc.Vertexes[1].Point
    h = r * math.cos(alpha) - r + t
    x = Vector(a.x, a.y, a.z - h)
    d = Vector(b.x, b.y, b.z - h)
    p = Vector(c.x, c.y, c.z + r)

    return Part.Wire(Part.Shape([
        Part.Arc(a, p, b),
        Part.LineSegment(a, x),
        Part.LineSegment(x, d),
        Part.LineSegment(d, b)
    ]).Edges)


def fretboard_fillet(fb, radius):
    Z = Vector(0,0,1)
    Y = Vector(0,1,0)
    N_INF = Vector(-10000, 0, 0)

    face = geom.query_one(fb.Faces,
                          where=lambda f: geom.is_planar(f, coplanar=Y),
                          order_by=lambda f: f.CenterOfGravity.distanceToPoint(N_INF))
    if not face:
        MarzLogger.warn("Fretboard fillet not possible due to missing face")
        return fb

    selected = geom.query(face.Edges, where=lambda e: geom.are_parallel(e.tangentAt(e.FirstParameter), Z), limit=2)
    if len(selected) == 2:
        fb = fb.makeFillet(radius, selected)
    else:
        MarzLogger.warn("Fretboard fillet not possible due to missing edges")
    return fb


@PureFunctionCache
def fretboardCone(startRadius, endRadius, thickness, fbd, top):
    """Create a conic solid for the fretboard base."""
    line = fbd.frame.midLineExtendedWith(10,10)
    radiusSlope = (endRadius - startRadius) / (fbd.scaleFrame.midLine.length/2)
    b = linexy(lineIntersection(line, fbd.frets[0]).point, line.start).length

    def radiusFn(l):
        return radiusSlope * (l + b) + startRadius

    def centerFn(l, r):
        p = line.lerpPointAt(l)
        return Vector(p.x, p.y, top - r)

    wires = []
    vdir = geom.vec(line.vector)
    for l, width in [(0, fbd.neckFrame.nut.length), (line.length, fbd.neckFrame.bridge.length)]:
        radius = radiusFn(l)
        center = centerFn(l, radius)
        wires.append(fretboardSection(center, radius, width, thickness, vdir))

    solid = Part.makeLoft(wires, True, True).removeSplitter()
    return solid


def fretsCut(inst, fbd):
    isZero = get_is_zero_fret(inst.fretboard)
    return fretsCutPure(inst.fretboard.startRadius, inst.fretboard.endRadius,
                        inst.fretboard.thickness, inst.fretWire.tangDepth,
                        inst.fretWire.tangWidth, inst.fretboard.fretNipping,
                        isZero, fbd)


@PureFunctionCache
def fretsCutPure(startRadius, endRadius, thickness, tangDepth, tangWidth, nipping, isZeroFret, fbd):
    """Create a solid of all fret slots to cut from the board."""
    bladeHeight = thickness*4
    trim = fretboardCone(startRadius, endRadius, thickness, fbd, thickness - tangDepth)

    frets = []
    for index, fret_i in enumerate(fbd.frets):
        if not isZeroFret and index == 0:
            continue
        fret = fret_i.extendSym(-nipping if nipping > 0 else 5)
        blade = geom.extrusion(fret.rectSym(tangWidth), 0, [0,0,bladeHeight])
        blade = blade.cut(trim)
        frets.append(blade)
    return frets


def base(inst, fbd):
    """Create the base board of the fretboard."""
    board, cache = getCachedObject('fretboard_base', fbd,
                                   inst.fretboard.startRadius,
                                   inst.fretboard.endRadius,
                                   inst.fretboard.thickness)
    if not board:
        cone = fretboardCone(inst.fretboard.startRadius, inst.fretboard.endRadius,
                             inst.fretboard.thickness, fbd, inst.fretboard.thickness)
        f = fbd.frame
        ps = [f.bridge.start, f.bass.start, f.nut.start, f.treble.start, f.bridge.start]
        cut = geom.extrusion(ps, 0, (0,0,inst.fretboard.thickness+1))
        board = cone.common(cut)

        try:
            fillet = fretboard_fillet(board, inst.fretboard.filletRadius or 0.0)
            if fillet and fillet.isValid():
                board = fillet
        except:
            MarzLogger.warn("Fretboard fillet failed for radius: {}", inst.fretboard.filletRadius)

        cache(board)
    return board


def nutSlot(inst, fbd):
    return nutSlotPure(inst.fretboard.thickness, inst.nut.depth, fbd)


@PureFunctionCache
def nutSlotPure(thickness, depth, fbd):
    nut = fbd.nutFrame.nut.clone().extendSym(5)
    bridge = fbd.nutFrame.bridge.clone().extendSym(5)
    polygon = [bridge.end, bridge.start, nut.end, nut.start, bridge.end]
    return geom.extrusion(polygon, thickness - depth, [0,0,thickness*4])


# ------------------------------
# Fretboard Builder Functions
# ------------------------------

def createFretboardShape(instrument, progress_listener=None):
    """Create a Fretboard with proper EDO handling."""
    from freecad.marz.model import fretboard_builder as builder
    if progress_listener is None:
        class _Noop:
            def add(self, *a, **kw): pass
        progress_listener = _Noop()

    progress_listener.add("Updating Fretboard...")
    inst = instrument
    fbd = builder.buildFretboardData(inst, edo=getattr(inst.fretboard, 'edo', 12))

    inlaysTask = Task.execute(makeInlays, fbd, inst.fretboard.thickness, inst.fretboard.inlayDepth)

    fretboard, cache = getCachedObject(
        'FretboardFeature',
        fbd, inst.fretWire.tangWidth, inst.fretWire.tangDepth,
        inst.nut.depth, inst.fretboard.thickness, inst.fretboard.startRadius,
        inst.fretboard.endRadius, inst.fretboard.fretNipping, inst.fretboard.filletRadius, getattr(inst.fretboard, 'length', 0.0)
    )

    if not fretboard:
        board, nut, fretSlots = Task.join([Task.execute(t, inst, fbd) for t in [base, nutSlot, fretsCut]])
        if board and nut and fretSlots:
            fretboard = board.cut(tuple([*fretSlots, nut]))
            if fretboard.Solids:
                fretboard = fretboard.Solids[0].removeSplitter()
        cache(fretboard)

    inlays = inlaysTask.get()
    if inlays:
        fretboard = fretboard.cut(inlays)

    progress_listener.add('Fretboard done.')
    return fretboard


def createFretboardPart(fbd, progress_listener=None):
    """Create the actual Fretboard Part in FreeCAD."""
    fbd.createFretboard()


def createConstructionShapes(fbd) -> List[Tuple[PartFeature, List[Vector]]]:
    """Return a list of construction shapes (target, points)."""
    shapes = []

    # Scale Frame
    shapes.append((RefScaleFrame, geom.vecs(fbd.scaleFrame.polygon)))
    shapes.append((RefProjFrame, geom.vecs(fbd.virtStrFrame.polygon)))
    shapes.append((RefFretboardFrame, geom.vecs(fbd.frame.polygon)))
    shapes.append((RefNeckFrame, geom.vecs(fbd.neckFrame.polygon)))

    midLine = fbd.neckFrame.midLineExtendedWith(400, 300)
    shapes.append((RefMidLine, geom.vecs([midLine.start, midLine.end])))

    pos = fbd.bridgePos
    shapes.append((RefBridgePos, geom.vecs([pos.start, pos.end])))

    for fret_n, fret_line in enumerate(fbd.frets):
        shapes.append((RefFrets, geom.vecs(fret_line.points)))

    return shapes


def createConstructionShapesParts(fbd):
    """Create construction shapes in the FreeCAD document."""
    @ui_thread()
    def createInUI():
        doc = App.ActiveDocument
        shapes = createConstructionShapes(fbd)
        for parts in group_by(shapes, lambda s: s[0].name).values():
            compound = []
            for target, points in parts:
                compound.append(Part.makePolygon(points))
            target.set(Part.makeCompound(compound), doc=doc)
    createInUI()
