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
from __future__ import annotations

from freecad.marz.model.instrument import Instrument, NeckJoint
from freecad.marz.model.neck_data import NeckData
from freecad.marz.model.fretboard_data import FretboardBox, FretboardData
from freecad.marz.model.custom_neck_profile import CustomNeckProfile
from freecad.marz.model.headstock_builder import getTop, BoundProfile
from freecad.marz.utils import traced, geom, traceTime
from freecad.marz.extension.threading import Task, task
from freecad.marz.extension.fc import App, Vector, Rotation, Placement


from typing import List, Optional
import math

from dataclasses import dataclass

import Part  # type: ignore
from Part import (  # type: ignore
    Edge,
    Face,
    Wire,
    Solid,
    Shell,
    Vertex,
    BSplineCurve,
    LineSegment)

import BOPTools.SplitAPI as split_api # type: ignore

GORDON_DEBUG = False

@dataclass
class NeckProfiles:
    edges: List[Edge]
    nut_edge: Edge

@dataclass
class HeelProfiles:
    edges: List[Edge]
    last: Edge

@dataclass
class NeckBase:
    heel: HeelProfiles
    neck: NeckProfiles
    solid: Solid

@dataclass
class HeadstockPlate:
    solid : Solid
    normal: Vector

def edge_mid_point(edge: Edge) -> Vector:
    mid_param = (edge.FirstParameter + edge.LastParameter) / 2.0
    return edge.valueAt(mid_param)

def heel_profile(support_edge: Edge, inst: Instrument, height: float) -> Edge:
    """
    Vertical edge for the heel (Deep U-shape)
    """
    mid = edge_mid_point(support_edge)
    base_height = inst.body.neckPocketDepth + inst.neck.topOffset

    a = support_edge.valueAt(support_edge.FirstParameter)
    b = a + Vector(0,0,-base_height/2.0)
    c = b + Vector(0,0,-base_height/2.0)
    # Clamp D to be wider than the core but narrower than the fretboard to avoid bulges
    d = Vector(c.x, c.y * 0.9, -height)
    e = Vector(mid.x, mid.y, -height - 1.0)
    i = support_edge.valueAt(support_edge.LastParameter)
    h = i + Vector(0,0,-base_height/2.0)
    g = h + Vector(0,0,-base_height/2.0)
    f = Vector(g.x, g.y * 0.9, -height)

    # Filter unique consecutive points to avoid Standard_ConstructionError
    all_pts = [a,b,c,d,e,f,g,h,i]
    pts = [all_pts[0]]
    for p in all_pts[1:]:
        if (p - pts[-1]).Length > 1e-5:
            pts.append(p)

    # Resample to exactly 31 points to match parametric profiles
    bsp = Part.BSplineCurve()
    bsp.interpolate(pts)
    edge = bsp.toShape()
    points = edge.discretize(Number=30)
    bsp_final = Part.BSplineCurve()
    bsp_final.interpolate(points)
    return bsp_final.toShape()

def heel_support_edge(cut_edge: Edge, box: FretboardBox) -> Edge:
    """
    Create an edge between treble and bass to be used as support for a heel profile

    :param Edge cut_edge: a source edge that intersects treble and bas
    :param FretboardBox box: Neck Frame
    :return Edge: An edge to be used as support
    """
    dist, vecs, info = cut_edge.distToShape(box.bass.edge())
    (a, _), *rest = vecs
    dist, vecs, info = cut_edge.distToShape(box.treble.edge())
    (b, _), *rest = vecs

    # Return from Bass (negative Y) to Treble (positive Y)
    if a.y < b.y:
        return LineSegment(a, b).toShape()
    else:
        return LineSegment(b, a).toShape()

@traced("Gordon Neck: Heel profiles")
def heel_profiles(inst: Instrument, fbd: FretboardData, neckd: NeckData) -> HeelProfiles:
    """
    Vertical edges at the end of the heel
    """

    # We use a height proportional to the actual neck thickness
    # at the heel to avoid oversized joints.
    nut_v = fbd.frame.nut.mid()
    bridge_v = fbd.neckFrame.bridge.mid()
    dist = abs(bridge_v.x - nut_v.x)
    # Ensure heel reaches the bottom of the neck pocket
    height = max(neckd.thicknessAt(dist), inst.body.neckPocketDepth + inst.neck.topOffset) + 2.0

    bridge = fbd.neckFrame.bridge.edge()
    mid = edge_mid_point(bridge)

    # Reduced steps for stability and performance
    steps = 3
    joint_fret_x = neckd.lineToFret(inst.neck.jointFret).end.x
    delta = abs(mid.x - joint_fret_x) / steps
    rot = Rotation(Vector(0,0,1), 0)

    edge1 = heel_profile(bridge, inst, height)
    edges = [edge1]
    p1 = edge1.Placement.Base
    neck_frame = fbd.neckFrame
    for step in range(1, steps):
        # We must transform the support edge, not just set Placement,
        # so that distToShape works correctly in global space.
        support_wire = bridge.copy()
        support_wire.transformShape(Placement(Vector(p1.x + step * delta, p1.y, p1.z), rot).toMatrix())
        edge_n = heel_profile(heel_support_edge(support_wire, neck_frame), inst, height)
        edges.append(edge_n)

    sorted_edges = list(reversed(edges))
    return HeelProfiles(sorted_edges, edge1)

