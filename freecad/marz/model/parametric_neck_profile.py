# -*- coding: utf-8 -*-
import math
import Part
from freecad.marz.extension.fc import Vector

class ParametricNeckProfile:
    def __init__(self, coreWidthRatio=0.5, coreThicknessRatio=0.0, coreOffsetRatio=0.0,
                 radiusTreble=50.0, radiusBass=50.0):
        self.name = "Parametric"
        self.coreWidthRatio = coreWidthRatio
        self.coreThicknessRatio = coreThicknessRatio
        self.coreOffsetRatio = coreOffsetRatio
        self.radiusTreble = radiusTreble
        self.radiusBass = radiusBass

    def getHPoint(self, width, height):
        """Returns the deepest point of the profile in Depth-Across plane (Normal=Z)"""
        max_offset = (width / 2.0) - (self.coreWidthRatio * width / 2.0)
        offset = self.coreOffsetRatio * max_offset
        # Deepest point is at the core plane level (X is depth, Y is lateral)
        depth = -height + (self.coreThicknessRatio * height)
        return Vector(depth, offset, 0)

    def wire(self, width, thickness):
        """Returns a closed Wire in the Depth-Across plane (X:Depth, Y:Lateral)"""
        W = max(0.1, width)
        T = max(0.1, thickness)
        cw_ratio = max(0.0, min(1.0, self.coreWidthRatio))
        ct_ratio = max(0.0, min(1.0, self.coreThicknessRatio))
        co_ratio = max(-1.0, min(1.0, self.coreOffsetRatio))

        core_width = cw_ratio * W

        # Mapping to legacy coord convention: X is Depth, Y is Lateral
        x_fret = 0.0
        x_shoulder = -T + (ct_ratio * T)

        max_offset = (W / 2.0) - (core_width / 2.0)
        offset = co_ratio * max_offset

        y_left_core  = -core_width / 2.0 + offset
        y_right_core =  core_width / 2.0 + offset

        # Vectors in (X=Depth, Y=Lateral, Z=0)
        p_bass_bottom   = Vector(x_shoulder, y_left_core,  0)
        p_treble_bottom = Vector(x_shoulder, y_right_core, 0)
        p_bass_top      = Vector(x_fret,     -W / 2.0,     0)
        p_treble_top    = Vector(x_fret,      W / 2.0,     0)

        edges = []
        if core_width > 0.001:
            edges.append(Part.LineSegment(p_bass_bottom, p_treble_bottom).toShape())

        edges.append(self._make_arc(p_treble_bottom, p_treble_top, self.radiusTreble))
        edges.append(Part.LineSegment(p_treble_top, p_bass_top).toShape())
        edges.append(self._make_arc(p_bass_top, p_bass_bottom, self.radiusBass))

        return Part.Wire(edges)

    def _make_arc(self, p1, p2, radius):
        """Creates a circular arc between p1 and p2 with given radius, bulging outward.
           Input points are in Depth-Across plane (X:Depth, Y:Lateral).
        """
        L_vec = p2.sub(p1)
        L = L_vec.Length
        if L < 0.001:
            return Part.LineSegment(p1, p2).toShape()

        R = max(radius, L / 2.0 + 0.001)
        M = p1.add(p2).multiply(0.5)

        # Normal to the chord P1-P2 in XY plane
        d = Vector(p2.x - p1.x, p2.y - p1.y, 0).normalize()
        n = Vector(-d.y, d.x, 0)

        h = math.sqrt(max(0, R**2 - (L / 2.0)**2))

        # Bulge points candidates
        c1 = M.add(n.multiply(h))
        c2 = M.sub(n.multiply(h))

        # Bulge direction must be AWAY from the core/top (Z=0 in world, X=0 in profile plane)
        # Deepest point is negative X in this local plane.
        # Thus, the bulge point must have the most negative X value.
        v1 = M.sub(c1)
        if v1.Length < 1e-6: v1 = n.negative()
        b1 = c1.add(v1.normalize().multiply(R))

        v2 = M.sub(c2)
        if v2.Length < 1e-6: v2 = n
        b2 = c2.add(v2.normalize().multiply(R))

        if b1.x > b2.x:
            bulge_pnt = b1
        else:
            bulge_pnt = b2

        try:
            return Part.ArcOfCircle(p1, bulge_pnt, p2).toShape()
        except Exception:
            return Part.LineSegment(p1, p2).toShape()

    def __call__(self, width, height, wire=True):
        """Returns the profile section. wire=True for closed solid loft, wire=False for surface building."""
        try:
            # We always return the wire when requested, which is a closed 4-element profile
            if wire:
                return self.wire(width, height)

            # For surface building (legacy/tigl), we return an open interpolated B-spline
            W = max(0.1, width)
            T = max(0.1, height)
            cw_ratio = max(0.0, min(1.0, self.coreWidthRatio))
            ct_ratio = max(0.0, min(1.0, self.coreThicknessRatio))
            co_ratio = max(-1.0, min(1.0, self.coreOffsetRatio))

            core_width = cw_ratio * W
            x_fret = 0.0
            x_shoulder = -T + (ct_ratio * T)
            max_offset = (W / 2.0) - (core_width / 2.0)
            offset = co_ratio * max_offset
            y_left_core  = -core_width / 2.0 + offset
            y_right_core =  core_width / 2.0 + offset

            p_bass_bottom   = Vector(x_shoulder, y_left_core,  0)
            p_treble_bottom = Vector(x_shoulder, y_right_core, 0)
            p_bass_top      = Vector(x_fret,     -W / 2.0,     0)
            p_treble_top    = Vector(x_fret,      W / 2.0,     0)

            if abs(y_right_core - y_left_core) < 0.001 and ct_ratio > 0.99:
                 return self._high_stability_fallback(W, T, wire)

            bass_arc = self._make_arc(p_bass_top, p_bass_bottom, self.radiusBass)
            treble_arc = self._make_arc(p_treble_bottom, p_treble_top, self.radiusTreble)

            # Standardize resolution for stability (Exactly 21 points)
            pts_bass = bass_arc.discretize(Number=10)
            if core_width > 0.001:
                core_line = Part.LineSegment(p_bass_bottom, p_treble_bottom).toShape()
                pts_core = core_line.discretize(Number=10)
                pts_treble = treble_arc.discretize(Number=10)
                all_pts = pts_bass + pts_core[1:] + pts_treble[1:]
            else:
                pts_treble = treble_arc.discretize(Number=30)
                all_pts = pts_bass + pts_treble[1:]

            # Aggressive duplicate filter
            pts = [all_pts[0]]
            for p in all_pts[1:]:
                if (p - pts[-1]).Length > 1e-4:
                    pts.append(p)

            bsp = Part.BSplineCurve()
            bsp.interpolate(pts)
            resampled_pts = bsp.toShape().discretize(Number=30) # Exactly 21 points

            final_curve = Part.BSplineCurve()
            final_curve.interpolate(resampled_pts)
            return final_curve.toShape()
        except Exception:
            return self._high_stability_fallback(width, height, wire)

    def _high_stability_fallback(self, width, height, wire):
        """Standard U-shape fallback mapped to (X:Depth, Y:Lateral)"""
        # (X=Depth, Y=Lateral, Z=0)
        leftTop = Vector(0, -width / 2.0, 0)
        rightTop = Vector(0, width / 2.0, 0)
        cent = Vector(-height, 0, 0)
        points = [leftTop, cent, rightTop]
        bsp = Part.BSplineCurve()
        bsp.interpolate(points)
        curve = bsp.toShape()
        if wire:
            return Part.Wire([curve, Part.LineSegment(rightTop, leftTop).toShape()])
        return curve
