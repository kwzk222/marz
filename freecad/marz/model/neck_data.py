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
# |  Marz Workbench is distributed in the hope that it will be useful,                |
# |  but WITHOUT ANY WARRANTY; without even the implied warranty of           |
# |  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the            |
# |  GNU General Public License for more details.                             |
# |                                                                           |
# |  You should have received a copy of the GNU General Public License        |
# |  along with Marz Workbench.  If not, see <https://www.gnu.org/licenses/>. |
# +---------------------------------------------------------------------------+


from freecad.marz.model.linexy import lineIntersection, linexy


class NeckData(object):
    """
    Neck reference constructions
    """

    # ! Using __slots__ to make this class Immutable.
    # ! Immutability is required here because instances of
    # ! this object will be cached as a hash calculated on creation,
    # ! so two instances created with same data will hit the same
    # ! cache entry.
    __slots__ = ['fbd', 'thicknessSlope', 'startThickness', '_ihash', 'profileName',
                 'coreWidthRatio', 'coreThicknessRatio', 'coreOffsetRatio',
                 'radiusTreble', 'radiusBass',
                 'transitionFunction', 'transitionLength', 'transitionTension']

    def __init__(self, inst, fbd):
        # Calculations
        startThickness = inst.neck.startThickness
        thicknessSlope = (inst.neck.endThickness - startThickness) / (inst.scale.avg / 2)

        # Neck Profile
        profileName = inst.neck.profile
        coreWidthRatio = inst.neck.coreWidthRatio
        coreThicknessRatio = inst.neck.coreThicknessRatio
        coreOffsetRatio = inst.neck.coreOffsetRatio
        radiusTreble = inst.neck.radiusTreble
        radiusBass = inst.neck.radiusBass

        # Set immutable values
        super().__setattr__('profileName', profileName)
        super().__setattr__('thicknessSlope', thicknessSlope)
        super().__setattr__('startThickness', startThickness)
        super().__setattr__('coreWidthRatio', coreWidthRatio)
        super().__setattr__('coreThicknessRatio', coreThicknessRatio)
        super().__setattr__('coreOffsetRatio', coreOffsetRatio)
        super().__setattr__('radiusTreble', radiusTreble)
        super().__setattr__('radiusBass', radiusBass)
        super().__setattr__('transitionLength', inst.neck.transitionLength)
        super().__setattr__('transitionTension', inst.neck.transitionTension)
        super().__setattr__('transitionFunction', inst.neck.transitionFunction)
        super().__setattr__('fbd', fbd)

        # Calculate immutable hash
        ihash = hash((thicknessSlope, startThickness, fbd, profileName,
                      coreWidthRatio, coreThicknessRatio, coreOffsetRatio,
                      radiusTreble, radiusBass,
                      inst.neck.transitionLength, inst.neck.transitionTension, inst.neck.transitionFunction))
        super().__setattr__('_ihash', ihash)

    def __setattr__(self, name, value):
        raise AttributeError(f"{self.__class__.__name__}.{name} is not writable.")

    def __hash__(self):
        return self._ihash

    def __eq__(self, other):
        return (self.thicknessSlope == other.thicknessSlope
                and self.startThickness == other.startThickness
                and self.coreWidthRatio == other.coreWidthRatio
                and self.coreThicknessRatio == other.coreThicknessRatio
                and self.coreOffsetRatio == other.coreOffsetRatio
                and self.radiusTreble == other.radiusTreble
                and self.radiusBass == other.radiusBass
                and self.transitionLength == other.transitionLength
                and self.transitionTension == other.transitionTension
                and self.transitionFunction == other.transitionFunction
                and self.fbd == other.fbd)

    def widthAt(self, dist):
        """Returns neck width at `dist`"""
        return self.fbd.widthAt(dist)

    def thicknessAt(self, dist):
        """Returns neck thickess at `dist`"""
        try:
            line = self.fbd.neckFrame.midLine
            if hasattr(self.fbd, "frets") and len(self.fbd.frets) > 0:
                x = linexy(lineIntersection(line, self.fbd.frets[0]).point, line.start).length
            else:
                x = 0.0
            b = self.thicknessSlope * -x + self.startThickness
            return self.thicknessSlope * dist + b
        except Exception:
            return self.startThickness

    def pointAtFret(self, fret):
        """Returns a point on intersection between `fret` and neck's midLine"""
        return lineIntersection(self.fbd.frets[fret], self.fbd.neckFrame.midLine).point

    def lineToFret(self, fret):
        """Returns a line between neck's midLine start and `fret`.
        Fret index is clamped to the valid range (0 .. n_frets-1) to avoid
        indexing errors when an explicit fretboard length reduces the number
        of generated frets.
        """
        # Protect against bad values and clamp to available frets
        if not hasattr(self.fbd, "frets") or not self.fbd.frets:
            # no frets available -> fallback to nut point as degenerate line
            return linexy(self.fbd.neckFrame.nut.mid(), self.fbd.neckFrame.nut.mid())
        max_index = len(self.fbd.frets) - 1
        safe_idx = int(max(0, min(fret, max_index)))
        return linexy(self.fbd.neckFrame.nut.mid(), self.pointAtFret(safe_idx))