@traced("Gordon Neck: Barrel profiles")
def neck_profiles(inst: Instrument, fbd: FretboardData, neckd: NeckData) -> NeckProfiles:
    """
    Generate neck profiles except Heel profiles and Headstock profile
    """
    # Reduced steps for stability and performance
    steps = 4
    if inst.neck.joint == NeckJoint.EXTRA_CHUNK:
        # For extra chunk, we extend the neck profiles up to the actual end of the fretboard
        edge = fbd.frame.midLine.edge()
        length = edge.Length
    else:
        edge = neckd.lineToFret(inst.neck.jointFret).edge()
        length = edge.Length

    curve = edge.Curve
    step = length / steps
    points = edge.discretize(Distance=step)
    direction = curve.Direction
    # Profile is built in XY plane (normal = Vector(0,0,1)).
    # We rotate it to align with the neck's longitudinal direction.
    rot = Rotation(Vector(0, 0, 1), direction)
    profile = CustomNeckProfile(doc=inst.doc if hasattr(inst, 'doc') else App.activeDocument())
    thicknessAt = neckd.thicknessAt
    widthAt = neckd.widthAt
    transition_offset = 20.0 # TODO: Bind a parameter

    def profile_edge(i, l=None, point=None, h_delta=0.0):
        if l is None:
            l = i * step
        h = thicknessAt(l) + h_delta
        if point is None:
            point = Vector(points[i].x, points[i].y, points[i].z)
        w = widthAt(l)
        p = profile(w, h, wire=False)
        if p:
            # Transform shape directly to ensure Tigl consumes correctly positioned curves
            p.transformShape(Placement(point, rot).toMatrix())
        return p

    # Edges between nut and transition start
    edges = [profile_edge(i) for i in range(steps + 1)]
    edges = [w for w in edges if w is not None and not w.isNull()]

    if not edges:
        # High stability fallback if no profiles can be generated
        return NeckProfiles([], None)

    nut_profile = edges[0]

    # Edge towards heel to force tangency
    if inst.neck.joint != NeckJoint.EXTRA_CHUNK:
        offset = length + 10.0
        control_edge = profile_edge(0, offset, points[0] + direction * offset)
        edges.append(control_edge)

    # space = 1.5
    # Edge for headstock transition start
    # edges.insert(1, profile_edge(0, transition_offset*space, points[0] + direction * transition_offset*space))
    edges.insert(1, profile_edge(0, transition_offset, points[0] + direction * transition_offset))

    # Edges before nut to force the guides pass for nut point
    # Increased distance to 10mm for better Gordon stability at headstock join
    control = -10.0  # Issue #40
    edges.insert(0, profile_edge(0, control, points[0] + direction * control))

    return NeckProfiles(edges, nut_profile)

@traced("Gordon Neck: Headstock base profile")
def headstock_end_profile(inst: Instrument, fbd: FretboardData, neckd: NeckData) -> Edge:
    """
    Generate Last profile (Deep U-shape for headstock join)
    """
    profile = BoundProfile(CustomNeckProfile(doc=inst.doc if hasattr(inst, 'doc') else App.activeDocument()), fbd.widthAt, neckd.thicknessAt)
    line = fbd.neckFrame.midLine
    pos = Vector(line.start.x, line.start.y, 0)
    gross_thickness = inst.headStock.thickness + (0 if inst.headStock.angle > 0 else inst.headStock.depth)

    # !important: angle = 0
    _, transition_edge = getTop(pos, 0, inst.headStock.width, inst.headStock.length, profile, 30)

    if transition_edge is None or transition_edge.isNull():
        return None

    mid = transition_edge.CenterOfMass
    height = gross_thickness

    # Robust endpoint selection for custom SVG wires
    vxs = sorted(transition_edge.Vertexes, key=lambda v: v.Point.y)
    # If Y is very similar (vertical transition), sort by X
    if abs(vxs[0].Point.y - vxs[-1].Point.y) < 1e-3:
        vxs = sorted(transition_edge.Vertexes, key=lambda v: v.Point.x)

    a = vxs[0].Point
    i = vxs[-1].Point

    b = a + Vector(0,0,-height/2.0)
    c = b + Vector(0,0,-height/2.0)
    # Taper the transition depth corners slightly to prevent Gordon from bulging
    d = Vector(c.x, c.y * 0.95, -height)
    e = Vector(mid.x, mid.y, -height - 1.0)
    h = i + Vector(0,0,-height/2.0)
    g = h + Vector(0,0,-height/2.0)
    f = Vector(g.x, g.y * 0.95, -height)

    # Note: We must ensure `all_pts` flows in the exact same orientation as neck profiles.
    # Neck profiles are forced to Bass -> Treble (negative Y -> positive Y) in neck_blank.
    # We sort by Y so it always goes from Bass to Treble reliably.
    if a.y > i.y:
        all_pts = [i, h, g, f, e, d, c, b, a]
    elif abs(a.y - i.y) < 1e-3 and a.x > i.x:
        # Fallback for vertical edges: sort by X
        all_pts = [i, h, g, f, e, d, c, b, a]
    else:
        all_pts = [a, b, c, d, e, f, g, h, i]

    # Filter unique consecutive points to avoid Standard_ConstructionError
    pts = [all_pts[0]]
    for p in all_pts[1:]:
        if (p - pts[-1]).Length > 1e-5:
            pts.append(p)

    # Resample to exactly 31 points to match parametric profiles
    bsp = Part.BSplineCurve()
    bsp.interpolate(pts)
    edge = bsp.toShape()
    points = edge.discretize(Number=30)
    bsp_final = Part.BSplineCurve()
    bsp_final.interpolate(points)
    return bsp_final.toShape()

@traced("Gordon Neck: Guides")
def create_guides(profiles: List[Edge]) -> List[Edge]:
    """
    Create BSpline Guides by points on each profile.
    Ensures all guides pass through every profile in the provided list.
    """
    # Standard resolution for stability (12 points)
    num_points = 12
    out = []
    # Discretize each profile into points
    profile_points = [e.discretize(Number=num_points) for e in profiles]

    if not profile_points:
        return []

    # Safety: ensure we don't exceed the number of points in any profile
    points_count = min(len(p) for p in profile_points)

    for i in range(points_count):
        guide_points = [points[i] for points in profile_points]

        # Filter unique points for guide spline
        gpts = [guide_points[0]]
        for p in guide_points[1:]:
            if (p - gpts[-1]).Length > 1e-4:
                gpts.append(p)

        if len(gpts) > 1:
            try:
                bsp = Part.BSplineCurve()
                bsp.interpolate(gpts)
                out.append(bsp.toShape())
            except Exception:
                pass
    return out

