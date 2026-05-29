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

            # Map the SVG directly to points and correct the orientation FIRST.
            # SVG space is X=Lateral, Y=Depth. We map to FreeCAD neck space: X=Depth, Y=Lateral
            all_pts = []
            for edge in edges:
                pts = edge.discretize(Number=20)
                mapped_pts = [Vector(p.y, p.x, 0) for p in pts]
                if not all_pts:
                    all_pts.extend(mapped_pts)
                else:
                    all_pts.extend(mapped_pts[1:])

            if len(all_pts) < 2:
                return self._high_stability_fallback(width, height, wire)

            # 1. Normalize depth (X axis) so that the top endpoints align to 0.
            # Note: The structural connection to the fretboard is represented by the first and last points.
            start_x = all_pts[0].x
            end_x = all_pts[-1].x

            # Translate shape so the structural endpoints lie on the X=0 plane
            top_offset = (start_x + end_x) / 2.0
            for i in range(len(all_pts)):
                all_pts[i] = Vector(all_pts[i].x - top_offset, all_pts[i].y, 0)

            # 2. Check orientation. In correct orientation, the middle (back of neck) is deeper (negative X)
            # than the endpoints (which are now roughly 0).
            mid_idx = len(all_pts) // 2
            if all_pts[mid_idx].x > 0:
                # Upside down: Invert X
                for i in range(len(all_pts)):
                    all_pts[i] = Vector(-all_pts[i].x, all_pts[i].y, 0)

            # 3. Determine current dimensions based on structural points and true extremes.
            start_y = all_pts[0].y
            end_y = all_pts[-1].y
            current_structural_width = abs(start_y - end_y)

            current_min_x = min(p.x for p in all_pts)
            current_max_x = max(p.x for p in all_pts)
            current_depth = current_max_x - current_min_x

            if current_structural_width < 1e-5 or current_depth < 1e-5:
                return self._high_stability_fallback(width, height, wire)

            # 4. Scale and Translate in FreeCAD space.
            # Scale factors
            scale_y = width / current_structural_width
            scale_x = height / current_depth

            # Find the new centered Y offset by measuring the scaled endpoints
            scaled_start_y = all_pts[0].y * scale_y
            scaled_end_y = all_pts[-1].y * scale_y
            trans_y = -(scaled_start_y + scaled_end_y) / 2.0

            # Find the new X offset by aligning the deepest point to -height
            scaled_min_x = current_min_x * scale_x
            trans_x = -height - scaled_min_x

            # Apply final mapping
            for i, p in enumerate(all_pts):
                new_x = (p.x * scale_x) + trans_x
                new_y = (p.y * scale_y) + trans_y

                # We clamp only the top edge exactly to 0 if mathematical floating point
                # imprecision pushed it above the fretboard bottom.
                # We leave lateral bounds entirely alone to preserve the exact geometric Bezier topology
                # (which prevents 'Map entry 0 is empty' faults).
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
