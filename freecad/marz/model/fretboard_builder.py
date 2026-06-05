# freecad/marz/model/fretboard_builder.py 

# -*- coding: utf-8 -*-
import math
import Part

from freecad.marz.model.fretboard_data import FretboardBox, FretboardData
from freecad.marz.model.instrument import FretboardCut, fret, NutPosition, NeckJoint
from freecad.marz.model.linexy import line, lineFrom, lineIntersection, lineTo, linexy
from freecad.marz.model.vxy import vxy

# Utilities used for building parts & caching
from freecad.marz.utils.cache import PureFunctionCache, getCachedObject
from freecad.marz.utils import geom, traceTime
from freecad.marz.extension.threading import Task
from freecad.marz.utils.collections import group_by
from freecad.marz.extension.fcui import ui_thread

# Some imports for creating construction parts in the UI
from freecad.marz.extension.fc import App, Vector
from freecad.marz.extension.fcdoc import PartFeature
from freecad.marz.feature.logging import MarzLogger

# helper functions are implemented in this module (base, nutSlot, fretsCut, etc.)
from freecad.marz.feature.fretboard import fretboardCone

def buildFretboardData(model) -> FretboardData:
    inst = model
    # normalize EDO reading (accept both cases)
    edo = getattr(inst.fretboard, 'edo', getattr(inst.fretboard, 'EDO', 12))
    # Garante que o comprimento seja definido e não zero
    fb_len = getattr(inst.fretboard, "length", None)
    if fb_len is None or fb_len <= 0.0:
        fb_len = 480.0
        inst.fretboard.length = fb_len  # Salva no objeto

    if not hasattr(inst.fretboard, "length"):
        try:
            inst.fretboard.addProperty("App::PropertyFloat", "length", "Fretboard", "Comprimento do braco (mm)")
        except Exception:
            pass
    inst.fretboard.length = fb_len

    def calc_virt_frame():
        spread = (inst.bridge.stringDistanceProj - inst.nut.stringDistanceProj) / 2.0
        length = inst.scale.max + inst.nut.offset
        # protect asin argument
        val = (spread / length) if length != 0 else 0.0
        val = max(-1.0, min(1.0, val))
        angle = math.asin(val)
        bass = line(vxy(0, 0), vxy(-length, 0)).rotate(-angle)
        bass.translateTo(bass.lerpPointAt(-inst.nut.offset))
        bridge = line(bass.end, vxy(0, - inst.bridge.stringDistanceProj))
        treble = line(bridge.end, vxy(length, 0)).rotate(angle)
        nut = lineTo(treble.end, bass.start)
        return FretboardBox(bass, treble, nut, bridge)

    virtStrFrame = calc_virt_frame()

    def calc_scale_frame():
        s = virtStrFrame.bass.lerpPointAt(inst.nut.offset)
        bass = lineTo(s, virtStrFrame.bass.end)
        bassPF = fret(inst.fretboard.perpendicularFret, inst.scale.bass, edo)
        trebPF = fret(inst.fretboard.perpendicularFret, inst.scale.treble, edo)
        scaleOffset = bassPF - trebPF
        vtreb = virtStrFrame.treble.cloneInverted()
        nut = lineTo(vtreb.lerpPointAt(scaleOffset + inst.nut.offset), bass.start)
        treble = lineTo(vtreb.lerpPointAt(scaleOffset + inst.nut.offset + inst.scale.treble), nut.start)
        bridge = lineTo(bass.end, treble.start)
        return FretboardBox(bass, treble, nut, bridge)

    scaleFrame = calc_scale_frame()
    bassSideMargin = inst.fretboard.sideMargin + inst.stringSet.last / 2.0
    trebSideMargin = inst.fretboard.sideMargin + inst.stringSet.first / 2.0 + getattr(inst.neck, 'trebleSideExtension', 0.0)

    vtreb = scaleFrame.treble.cloneInverted()
    bassPerp = scaleFrame.bass.clone().rotate(math.radians(-90)).vector.setLength(bassSideMargin)
    trebPerp = vtreb.clone().rotate(math.radians(90)).vector.setLength(trebSideMargin)

    bassMarginLine = scaleFrame.bass.clone().translate(bassPerp).extendSym(100)
    trebMarginLine = vtreb.clone().translate(trebPerp).extendSym(100)

    def calc_frets():
        """
        Build and return the list of fret lines (lineTo objects).
        If inst.fretboard.length is set (>0), cap frets so none lie beyond that length
        (measured from the nut). Also update inst.fretboard.frets if the user-requested
        frets exceed the physically possible number for the given scale & EDO.
        """
        frets = []

        # If the user provided a physical fretboard length (mm), compute the
        # maximum integer fret index that fits physically.
        max_allowed = None
        fb_len = getattr(inst.fretboard, 'length', None)
        if fb_len is not None and fb_len > 0.0:
            bass_scale = float(inst.scale.bass) if inst.scale and inst.scale.bass else None
            if bass_scale and bass_scale > 0.0:
                ratio = fb_len / bass_scale
                if ratio >= 1.0:
                    # board length reaches or exceeds scale  allow the configured frets
                    max_allowed = inst.fretboard.frets
                else:
                    try:
                        # exact continuous solution; floor to get integer max fret index
                        val = - edo * math.log2(1.0 - ratio)
                        max_allowed = int(math.floor(val))
                    except Exception:
                        max_allowed = inst.fretboard.frets

        # If we computed a maximum allowed count, enforce it on the model's property
        if max_allowed is not None:
            if inst.fretboard.frets > max_allowed:
                inst.fretboard.frets = max_allowed

        # Build frets, stopping when next fret would be beyond the physical board length
        for i in range(inst.fretboard.frets + 1):
            # compute nut->fret distance (on bass side) for this index
            d = fret(i, inst.scale.bass, edo)
            # If fb_len defined and this fret would be beyond it, stop adding frets
            if fb_len is not None and i > 0 and d > fb_len:
                break

            s = d
            e = fret(i, inst.scale.treble, edo)
            ps = scaleFrame.bass.lerpPointAt(s)
            pe = vtreb.lerpPointAt(e)
            bfret = lineTo(ps, pe).extendSym(100)
            i1 = lineIntersection(bassMarginLine, bfret)
            i2 = lineIntersection(trebMarginLine, bfret)
            frets.append(lineTo(i1.point, i2.point))
        return frets


    frets = calc_frets()
    print(f"DEBUG: scale={inst.scale.bass}, edo={edo}, fb_len={fb_len}, frets={inst.fretboard.frets}")
    if not frets:
        # If no frets computed, fallback to original behavior (shouldn't normally happen)
        raise RuntimeError("No frets generated for fretboard")

    fret0 = frets[0]

    # If the user set an explicit fretboard length, compute a fractional
    # virtual fret index that matches that length and use it for the board end.
    fb_len = getattr(inst.fretboard, 'length', None)
    if fb_len is not None and fb_len > 0.0:
        try:
            bass_scale = float(inst.scale.bass)
            ratio = fb_len / bass_scale
            if ratio >= 1.0:
                # board extends to or beyond scale  use last real fret
                fretz = frets[-1]
            else:
                # fractional fret index that lies exactly at fb_len
                n_virtual = - edo * math.log2(1.0 - ratio)
                s = fret(n_virtual, inst.scale.bass, edo)
                e = fret(n_virtual, inst.scale.treble, edo)
                ps = scaleFrame.bass.lerpPointAt(s)
                pe = vtreb.lerpPointAt(e)
                bfret = lineTo(ps, pe).extendSym(100)
                i1 = lineIntersection(bassMarginLine, bfret)
                i2 = lineIntersection(trebMarginLine, bfret)
                fretz = lineTo(i1.point, i2.point)
        except Exception:
            # On any error, fall back to the last real fret
            fretz = frets[-1]
    else:
        fretz = frets[-1]
    

    def calc_fretboard_frame():
        if not frets or len(frets) < 2:
            raise RuntimeError(
                f"calc_frets() returned insufficient frets (count={len(frets)}). "
                f"Check EDO={edo}, scale={inst.scale.bass}, fb_len={getattr(inst.fretboard,'length',None)}, frets={inst.fretboard.frets}"
            )
        bassRef = lineTo(fret0.start, frets[1].start)
        s = bassRef.lerpPointAt(-inst.nut.offset - inst.nut.thickness - inst.fretboard.startMargin)
        e = lineFrom(fretz.start, bassRef.vector, inst.fretboard.endMargin).end
        bass = lineTo(s, e)

        trebRef = lineTo(fret0.end, frets[1].end)
        if (inst.nut.position is NutPosition.PARALLEL):
            s = trebRef.lerpPointAt(-inst.nut.offset - inst.nut.thickness - inst.fretboard.startMargin)
            e = lineFrom(fretz.end, trebRef.vector, inst.fretboard.endMargin).end
            treble = lineTo(e, s)
        else:
            tmp = lineFrom(bass.start, vxy(0, -1), 100)
            i = lineIntersection(tmp, trebRef).point or trebRef.lerpPointAt(
                -inst.nut.offset - inst.nut.thickness - inst.fretboard.startMargin)
            e = lineFrom(fretz.end, trebRef.vector, inst.fretboard.endMargin).end
            treble = lineTo(e, i)

        if (inst.fretboard.cut is FretboardCut.CUSTOM):
            bass = bass.lerpLineTo(inst.fretboard.cutBassDistance)
            treble = treble.cloneInverted().lerpLineTo(inst.fretboard.cutTrebleDistance).cloneInverted()
        elif (inst.fretboard.cut is FretboardCut.PERPENDICULAR):
            if (inst.fretboard.cutBassDistance):
                bass = bass.lerpLineTo(inst.fretboard.cutBassDistance)
            tmp = lineFrom(bass.end, vxy(0, -1), 100)
            i = lineIntersection(tmp, treble)
            if (i.point):
                treble = lineTo(i.point, treble.end)

        bridge = lineTo(bass.end, treble.start)
        nut = lineTo(treble.end, bass.start)
        return FretboardBox(bass, treble, nut, bridge)

    frame = calc_fretboard_frame()

    def calc_nut_frame():
        vtreb = frame.treble.cloneInverted()
        
        try:
            from freecad.marz.model.instrument import get_is_zero_fret
            isZero = get_is_zero_fret(inst.fretboard)
        except Exception:
            isZero = getattr(inst.fretboard, 'isZeroFret', False)
            if isinstance(isZero, property):
                isZero = False
            
        offset = inst.nut.offset if isZero else 0.0

        fret0_bass = frets[0].start
        fret0_treb = frets[0].end
        
        # Calculate offset correctly using vxy multiplication or components
        s_bass = lineFrom(fret0_bass, vxy(-frame.bass.vector.x, -frame.bass.vector.y), offset).end
        s_treb = lineFrom(fret0_treb, vxy(-vtreb.vector.x, -vtreb.vector.y), offset).end
        
        bass = lineFrom(s_bass, frame.bass.vector, inst.nut.thickness)
        treble = lineFrom(s_treb, vtreb.vector, inst.nut.thickness).flipDirection()
        return FretboardBox(bass, treble, lineTo(treble.end, bass.start), lineTo(bass.end, treble.start))

    nutFrame = calc_nut_frame()

    # center frames
    diff = frets[0].mid().sub(scaleFrame.nut.mid())
    scaleFrame = scaleFrame.translate(diff)
    virtStrFrame = virtStrFrame.translate(diff)

    def cal_bridge_pos():
        a = scaleFrame.treble.lerpPointAt(-inst.bridge.trebleCompensation)
        b = scaleFrame.bass.lerpPointAt(scaleFrame.bass.length + inst.bridge.bassCompensation)
        return linexy(a, b)

    bridgePos = cal_bridge_pos()

    def calc_neck_frame():
        x = max(frame.bass.start.x, frame.treble.end.x)
        perp = linexy(vxy(x, 0), vxy(x, 1))
        p = lineIntersection(frame.bass, perp)
        q = lineIntersection(frame.treble, perp)
        nut = linexy(p.point, q.point)

        if inst.neck.joint is NeckJoint.THROUGH:
            x = min(frame.bass.end.x, frame.treble.start.x) - inst.body.length
        else:
            x = min(frame.bass.end.x, frame.treble.start.x)

        x += inst.neck.heelOffset
        perp = linexy(vxy(x, 0), vxy(x, 1))
        p = lineIntersection(frame.bass, perp)
        q = lineIntersection(frame.treble, perp)
        bridge = linexy(q.point, p.point)
        bass = linexy(bridge.end.clone(), nut.start.clone())
        treble = linexy(nut.end.clone(), bridge.start.clone())
        return FretboardBox(bass, treble, nut, bridge)

    neckFrame = calc_neck_frame()

    # return FretboardData; pass edo into it
    fbd = FretboardData(frame, virtStrFrame, scaleFrame, nutFrame,
                        frets, bridgePos, neckFrame,
                        inst.fretboard.filletRadius, inst.neck.heelOffset, edo)
    fbd = fbd.translate(vxy(0, 0).sub(neckFrame.nut.mid()))
    return fbd