@task
def top_cut(inst: Instrument, pos: Vector, angle: float) -> Task[Solid]:
    if angle <= 0:
        return top_cut_flat(pos, inst.headStock.depth, inst.headStock.topTransitionLength, inst.headStock.voluteOffset)
    else:
        return top_cut_angled(pos, angle, inst.headStock.voluteOffset)

def volute_cut(inst: Instrument, headstock_transition_wire: Wire, gross_thickness: float, neck_base: Task[NeckBase], headstock: Task[HeadstockPlate]) -> Task[Solid]:
    if inst.headStock.voluteRadius > 0:
        return volute_cutter_arc(inst.headStock.voluteRadius, headstock_transition_wire, gross_thickness, headstock().normal)
    else:
        return volute_cutter_flat(headstock_transition_wire, gross_thickness, neck_base().neck.nut_edge, headstock().normal)

@traced("Gordon Neck")
def gordon_neck(inst: Instrument, fbd: FretboardData, neckd: NeckData):
    _profile = CustomNeckProfile(doc=inst.doc if hasattr(inst, 'doc') else App.activeDocument())
    profile = BoundProfile(_profile, fbd.widthAt, neckd.thicknessAt)
    line = fbd.neckFrame.midLine
    pos = Vector(line.start.x, line.start.y, 0)
    angle = math.radians(inst.headStock.angle)
    gross_thickness = inst.headStock.thickness + (0 if angle > 0 else inst.headStock.depth)
    headstock_contour_wire, headstock_transition_wire = getTop(pos, angle, inst.headStock.width, inst.headStock.length, profile, 30)

    t_blank = neck_blank(inst, fbd, neckd)
    t_headstock = extrude_headstock_plate(headstock_contour_wire, gross_thickness, headstock_transition_wire)
    t_top_cut = top_cut(inst, pos, angle)
    t_pockets = pocket_cut(angle, pos, inst.headStock.depth)
    t_volute_cut = volute_cut(inst, headstock_transition_wire, gross_thickness, t_blank, t_headstock)

    with traceTime('Gordon Neck: Base + Headstock'):
        # High tolerance to avoid possible self intersecting artifacts
        blank_data = t_blank()
        headstock_data = t_headstock()

        # Ensure solids are valid before fusion
        blank_solid = blank_data.solid if blank_data and blank_data.solid and not blank_data.solid.isNull() and blank_data.solid.isValid() else None
        headstock_solid = headstock_data.solid if headstock_data and not headstock_data.solid.isNull() and headstock_data.solid.isValid() else None

        if blank_solid and headstock_solid:
            try:
                pre_assemble = blank_solid.fuse([headstock_solid], 1e-2)
            except Exception:
                # If fusion fails, use a compound as fallback to avoid Null shape error
                pre_assemble = Part.Compound([blank_solid, headstock_solid])
        elif blank_solid:
            pre_assemble = blank_solid
        elif headstock_solid:
            pre_assemble = headstock_solid
        else:
            # Fallback to empty compound instead of raising error
            pre_assemble = Part.Compound([])

        # Ensure we always have a shape object to avoid failures in downstream cuts
        if pre_assemble is None:
             pre_assemble = Part.Compound([])

    with traceTime('Gordon Neck: Collect Top, Volute, Pockets'):
        tools = [t_volute_cut(), t_top_cut()]
        tool_cut_pockets = t_pockets()
        if tool_cut_pockets:
            tools.append(tool_cut_pockets)

    with traceTime('Gordon Neck: Assembly - Top - Bottom - Pockets'):
        try:
            pre_assemble = pre_assemble.cut(tools, 1e-2)
        except Exception:
            pass # Keep uncut assembly if cutting fails

    with traceTime('Gordon Neck: Refine'):
        try:
            assemble = pre_assemble.removeSplitter()
        except Exception:
            assemble = pre_assemble

    return assemble

def edge_to_closed_face(edge: Edge) -> Face:
    """
    Takes a single curved Edge, create a segment between extremes and build a face
    """
    p1 = edge.valueAt(edge.FirstParameter)
    p2 = edge.valueAt(edge.LastParameter)

    # Check if already closed
    if (p1 - p2).Length < 1e-5:
        try:
            # Re-discretize to ensure a clean closed wire
            pts = edge.discretize(Number=50)
            return Face(Part.makePolygon(pts + [pts[0]]))
        except Exception:
            pass

    try:
        # Standard U-shape profiles are built from Bass to Treble (or vice versa).
        # To ensure the Face is correctly built, we use a polygon which is more robust.
        pts = edge.discretize(Number=50)
        # Add the straight line segment by closing back to the first point
        w = Part.makePolygon(pts + [pts[0]])
        return Face(w)
    except Exception:
        # Absolute fallback if Face construction fails
        pnt_list = [edge.valueAt(edge.FirstParameter), edge.valueAt(edge.LastParameter),
                    edge.valueAt((edge.FirstParameter + edge.LastParameter)/2)]
        return Face(Part.makePolygon(pnt_list + [pnt_list[0]]))

