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




    def _scale_shape(self, width, height, wire=True):
        """Scales the SVG shape to exactly match the desired width and height"""
        try:
            edges = self.shape.Edges
            if not edges:
                return self._high_stability_fallback(width, height, wire)

            # Extract points to find true bounds instead of using shape.BoundBox
            # which might include invisible control points
            raw_pts = []
            for edge in edges:
                pts = edge.discretize(Number=50) # use 50 points per edge for decent accuracy
                raw_pts.extend(pts)

            if not raw_pts:
                return self._high_stability_fallback(width, height, wire)

            min_x = min(p.x for p in raw_pts)
            max_x = max(p.x for p in raw_pts)
            min_y = min(p.y for p in raw_pts)
            max_y = max(p.y for p in raw_pts)

            svg_width = max_x - min_x
            svg_height = max_y - min_y

            if svg_width < 1e-5 or svg_height < 1e-5:
                return self._high_stability_fallback(width, height, wire)

            # Map X=Lateral, Y=Depth
            scale_x = width / svg_width
            scale_y = height / svg_height

            matrix = App.Matrix()
            matrix.scale(scale_x, scale_y, 1.0)

            scaled_shape = self.shape.copy()
            scaled_shape.transformShape(matrix)

            # Find bounds of scaled shape to translate correctly
            scaled_raw_pts = []
            for edge in scaled_shape.Edges:
                pts = edge.discretize(Number=50)
                scaled_raw_pts.extend(pts)

            if not scaled_raw_pts:
                return self._high_stability_fallback(width, height, wire)

            s_min_x = min(p.x for p in scaled_raw_pts)
            s_max_x = max(p.x for p in scaled_raw_pts)
            s_max_y = max(p.y for p in scaled_raw_pts)

            # Center X (Lateral) exactly around 0 using geometric bounds
            trans_x = -(s_max_x + s_min_x) / 2.0

            # Align Y (Depth) max to 0 using geometric bounds
            trans_y = -s_max_y

            scaled_shape.translate(Vector(trans_x, trans_y, 0))

            edges = scaled_shape.Edges
            all_pts = []
            for edge in edges:
                pts = edge.discretize(Number=20)
                mapped_pts = [Vector(p.y, p.x, 0) for p in pts]
                if not all_pts:
                    all_pts.extend(mapped_pts)
                else:
                    all_pts.extend(mapped_pts[1:])

            # Dynamically check orientation and invert Depth (X axis) if it's upside down.
            # A correct neck profile has its endpoints (fretboard edge) closer to 0
            # and its midpoint (back of neck) closer to -height.
            # If the ends are deeper than the middle, we invert X.
            if len(all_pts) > 2:
                end_depth = abs(all_pts[0].x) + abs(all_pts[-1].x)
                mid_depth = abs(all_pts[len(all_pts)//2].x) * 2

                # If the ends are deeper (more negative) than the middle, the shape is upside down.
                if end_depth > mid_depth:
                    # Invert X axis for all points and shift so the peak remains bounded.
                    # Since X ranges from -height to 0, inverting makes it 0 to height.
                    # We subtract height to shift it back to -height to 0.
                    for i in range(len(all_pts)):
                        all_pts[i] = Vector(-all_pts[i].x - height, all_pts[i].y, 0)

            for i, p in enumerate(all_pts):
                new_x = p.x
                new_y = p.y
                # Depth bounds (X axis) - we only clamp the top to 0 just in case
                # mathematical precision floated above the fretboard bottom.
                # We leave lateral bounds alone to preserve the exact geometric Bezier curve topology.
                if new_x > 0:
                    new_x = 0.0
                all_pts[i] = Vector(new_x, new_y, 0)

            # Filter duplicates
            pts = [all_pts[0]]
            for p in all_pts[1:]:
                if (p - pts[-1]).Length > 1e-4:
                    pts.append(p)

            bsp = Part.BSplineCurve()
            bsp.interpolate(pts)
            curve = bsp.toShape()

            if not wire:
                return curve

            p_start = curve.valueAt(curve.FirstParameter)
            p_end = curve.valueAt(curve.LastParameter)

            segments = [curve]

            p_start_top = Vector(0, p_start.y, 0)
            p_end_top = Vector(0, p_end.y, 0)

            if (p_end - p_end_top).Length > 1e-4:
                segments.append(Part.LineSegment(p_end, p_end_top).toShape())

            if (p_end_top - p_start_top).Length > 1e-4:
                segments.append(Part.LineSegment(p_end_top, p_start_top).toShape())

            if (p_start_top - p_start).Length > 1e-4:
                segments.append(Part.LineSegment(p_start_top, p_start).toShape())

            try:
                return Part.Wire(segments)
            except:
                return Part.Wire([curve])

        except Exception:
            return self._high_stability_fallback(width, height, wire)


    def __call__(self, width, height, wire=True):
        """Returns the profile section. wire=True for closed solid loft, wire=False for surface building."""
        try:
            return self._scale_shape(width, height, wire=wire)
        except Exception:
            return self._high_stability_fallback(width, height, wire)

    def wire(self, width, height):
        return self._scale_shape(width, height, wire=True)



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