# -----------------------
# Geometry / helper functions (base, nutSlot, fretsCut, makeInlays)
# -----------------------

@PureFunctionCache
def fretboardSection(c, r, w, t, v):
    alpha = math.asin(max(-1.0, min(1.0, w/(2*r))))
    alpha_deg = (180.0 * alpha) / math.pi
    arc = Part.makeCircle(r, c, v, -alpha_deg, alpha_deg)
    if arc.Vertexes[0].Point.z <= 0:
        for offset in [90, 180, 270]:
            arc = Part.makeCircle(r, c, v, offset-alpha_deg, offset+alpha_deg)
            if arc.Vertexes[0].Point.z > 0:
                break
    a = arc.Vertexes[0].Point
    b = arc.Vertexes[1].Point
    h = r * math.cos(alpha) - r + t
    x = Vector(a.x, a.y, a.z - h)
    d = Vector(b.x, b.y, b.z - h)
    p = Vector(c.x, c.y, c.z + r)
    return Part.Wire(Part.Shape([Part.Arc(a,p,b), Part.LineSegment(a,x), Part.LineSegment(x,d), Part.LineSegment(d,b)]).Edges)

from freecad.marz.model.instrument import get_is_zero_fret

def fretsCut(inst, fbd):
    isZero = get_is_zero_fret(inst.fretboard)
    return fretsCutPure(
        inst.fretboard.startRadius,
        inst.fretboard.endRadius,
        inst.fretboard.thickness,
        inst.fretWire.tangDepth,
        inst.fretWire.tangWidth,
        inst.fretboard.fretNipping,
        isZero,
        fbd
    )