@traced("Gordon Neck: Headstock profile rotation")
def apply_headstock_angle(edge: Edge, angle_deg: float, fbd: FretboardData) -> Edge:
    """
    Applies headstock rotation to edge (Headstock profile)

    :param Edge edge: raw headstock profile
    :param float angle_deg: headstock angle
    :param FretboardData fbd: Fretboard geometry
    :return Edge: Rotated and adjusted edge
    """
    line = fbd.neckFrame.midLine
    pos = Vector(line.start.x, line.start.y, 0) # Nut position
    _edge = edge.copy() # working edge

    # Reference place where all profiles must be supported
    plane = Part.makePlane(200, 200, Vector(0,-100,0))

    # Rotate the edge, it will be in bad state because extremes are not anymore in plane
    _edge.rotate(pos, Vector(0,1,0), angle_deg)

    segments = [] # Segments to reconstruct the edge

    # Create first edge complete projection to plane
    vx0 = _edge.Vertexes[0]
    tan1 = _edge.tangentAt(_edge.FirstParameter) * -1000
    ray = LineSegment(vx0.Point, vx0.Point + tan1).toShape()
    dist, vecs, infos = ray.distToShape(plane)
    if dist <= 1e-7:
        (self_p, plan_p), *rest = vecs
        curve = LineSegment(plan_p, vx0.Point)
        if curve.length() > 1e-7:
            segments.append(curve.toShape())

    # put original rotated edge in the middle
    segments.append(_edge)

    # Create last edge complete projection to plane
    vx1 = _edge.Vertexes[1]
    tan1 = _edge.tangentAt(_edge.LastParameter) * 1000
    ray = LineSegment(vx1.Point, vx1.Point + tan1).toShape()
    dist, vecs, infos = ray.distToShape(plane)
    if dist <= 1e-7:
        (self_p, plan_p), *rest = vecs
        segments.append(LineSegment(vx1.Point, plan_p).toShape())

    # Reconstruct a BSpline edge
    bspline = Wire(segments)

    # Re-parameterize the curve to make it homogeneous
    points = bspline.discretize(Number=100)
    bsp = Part.BSplineCurve()
    bsp.interpolate(points)
    return bsp.toShape()

@task
@traced('Gordon Neck: Base')
def neck_blank(inst: Instrument, fbd: FretboardData, neckd: NeckData) -> Task[NeckBase]:
    from freecad.marz.feature.logging import MarzLogger

    if inst.neck.joint == NeckJoint.EXTRA_CHUNK:
        # Task must return NeckBase object directly
        return neck_blank_extra_chunk_impl(inst, fbd, neckd)

    # Heel Profiles
    heel = heel_profiles(inst, fbd, neckd)

    # Barrel profiles
    profiles = neck_profiles(inst, fbd, neckd)

    # Headstock final profile
    headstock = headstock_end_profile(inst, fbd, neckd)
    if headstock is not None and inst.headStock.angle > 0.5:
        headstock = apply_headstock_angle(headstock, inst.headStock.angle, fbd)

    raw_profiles = (headstock, *profiles.edges, *heel.edges)

    # Re-parameterize profiles to make it smooth
    all_profiles = []
    for index, e in enumerate(raw_profiles):
        if e is None or e.isNull():
            MarzLogger.warn(f"Gordon Neck: Profile {index} is None or Null. Skipping.")
            continue

        # Standardize orientation: Always Bass to Treble (Negative Y to Positive Y)
        # This is critical for Gordon surface interpolation to succeed.
        p_start = e.valueAt(e.FirstParameter)
        p_end = e.valueAt(e.LastParameter)
        if p_start.y > p_end.y:
            e = e.reversed()
        elif abs(p_start.y - p_end.y) < 1e-3:
            # For nearly vertical edges (e.g. some custom headstocks), sort by X to ensure consistency
            if p_start.x > p_end.x:
                e = e.reversed()

        # Resample to exactly 31 points (Number=30 gives 31 points)
        points = e.discretize(Number=30)
        bsp = Part.BSplineCurve()
        bsp.interpolate(points)
        all_profiles.append(bsp.toShape())

    # Very strict filter for performance (20mm)
    all_profiles.sort(key=lambda e: e.CenterOfMass.x)
    profiles_edges = []
    if all_profiles:
        profiles_edges.append(all_profiles[0])
        for i in range(1, len(all_profiles)):
            dist = abs(all_profiles[i].CenterOfMass.x - profiles_edges[-1].CenterOfMass.x)
            
            # The nut profile is near X=0. In multiscale Extra Chunk, the frame margins and slant
            # shift the nut profile slightly (usually up to 10-15mm). We use a safe anchor radius of 15.0mm.
            # Profiles in the headstock transition zone (X > 0.1) are also kept to maintain shape detail.
            is_anchor = abs(all_profiles[i].CenterOfMass.x) < 15.0 or all_profiles[i].CenterOfMass.x > 0.1
            
            # Prevent scrunched geometry and system crashes by keeping intermediate profiles sparse (min 10.0mm)
            if dist < 10.0 and not is_anchor and i != len(all_profiles) - 1:
                continue
                
            if dist > 20.0 or is_anchor or i == len(all_profiles) - 1:
                profiles_edges.append(all_profiles[i])

    # Create guides
    guides = create_guides(profiles_edges)

    # Neck gordon surface
    with traceTime("Gordon Neck: InterpolateCurveNetwork"):
        if not profiles_edges:
             MarzLogger.error("Gordon Neck: No profiles generated for neck blank.")
             return NeckBase(heel, profiles, None)
        if not guides:
             MarzLogger.error("Gordon Neck: No guides generated for neck blank.")
             return NeckBase(heel, profiles, None)

        tol_3d = 1.0
        tol_2d = 1.0
        guide_curves = [e.Curve.toBSpline(e.FirstParameter, e.LastParameter) for e in guides]
        prof_curves = [e.Curve.toBSpline(e.FirstParameter, e.LastParameter) for e in profiles_edges]

        solid = None
        face_gordon = None

        try:
            from freecad.marz.curves.gordon import InterpolateCurveNetwork
            interp = InterpolateCurveNetwork(prof_curves, guide_curves, tol_3d, tol_2d)
            face_gordon = interp.surface()
        except Exception as ex:
            MarzLogger.warn(f"Gordon Neck: Interpolation failed: {ex}. Falling back to Loft.")
            face_gordon = None

        if face_gordon and not face_gordon.isNull():
            # Robust boundary extraction
            outer_edges = face_gordon.OuterWire.Edges
            def get_x_extent(edge):
                bbox = edge.BoundBox
                return bbox.XMax - bbox.XMin

            try:
                # 1. Identify ends (transversal) and sides (longitudinal)
                sorted_by_x_ext = sorted(outer_edges, key=get_x_extent)
                ends = sorted(sorted_by_x_ext[:2], key=lambda e: e.CenterOfMass.x)
                sides = sorted(sorted_by_x_ext[2:], key=lambda e: e.CenterOfMass.y)

                heel_edge, headstock_edge = ends[0], ends[1]
                side_bass, side_treble = sides[0], sides[1]

                # 2. Extract oriented boundary vertices for top face construction
                # Orient side edges consistently from Heel to Headstock for perfect shell closure
                if side_bass.valueAt(side_bass.FirstParameter).x > side_bass.valueAt(side_bass.LastParameter).x:
                    side_bass = side_bass.reversed()
                if side_treble.valueAt(side_treble.FirstParameter).x > side_treble.valueAt(side_treble.LastParameter).x:
                    side_treble = side_treble.reversed()

                v_h_b = side_bass.Vertexes[0].Point
                v_hs_b = side_bass.Vertexes[1].Point
                v_h_t = side_treble.Vertexes[0].Point
                v_hs_t = side_treble.Vertexes[1].Point

                # 3. Construct Top Face (Glue surface)
                # Using actual Gordon boundaries ensures bit-perfect shell closure.
                top_wire = Wire([side_bass, LineSegment(v_hs_b, v_hs_t).toShape(), side_treble.reversed(), LineSegment(v_h_t, v_h_b).toShape()])
                face_fretboard = Face(top_wire)

                face_heel = edge_to_closed_face(heel_edge)
                face_headstock = edge_to_closed_face(headstock_edge)

                shell = Shell([face_heel, face_gordon, face_fretboard, face_headstock])

                # Aggressively fix and close the shell
                if not shell.isClosed():
                    shell.fix(0.5, 0.5, 0.5)

                solid = Part.makeSolid(shell)
                if solid.isNull() or not solid.isValid():
                    solid = Part.makeSolid(shell.fix(0.5, 0.5, 0.5))
                if solid.isNull() or not solid.isValid():
                    solid = None
            except Exception:
                solid = None

        if solid is None or solid.isNull():
            # Final fallback: Loft ensures the neck always has volume
            closed_profiles = []
            for e in profiles_edges:
                p1 = e.valueAt(e.FirstParameter)
                p2 = e.valueAt(e.LastParameter)
                if (p1 - p2).Length > 1e-4:
                    line = Part.LineSegment(p2, p1).toShape()
                    try:
                        closed_profiles.append(Part.Wire([e, line]))
                    except Exception:
                        closed_profiles.append(Part.Wire([e]))
                else:
                    try:
                        closed_profiles.append(Part.Wire([e]))
                    except Exception:
                        pass

            try:
                solid = Part.makeLoft(closed_profiles, True, True)
            except Exception:
                try:
                    solid = Part.makeLoft(closed_profiles, True, False)
                except Exception:
                    solid = None

    # Cut the excess part of the heel
    try:
        pnt = heel.edges[-1].valueAt(heel.edges[-1].FirstParameter)
        cut_plane_size = 500.0
        pnt.y = -cut_plane_size/2
        pnt.x -= 20
        # Shift Z down slightly to prevent slicing EXACTLY on the bottom boundary
        pnt.z = -inst.body.neckPocketDepth - inst.neck.topOffset - 0.5
        plane = Part.makePlane(cut_plane_size, cut_plane_size, pnt, Vector(0,0,1), Vector(1,0,0))
        res = split_api.slice(solid, [plane], 'CompSolid')
        part = None
        if res and hasattr(res, 'Solids') and len(res.Solids) > 0:
            part = geom.query_one(res.Solids, order_by=lambda s: -s.CenterOfMass.z)

        if part is None or part.isNull():
            part = solid
    except Exception:
        part = solid

    return NeckBase(heel, profiles, part)

