# -*- coding: utf-8 -*-
import math
import Part
from freecad.marz.extension.fc import Vector, App
from freecad.marz.feature import document

class CustomNeckProfile:
    def __init__(self, doc=None):
        self.doc = doc or App.activeDocument()
        self.shape = self._get_svg_shape()

    def _get_svg_shape(self):
        try:
            if document.NeckProfileImports.contour:
                feature = self.doc.getObject(document.NeckProfileImports.contour.name)
                if feature and hasattr(feature, "Shape") and not feature.Shape.isNull():
                    return feature.Shape
        except Exception:
            pass
        return None

    def getHPoint(self, width, height):
        """Returns the deepest point of the profile in Depth-Across plane (Normal=Z)"""
        # Deepest point is at the core plane level (X is depth, Y is lateral)
        depth = -height
        return Vector(depth, 0, 0)

    def wire(self, width, height):
        """Returns a closed Wire in the Depth-Across plane (X:Depth, Y:Lateral)"""
        if self.shape:
            return self._scale_shape(width, height)
        else:
            return self._high_stability_fallback(width, height, True)

    def _scale_shape(self, width, height):
        """Scales the SVG shape to the desired width and height"""
        bbox = self.shape.BoundBox
        svg_width = bbox.XMax - bbox.XMin
        svg_height = bbox.YMax - bbox.YMin

        if svg_width < 1e-5 or svg_height < 1e-5:
            return self._high_stability_fallback(width, height, True)

        scale_x = width / svg_width
        scale_y = height / svg_height

        # We want to map SVG X to Lateral (Y) and SVG Y to Depth (X)
        # Also need to center Lateral (Y) and set top of Depth (X) to 0

        matrix = App.Matrix()

        # Scale
        matrix.scale(scale_x, scale_y, 1.0)

        scaled_shape = self.shape.copy()
        scaled_shape.transformShape(matrix)

        scaled_bbox = scaled_shape.BoundBox

        # Center X and set Y max to 0
        trans_x = -(scaled_bbox.XMax + scaled_bbox.XMin) / 2.0
        trans_y = -scaled_bbox.YMax

        scaled_shape.translate(Vector(trans_x, trans_y, 0))

        # Map to X:Depth, Y:Lateral
        # Current: X is Lateral, Y is Depth
        # Rotate 90 degrees around Z axis? No, just map coordinates.
        # Actually, let's discretize and build a new wire to ensure closed and correctly oriented.

        try:
            edges = scaled_shape.Edges
            if not edges:
                return self._high_stability_fallback(width, height, True)

            all_pts = []
            for edge in edges:
                pts = edge.discretize(Number=20)
                # Swap X and Y, and invert X (depth is negative X)
                # Original: X=Lateral, Y=Depth (negative)
                # New: X=Depth (Y), Y=Lateral (X)
                mapped_pts = [Vector(p.y, p.x, 0) for p in pts]
                if not all_pts:
                    all_pts.extend(mapped_pts)
                else:
                    all_pts.extend(mapped_pts[1:])

            # Close the wire with a straight line if needed
            if (all_pts[0] - all_pts[-1]).Length > 1e-5:
                all_pts.append(all_pts[0])

            # Filter duplicates
            pts = [all_pts[0]]
            for p in all_pts[1:]:
                if (p - pts[-1]).Length > 1e-4:
                    pts.append(p)

            bsp = Part.BSplineCurve()
            bsp.interpolate(pts)
            curve = bsp.toShape()

            # The top straight edge might be missing or distorted, so let's enforce a top line
            # Wait, the user draws a profile, we should just use their curve.
            # But we need a closed wire for lofting.
            # If the SVG is already a closed loop (e.g. D shape), great.
            # If it's just the bottom curve (U shape), we need to close it with a top line.

            # For simplicity, let's assume it's just the curve and we close it.
            # Find the extremes in Lateral (Y)
            min_y_pt = min(pts, key=lambda p: p.y)
            max_y_pt = max(pts, key=lambda p: p.y)

            top_line = Part.LineSegment(max_y_pt, min_y_pt).toShape()

            try:
                # Try to make a wire from the curve and the top line
                return Part.Wire([curve, top_line])
            except:
                # If that fails, maybe the curve is already closed
                return Part.Wire([curve])

        except Exception:
            return self._high_stability_fallback(width, height, True)

    def __call__(self, width, height, wire=True):
        """Returns the profile section. wire=True for closed solid loft, wire=False for surface building."""
        try:
            if wire:
                return self.wire(width, height)
            else:
                # For surface building (legacy/tigl), we return an open interpolated B-spline
                w = self.wire(width, height)
                # Extract the curve part (first edge)
                if w.Edges:
                    return w.Edges[0]
                return self._high_stability_fallback(width, height, False)
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