@PureFunctionCache
def fretsCutPure(startRadius, endRadius, thickness, tangDepth, tangWidth, nipping, isZeroFret, fbd):
    bladeHeight = thickness*4
    trim = fretboardCone(startRadius, endRadius, thickness, fbd, thickness - tangDepth)
    frets = []
    for index, fret_i in enumerate(fbd.frets):
        if not isZeroFret and index == 0:
            continue
        fret_line = fret_i.extendSym(-nipping if nipping > 0 else 5)
        blade = geom.extrusion(fret_line.rectSym(tangWidth), 0, [0,0,bladeHeight])
        blade = blade.cut(trim)
        frets.append(blade)
    return frets

def makeInlays(fbd, thickness=-1, inlayDepth=0):
    line = fbd.scaleFrame.midLine
    shapes = []
    edo = getattr(fbd, 'edo', getattr(fbd, 'EDO', 12))
    with traceTime("Prepare inlay pockets geometry"):
        for i in range(len(fbd.frets)):
            inlay = App.ActiveDocument.getObject(f"Marz_FInlay_Fret{i}")
            if inlay:
                ishape = inlay.Shape.copy()
                a = fret(i-1, line.length, edo) if i-1 >= 0 else 0.0
                b = fret(i, line.length, edo)
                p = line.lerpPointAt((a + b) / 2)
                ishape.translate(Vector(p.x, p.y, thickness + 1))
                shapes.append(ishape)
    if shapes:
        if thickness <= 0:
            return Part.makeCompound(shapes)
        return Part.makeCompound(shapes).extrude(Vector(0,0,-inlayDepth-1))
    return None