def neck_blank_extra_chunk_impl(inst: Instrument, fbd: FretboardData, neckd: NeckData) -> NeckBase:
    from freecad.marz.feature.logging import MarzLogger
    # Barrel profiles up to the end of fretboard
    profiles = neck_profiles(inst, fbd, neckd)

    # Headstock final profile
    headstock = headstock_end_profile(inst, fbd, neckd)
    if headstock is not None and inst.headStock.angle > 0.5:
        headstock = apply_headstock_angle(headstock, inst.headStock.angle, fbd)

    raw_profiles = (headstock, *profiles.edges)

    # Re-parameterize profiles
    all_profiles = []
    for index, e in enumerate(raw_profiles):
        if e is None or e.isNull():
            MarzLogger.warn(f"Gordon Neck (Extra Chunk): Profile {index} is None or Null. Skipping.")
            continue

        # Standardize orientation: Always Bass to Treble (Negative Y to Positive Y)
        p_start = e.valueAt(e.FirstParameter)
        p_end = e.valueAt(e.LastParameter)
        if p_start.y > p_end.y:
            e = e.reversed()
        elif abs(p_start.y - p_end.y) < 1e-3:
            if p_start.x > p_end.x:
                e = e.reversed()

        # Resample to exactly 31 points
        points = e.discretize(Number=30)
        bsp = Part.BSplineCurve()
        bsp.interpolate(points)
        all_profiles.append(bsp.toShape())

    # Sort and filter close profiles (20mm filter)
    all_profiles.sort(key=lambda e: e.CenterOfMass.x)
    profiles_edges = []
    if all_profiles:
        profiles_edges.append(all_profiles[0])
        for i in range(1, len(all_profiles)):
            dist = abs(all_profiles[i].CenterOfMass.x - profiles_edges[-1].CenterOfMass.x)
            
            # The nut profile is near X=0. In multiscale Extra Chunk, the frame margins and slant
            # shift the nut profile slightly (usually up to 10-15mm). We use a safe anchor radius of 15.0mm.
            # Profiles in the headstock transition zone (X > 0.1) are also kept to maintain shape detail.
            is_anchor = abs(all_profiles[i].CenterOfMass.x) < 15.0 or all_profiles[i].CenterOfMass.x > 0.1
            
            # Prevent scrunched geometry and system crashes by keeping intermediate profiles sparse (min 10.0mm)
            if dist < 10.0 and not is_anchor and i != len(all_profiles) - 1:
                continue
                
            if dist > 20.0 or is_anchor or i == len(all_profiles) - 1:
                profiles_edges.append(all_profiles[i])
    guides = create_guides(profiles_edges)

    # Neck gordon surface
    tol_3d = 1.0
    tol_2d = 1.0
    guide_curves = [e.Curve.toBSpline(e.FirstParameter, e.LastParameter) for e in guides]
    prof_curves = [e.Curve.toBSpline(e.FirstParameter, e.LastParameter) for e in profiles_edges]

    neck_solid = None
    face_gordon = None

    try:
        from freecad.marz.curves.gordon import InterpolateCurveNetwork
        interp = InterpolateCurveNetwork(prof_curves, guide_curves, tol_3d, tol_2d)
        face_gordon = interp.surface()
    except Exception as ex:
        MarzLogger.warn(f"Gordon Neck (Extra Chunk): Interpolation failed: {ex}. Falling back to Loft.")
        face_gordon = None

    if face_gordon and not face_gordon.isNull():
        # Robust boundary extraction
        outer_edges = face_gordon.OuterWire.Edges
        def get_x_extent(edge):
            bbox = edge.BoundBox
            return bbox.XMax - bbox.XMin

        try:
            # 1. Identify ends and sides
            sorted_by_x_ext = sorted(outer_edges, key=get_x_extent)
            ends = sorted(sorted_by_x_ext[:2], key=lambda e: e.CenterOfMass.x)
            sides = sorted(sorted_by_x_ext[2:], key=lambda e: e.CenterOfMass.y)

            heel_edge, headstock_edge = ends[0], ends[1]
            side_bass, side_treble = sides[0], sides[1]

            # 2. Extract oriented boundary vertices
            if side_bass.valueAt(side_bass.FirstParameter).x > side_bass.valueAt(side_bass.LastParameter).x:
                side_bass = side_bass.reversed()
            if side_treble.valueAt(side_treble.FirstParameter).x > side_treble.valueAt(side_treble.LastParameter).x:
                side_treble = side_treble.reversed()

            v_h_b = side_bass.Vertexes[0].Point
            v_hs_b = side_bass.Vertexes[1].Point
            v_h_t = side_treble.Vertexes[0].Point
            v_hs_t = side_treble.Vertexes[1].Point

            # 3. Construct Top Face
            top_wire = Wire([side_bass, LineSegment(v_hs_b, v_hs_t).toShape(), side_treble.reversed(), LineSegment(v_h_t, v_h_b).toShape()])
            face_fretboard = Face(top_wire)
            face_heel = edge_to_closed_face(heel_edge)
            face_headstock = edge_to_closed_face(headstock_edge)

            shell = Shell([face_heel, face_gordon, face_fretboard, face_headstock])

            # Aggressively fix and close
            if not shell.isClosed():
                shell.fix(0.5, 0.5, 0.5)

            neck_solid = Part.makeSolid(shell.fix(0.5, 0.5, 0.5))
        except Exception:
            neck_solid = None

    if neck_solid is None or neck_solid.isNull():
        closed_profiles = []
        for e in profiles_edges:
            p1 = e.valueAt(e.FirstParameter)
            p2 = e.valueAt(e.LastParameter)
            if (p1 - p2).Length > 1e-4:
                line = Part.LineSegment(p2, p1).toShape()
                try:
                    closed_profiles.append(Part.Wire([e, line]))
                except Exception:
                    closed_profiles.append(Part.Wire([e]))
            else:
                try:
                    closed_profiles.append(Part.Wire([e]))
                except Exception:
                    pass

        try:
            neck_solid = Part.makeLoft(closed_profiles, True, True)
        except Exception:
            try:
                neck_solid = Part.makeLoft(closed_profiles, True, False)
            except Exception:
                neck_solid = None

    # Create Extra Chunk
    # Use dimensions at the end of the fretboard (physical wood end)
    # Note: Workbench coordinates have Nut at X=0, Fretboard end at X < 0
    nut_v = fbd.frame.nut.mid()
    fend_v = fbd.frame.bridge.mid()

    # Slice the neck solid exactly at the physical fretboard end (fend_v.x)
    # to prevent any Gordon surface artifacts or extension past the wood boundary.
    try:
        # Only slice if the solid extends past the slice plane (fend_v.x)
        if neck_solid and not neck_solid.isNull():
            bbox = neck_solid.BoundBox
            # If XMin is strictly less than the fretboard end, there is a bulge to slice
            # Only slice if there is a massive protrusion (> 1.0mm) to avoid OCC topological faults 
            # (Map entry 0 is empty) caused by slicing nearly-coincident Gordon surface boundaries.
            # Small bulges will be swallowed by the extra chunk solid box anyway.
            # Only slice if there is a protrusion (> 0.5mm) to avoid OCC topological faults 
            # (Map entry 0 is empty) caused by slicing nearly-coincident Gordon surface boundaries.
            if bbox.XMin < fend_v.x - 0.5:
                # Use a dynamically sized bounding box cut instead of slice to robustly avoid OpenCASCADE 
                # topological boundary faults on slanted multiscale Gordon surfaces.
                cut_y_size = (bbox.YMax - bbox.YMin) * 2.0 + 100.0
                cut_z_size = (bbox.ZMax - bbox.ZMin) * 2.0 + 100.0
                cut_x_size = abs(fend_v.x - bbox.XMin) + 100.0
                
                cutter = Part.makeBox(cut_x_size, cut_y_size, cut_z_size, Vector(fend_v.x - cut_x_size, bbox.YMin - 50.0, bbox.ZMin - 50.0))
                try:
                    cut_solid = neck_solid.cut(cutter, 1e-4)
                    if cut_solid and not cut_solid.isNull():
                        neck_solid = cut_solid
                except Exception:
                    pass
    except Exception:
        pass

    dist_at_end = abs(fend_v.x - nut_v.x)

    width = fbd.widthAt(dist_at_end)
    thickness = neckd.thicknessAt(dist_at_end)

    # Parameters
    chunk_length = float(inst.neck.extraChunkLength)
    v_offset = float(inst.neck.extraChunkVerticalOffset) # Longitudinal (X)
    d_offset = float(inst.neck.extraChunkDepthOffset)    # Thickness (Z)
    chunk_thickness = float(inst.neck.extraChunkThickness)
    conn_thickness = float(inst.neck.extraChunkVerticalThickness)
    top_offset = float(getattr(inst.neck, 'extraChunkTopOffset', 0.0))

    def tapered_box(x1, x2, z_bottom, z_top):
        # fbd.widthAt expects distance from midline start.
        # neckFrame.midLine.start.x is near nut.
        line_start_x = fbd.neckFrame.midLine.start.x
        d1 = abs(x1 - line_start_x)
        d2 = abs(x2 - line_start_x)
        w1 = fbd.widthAt(d1)
        w2 = fbd.widthAt(d2)

        p1 = Vector(x1, -w1/2, z_bottom)
        p2 = Vector(x1,  w1/2, z_bottom)
        p3 = Vector(x1,  w1/2, z_top)
        p4 = Vector(x1, -w1/2, z_top)
        wire1 = Part.makePolygon([p1, p2, p3, p4, p1])

        p5 = Vector(x2, -w2/2, z_bottom)
        p6 = Vector(x2,  w2/2, z_bottom)
        p7 = Vector(x2,  w2/2, z_top)
        p8 = Vector(x2, -w2/2, z_top)
        wire2 = Part.makePolygon([p5, p6, p7, p8, p5])

        return Part.makeLoft([Part.Wire(wire1), Part.Wire(wire2)], True)

    # L-shaped logic:
    # 1. Thin box (the "__" part): This part is at d_offset and has chunk_thickness.
    # 2. Connection box (the "|" part): This part bridges the neck to the thin box,
    #    starting EXACTLY from the fretboard end and extending towards the nut.

    # Nut is at 0 (approx), Fretboard end is at fend_v.x (negative X)

    # 1. Thin Box (Longitudinal extension)
    # The chunk starts at the end of the fretboard (fend_v.x) + v_offset, and extends towards the bridge (-X).
    chunk_start_x = fend_v.x + v_offset
    chunk_end_x = chunk_start_x - chunk_length

    # Z Coordinates for thin box
    z_thin_bottom = -thickness - d_offset
    z_thin_top = z_thin_bottom + chunk_thickness
    z_thin_top = min(z_thin_top, -top_offset)
    actual_thin_thickness = max(z_thin_top - z_thin_bottom, 0.1)

    # 2. Connection Box (Vertical connector)
    # The connector must bridge the gap between the actual neck end (fend_v.x) and the chunk start.
    # It bridges from the neck's bottom (Z=-thickness) to the chunk's bottom (Z=z_thin_bottom).
    # Its leftmost wall MUST be at the physical fretboard end (fend_v.x).
    conn_left_x = fend_v.x
    conn_right_x = conn_left_x + conn_thickness

    # Z coordinates for connection box
    # It must span from the lowest point (z_thin_bottom or -thickness)
    # up to the highest point (perfectly flat with neck top surface Z=0 minus top_offset).
    z_conn_bottom = min(z_thin_bottom, -thickness)
    z_conn_top = -top_offset

    chunk_conn = tapered_box(conn_left_x - 0.5, conn_right_x + 0.5, z_conn_bottom, z_conn_top)

    # The main chunk spans from the far bridge end (chunk_end_x) up to the connector/neck end.
    x_bridge = min(chunk_end_x, chunk_start_x)
    x_nut = max(chunk_start_x, conn_left_x)
    
    if actual_thin_thickness > 0:
        chunk_main = tapered_box(x_bridge, x_nut, z_thin_bottom, z_thin_top)
    else:
        chunk_main = None

    # Fusion
    if chunk_main:
        chunk = chunk_main.fuse(chunk_conn)
    else:
        chunk = chunk_conn

    # Combine chunk with the neck
    combined = neck_solid
    if neck_solid is None or neck_solid.isNull() or not neck_solid.isValid():
        if chunk is not None and not chunk.isNull() and chunk.isValid():
            combined = chunk
    else:
        try:
            if chunk is not None and not chunk.isNull() and chunk.isValid():
                # To ensure robust fusion, we can use a compound if fuse fails
                try:
                    fused = neck_solid.fuse(chunk, 1e-2)
                    if fused and not fused.isNull() and fused.isValid():
                        combined = fused
                    else:
                        # Falling back to compound if fusion fails
                        combined = Part.Compound([neck_solid, chunk])
                except Exception:
                    combined = Part.Compound([neck_solid, chunk])
        except Exception:
            # Absolute fallback to compound
            if neck_solid is not None and chunk is not None:
                 combined = Part.Compound([neck_solid, chunk])
            elif neck_solid is not None:
                 combined = neck_solid
            else:
                 combined = chunk

    # Heel structure for NeckBase (might be needed by other parts of the code)
    # Creating a dummy HeelProfiles for EXTRA_CHUNK
    heel = HeelProfiles([], profiles.edges[-1])

    final_combined = combined
    if combined is not None and not combined.isNull():
        try:
            final_combined = combined.removeSplitter()
        except Exception:
            final_combined = combined

    return NeckBase(heel, profiles, final_combined)

