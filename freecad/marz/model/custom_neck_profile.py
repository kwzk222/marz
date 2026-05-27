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
        """Scales the SVG shape to exactly match the desired width and height"""
        try:
            bbox = self.shape.BoundBox
            svg_width = bbox.XMax - bbox.XMin
            svg_height = bbox.YMax - bbox.YMin

            if svg_width < 1e-5 or svg_height < 1e-5:
                return self._high_stability_fallback(width, height, True)

            # Map X=Lateral, Y=Depth
            scale_x = width / svg_width
            scale_y = height / svg_height


            # Scale
            matrix = App.Matrix()
            matrix.scale(scale_x, scale_y, 1.0)

            scaled_shape = self.shape.copy()
            scaled_shape.transformShape(matrix)

            scaled_bbox = scaled_shape.BoundBox

            # Center X (Lateral) exactly around 0.
            trans_x = -(scaled_bbox.XMax + scaled_bbox.XMin) / 2.0

            # Align Y (Depth) max to 0.
            # FreeCAD imports SVG with inverted Y, meaning the top of the U-shape is at Y=0,
            # and the bottom of the U-shape drops into negative Y.
            # Translating by -YMax ensures the highest point (fretboard) is exactly at Y=0.
            trans_y = -scaled_bbox.YMax

            scaled_shape.translate(Vector(trans_x, trans_y, 0))

            edges = scaled_shape.Edges
            if not edges:
                return self._high_stability_fallback(width, height, True)

            all_pts = []
            for edge in edges:
                pts = edge.discretize(Number=20)
                # Map coordinates to FreeCAD Profile format:
                # X axis is Depth. Y axis is Lateral.
                # Since Y is already mapped from 0 down to -height (due to FreeCAD SVG Y inversion),
                # we just set X = p.y and Y = p.x
                mapped_pts = [Vector(p.y, p.x, 0) for p in pts]
                if not all_pts:
                    all_pts.extend(mapped_pts)
                else:
                    all_pts.extend(mapped_pts[1:])


            # Ensure extremes are EXACTLY bound to width and height to avoid floating point errors
            for i, p in enumerate(all_pts):
                new_x = p.x
                new_y = p.y
                # Lateral bounds (Y axis)
                if new_y < -width / 2.0:
                    new_y = -width / 2.0
                elif new_y > width / 2.0:
                    new_y = width / 2.0
                # Depth bounds (X axis)
                if new_x < -height:
                    new_x = -height
                elif new_x > 0:
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


            p_start = curve.valueAt(curve.FirstParameter)
            p_end = curve.valueAt(curve.LastParameter)

            # Close the wire exactly at X=0 (Depth=0)
            # Create vertical line segments to X=0 if the curve doesn't reach exactly X=0
            segments = [curve]

            p_start_top = Vector(0, p_start.y, 0)
            p_end_top = Vector(0, p_end.y, 0)

            # Since p_end is the end of the curve, we go from p_end up to p_end_top
            if (p_end - p_end_top).Length > 1e-4:
                segments.append(Part.LineSegment(p_end, p_end_top).toShape())

            # Then across the top
            if (p_end_top - p_start_top).Length > 1e-4:
                segments.append(Part.LineSegment(p_end_top, p_start_top).toShape())

            # Then back down to the start of the curve
            if (p_start_top - p_start).Length > 1e-4:
                segments.append(Part.LineSegment(p_start_top, p_start).toShape())

            try:
                return Part.Wire(segments)
            except:
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