from freecad.marz.utils.cache import getCachedObject as _getCachedObject
def base(inst, fbd):
    (board, cache) = _getCachedObject('fretboard_base', fbd,
                                     inst.fretboard.startRadius,
                                     inst.fretboard.endRadius,
                                     inst.fretboard.thickness)
    if not board:
        with traceTime("Build fretboard base"):
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
                MarzLogger.warn("It was not possible to fillet the fretboard with radius: {}", inst.fretboard.filletRadius)
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

def fretboard_fillet(fb, radius):
    try:
        from freecad.marz.feature.fretboard import fretboard_fillet as fb_f
        return fb_f(fb, radius)
    except Exception:
        return fb

def createFretboardShape(instrument_model, progress_listener=None):
    if progress_listener is None:
        class _Noop:
            def add(self, *a, **kw): pass
        progress_listener = _Noop()
    progress_listener.add("Updating Fretboard...")
    inst = instrument_model
        # --- DEBUG: inspect instrument_model / fbd ---
    try:
        import FreeCAD
        FreeCAD.Console.PrintMessage("[Marz DEBUG] createFretboardShape called. instrument_model type: {}\n".format(type(inst)))
    except Exception:
        pass
# --- DEBUG end
    fbd = buildFretboardData(inst)
    try: # --- DEBUG: inspect fbd ---
        import FreeCAD
        FreeCAD.Console.PrintMessage("[Marz DEBUG] buildFretboardData produced fbd type: {}; frets_count: {}\n".format(
            type(fbd), len(getattr(fbd, "frets", []))
        ))
    except Exception:
        pass # --- DEBUG end


    inlaysTask = Task.execute(makeInlays, fbd, inst.fretboard.thickness, inst.fretboard.inlayDepth)

    (fretboard, cache) = getCachedObject(
        'FretboardFeature',
        fbd, inst.fretWire.tangWidth, inst.fretWire.tangDepth,
        inst.nut.depth, inst.fretboard.thickness, inst.fretboard.startRadius,
        inst.fretboard.endRadius, inst.fretboard.fretNipping, inst.fretboard.filletRadius, getattr(inst.fretboard, 'length', 0.0)
    )
    try: # --- DEBUG: inspect cached fretboard ---
        import FreeCAD
        FreeCAD.Console.PrintMessage("[Marz DEBUG] cached_fretboard present? {}\n".format(bool(fretboard)))
    except Exception:
        pass

    if not fretboard:
        board, nut, fretSlots = Task.join([Task.execute(t, inst, fbd) for t in [base, nutSlot, fretsCut]])
        try:
            import FreeCAD
            FreeCAD.Console.PrintMessage("[Marz DEBUG] build parts -> board: {}, nut: {}, fretSlots(len): {}\n".format(
                bool(board), bool(nut), len(fretSlots) if hasattr(fretSlots, "__len__") else type(fretSlots)
            ))
        except Exception:
            pass #

    if not fretboard:
        (board, nut, fretSlots) = Task.join([Task.execute(t, inst, fbd) for t in [base, nutSlot, fretsCut]])
        try: # --- DEBUG: inspect built parts ---
            import FreeCAD
            FreeCAD.Console.PrintMessage("[Marz DEBUG] cached_fretboard present? {}\n".format(bool(fretboard)))
        except Exception:
            pass

        if not fretboard:
            board, nut, fretSlots = Task.join([Task.execute(t, inst, fbd) for t in [base, nutSlot, fretsCut]])
            try:
                import FreeCAD
                FreeCAD.Console.PrintMessage("[Marz DEBUG] build parts -> board: {}, nut: {}, fretSlots(len): {}\n".format(
                    bool(board), bool(nut), len(fretSlots) if hasattr(fretSlots, "__len__") else type(fretSlots)
                ))
            except Exception:
                pass # --- DEBUG end
        if board and nut and fretSlots:
            fretboard = board.cut(tuple([*fretSlots, nut]))
            if hasattr(fretboard, 'Solids') and fretboard.Solids:
                fretboard = fretboard.Solids[0].removeSplitter()
        cache(fretboard)

    inlays = inlaysTask.get()
    if inlays and fretboard:
        fretboard = fretboard.cut(inlays)

    progress_listener.add('Fretboard done.')
    try:
        import FreeCAD
        if fretboard:
            FreeCAD.Console.PrintMessage("[Marz DEBUG] final fretboard object truthy; shape attr?: {}\n".format(hasattr(fretboard, "ShapeType")))
        else:
            FreeCAD.Console.PrintMessage("[Marz DEBUG] final fretboard is None/False\n")
    except Exception:
        pass

    return fretboard