@task
@traced("Gordon Neck: Volute Arc")
def volute_cutter_arc(radius, transition_wire, thickness, plate_normal) -> Task[Solid]:
    """Generate solid to cut from the bottom of the headstock"""
    if transition_wire is None or transition_wire.isNull():
        return None

    # Robust direction and length for custom SVG wires
    vxs = sorted(transition_wire.Vertexes, key=lambda v: v.Point.y)
    if abs(vxs[0].Point.y - vxs[-1].Point.y) < 1e-3:
        vxs = sorted(transition_wire.Vertexes, key=lambda v: v.Point.x)

    p_bass = vxs[0].Point
    p_treble = vxs[-1].Point
    direction = (p_treble - p_bass).normalize()
    length = (p_treble - p_bass).Length

    pnt = p_bass - direction * 5
    pnt = pnt + plate_normal * (thickness)
    pnt2 = pnt + (plate_normal * radius)
    cyl = Part.makeCylinder(radius, length + 10, pnt2, direction)
    return cyl

@task
@traced("Gordon Neck: Volute Flat")
def volute_cutter_flat(transition_wire, thickness, nut: Edge, plate_normal: Vector) -> Task[Solid]:
    """Generate solid to cut from the bottom of the headstock"""
    if transition_wire is None or transition_wire.isNull():
        return None

    # Robust direction and mid point for custom SVG wires
    vxs = sorted(transition_wire.Vertexes, key=lambda v: v.Point.y)
    if abs(vxs[0].Point.y - vxs[-1].Point.y) < 1e-3:
        vxs = sorted(transition_wire.Vertexes, key=lambda v: v.Point.x)

    p_bass = vxs[0].Point
    p_treble = vxs[-1].Point
    direction = (p_treble - p_bass).normalize()
    length = (p_treble - p_bass).Length

    transition_edge_mid = transition_wire.CenterOfMass
    dist_trans_to_nut, *_ = Vertex(transition_edge_mid).distToShape(nut)

    pnt = p_bass - direction * 5
    pnt = pnt + plate_normal * (thickness)
    plane = Part.makePlane(length + 10, length + 10, pnt, plate_normal, direction)
    cutter = plane.extrude(plate_normal * 1000)
    tr = plate_normal.cross(direction).normalize()
    if tr.x > 0:
        tr = tr.negative()
    cutter.translate(tr * (dist_trans_to_nut - 0.5))
    return cutter

@task
@traced("Gordon Neck: Headstock plate")
def extrude_headstock_plate(contour: Wire, thickness: float, transition_edge: Wire) -> Task[HeadstockPlate]:
    """
    Generate base plate solid with transition space removed

    :param Wire contour: Top contour wire, already rotated to headstock angle and positioned
    :param Wire transition_edge: Transition cut line

    :return Solid: Headstock flat part solid, normal vector
    """

    # Create a face from contour
    face = Face(contour)
    normal = face.normalAt(0,0)

    # Ensure that normal is deterministically up
    if normal.z > 0:
        normal = normal.negative()

    # Remove the left part
    try:
        # Use a slightly looser tolerance to avoid OCC topological boundary faults ("Map entry 0 is empty") 
        # when slicing a rotated face with an exact bounding edge in multiscale.
        parts = split_api.slice(face, [transition_edge], "Standard", 1e-4)
        selected = geom.query_one(parts.Faces, order_by=lambda f: -f.CenterOfMass.x)
        if selected is None or selected.isNull() or not selected.isValid():
            selected = face
    except Exception as ex:
        # If OCC fails to slice perfectly due to boundary coincidence, fallback to extruding the entire contour
        # so the headstock still safely generates rather than failing the entire neck build.
        import FreeCAD
        FreeCAD.Console.PrintWarning(f"Headstock transition slice fallback triggered: {ex}\n")
        selected = face

    # Extrude
    return HeadstockPlate(selected.extrude(normal * thickness), normal)