def createFretboardPart(fbd, instrument_model=None, progress_listener=None):
    """Create the actual Fretboard Part in the document. Accepts an Instrument model."""
    # If instrument_model supplied, use it to build the shape (so EDO etc come from model)
    import traceback
    try:
        import FreeCAD
        FreeCAD.Console.PrintMessage("[Marz DEBUG] createFretboardPart called; instrument_model type: {}\n".format(type(instrument_model)))
    except Exception:
        pass

    try:
        if instrument_model is not None:
            shape = createFretboardShape(instrument_model, progress_listener)
        else:
            shape = createFretboardShape(fbd, progress_listener)
    except Exception:
        # print full traceback to FreeCAD Report so it can't be silently lost
        try:
            import FreeCAD
            FreeCAD.Console.PrintError("createFretboardPart exception:\n" + traceback.format_exc())
        except Exception:
            print("createFretboardPart exception:\n", traceback.format_exc())
        MarzLogger.warn("createFretboardPart error: {}", traceback.format_exc())
        return

    try:
        import FreeCAD
        FreeCAD.Console.PrintMessage("[Marz DEBUG] createFretboardPart returned shape? {}\n".format(bool(shape)))
    except Exception:
        pass


    try:
        from freecad.marz.feature.document import FretboardPart
        if shape:
            FretboardPart.set(shape)
    except Exception:
        # fallback: create a temporary Part::Feature in the active doc
        doc = App.ActiveDocument
        if doc and shape:
            obj = doc.addObject("Part::Feature", "FretboardTemp")
            obj.Shape = shape
            doc.recompute()

def createConstructionShapes(fbd):
    # delegate to feature.fretboard if available
    try:
        from freecad.marz.feature.fretboard import createConstructionShapes as create_fn
        return create_fn(fbd)
    except Exception:
        shapes = []
        from freecad.marz.feature.document import (RefScaleFrame, RefProjFrame, RefFretboardFrame,
                                                   RefNeckFrame, RefMidLine, RefBridgePos, RefFrets)
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
    @ui_thread()
    def createInUI():
        doc = App.ActiveDocument
        shapes = createConstructionShapes(fbd)
        for parts in group_by(shapes, lambda s: s[0].name).values():
            compound = []
            for target, points in parts:
                compound.append(Part.makePolygon(points))
            try:
                target.set(Part.makeCompound(compound), doc=doc)
            except Exception:
                pass
    createInUI()