@traced("Gordon Neck: Top Cut")
def top_cut_flat(pos: Vector, depth: float, transition_length: float, volute_offset: float) -> Solid:
    """Create solid to cleanup the top surface"""
    a = Vector(pos)
    b = Vector(a.x + transition_length + depth, a.y, a.z - depth)
    c = Vector(a.x + 300, b.y, b.z)
    d = Vector(c.x, c.y, c.z + 2 * depth)
    e = Vector(a.x - volute_offset - 5, a.y, d.z)
    f = Vector(e.x, e.y, a.z)
    curve = Part.BSplineCurve([
        a,
        Vector(a.x + transition_length / 2, a.x, a.z),
        Vector(a.x + transition_length / 2, a.x, a.z - depth),
        b])
    l1 = Part.LineSegment(b, c)
    l2 = Part.LineSegment(c, d)
    l3 = Part.LineSegment(d, e)
    l4 = Part.LineSegment(e, f)
    l5 = Part.LineSegment(f, a)
    wire = geom.wireFromPrim([curve, l1, l2, l3, l4, l5])
    wire.translate(Vector(0, -150, 0))
    solid = Part.Face(wire).extrude(Vector(0, 300, 0))
    return solid

@traced("Gordon Neck: Top Cut")
def top_cut_angled(pos: Vector, angle_rads: float, volute_offset: float) -> Solid:
    """Create solid to cleanup the top surface"""
    a = Vector(pos)
    c = Vector(a.x + 300 * math.cos(angle_rads), a.y, a.z - 300 * math.sin(angle_rads))
    d = Vector(c.x, c.y, abs(c.z))
    e = Vector(a.x - volute_offset - 5, a.y, d.z)
    f = Vector(e.x, e.y, a.z)
    l1 = Part.LineSegment(a, c)
    l2 = Part.LineSegment(c, d)
    l3 = Part.LineSegment(d, e)
    l4 = Part.LineSegment(e, f)
    l5 = Part.LineSegment(f, a)
    wire = geom.wireFromPrim([l1, l2, l3, l4, l5])
    wire.translate(Vector(0, -150, 0))
    solid = Part.Face(wire).extrude(Vector(0, 300, 0))
    return solid

@task
def pocket_cut(angle_rads: float, pos: Vector, depth: float) -> Task[Optional[Solid]]:
    """Create solid to cut pockets from the headstock"""
    pockets = App.ActiveDocument.getObject('Marz_Headstock_Pockets')
    if pockets:
        pockets = pockets.Shape.copy()
        if angle_rads > 0:
            pockets.Placement = Placement(pos, Rotation(Vector(0, 1, 0), math.degrees(angle_rads)))
        else:
            pockets.Placement = Placement(pos + Vector(0, 0, -depth), Rotation(Vector(0, 1, 0), 0))
        return pockets
