# freecad/marz/model/fretboard_data.py
# -*- coding: utf-8 -*-
from freecad.marz.model.linexy import lineIntersection, linexy
from freecad.marz.model.vxy import vxy
import Part

class FretboardBox(object):
    bass: linexy
    treble: linexy
    nut: linexy
    bridge: linexy

    # include both EDO and edo as slots for compatibility
    __slots__ = ['bass', 'treble', 'nut', 'bridge', '_polygon', '_midLine', '_midLineExtended', '_ihash', 'EDO', 'edo']

    def __init__(self, bass, treble, nut, bridge):
        super().__setattr__('bass', bass)
        super().__setattr__('treble', treble)
        super().__setattr__('nut', nut)
        super().__setattr__('bridge', bridge)
        super().__setattr__('_ihash', hash((bass, treble, nut, bridge)))

    def __hash__(self):
        return self._ihash

    def __eq__(self, other):
        return (
            self.bass == other.bass
            and self.treble == other.treble
            and self.nut == other.nut
            and self.bridge == other.bridge
        )

    def __setattr__(self, name, value):
        raise AttributeError(f"{self.__class__.__name__}.{name} is not writable.")

    @property
    def polygon(self):
        return [self.bass.start, self.bridge.start, self.treble.start, self.nut.start, self.bass.start]

    @property
    def midLine(self):
        return linexy(self.nut.mid(), self.bridge.mid())

    @property
    def midLineExtended(self):
        line = self.midLine
        maxxp = max(self.treble.end.x, self.bass.start.x)
        minxp = min(self.treble.start.x, self.bass.end.x)
        return linexy(
            lineIntersection(linexy(vxy(maxxp, 0), vxy(maxxp, 1)), line).point,
            lineIntersection(linexy(vxy(minxp, 0), vxy(minxp, 1)), line).point
        )

    def midLineExtendedWith(self, start=0, end=0):
        line = self.midLineExtended
        return line.lerpLineTo(-start).flipDirection().lerpLineTo(line.length + start + end)

    def translate(self, v):
        tr = [l.clone().translate(v) for l in (self.bass, self.treble, self.nut, self.bridge)]
        return FretboardBox(*tr)
    
    def wire(self):
        return Part.Wire([self.nut.edge(), self.bass.edge(), self.bridge.edge(), self.treble.edge()])


class FretboardData:
    frame: FretboardBox
    virtStrFrame: FretboardBox
    scaleFrame: FretboardBox
    nutFrame: FretboardBox
    neckFrame: FretboardBox

    __slots__ = ['frame', 'virtStrFrame', 'scaleFrame', 'nutFrame', 'frets', 'bridgePos', 
                 'neckFrame', '_ihash', 'filletRadius', 'heelOffset', 'EDO', 'edo']

    def __init__(self, frame, virtStrFrame, scaleFrame, nutFrame, 
                 frets, bridgePos, neckFrame, filletRadius, heelOffset, edo=12):
        super().__setattr__('frame', frame)
        super().__setattr__('virtStrFrame', virtStrFrame)
        super().__setattr__('scaleFrame', scaleFrame)
        super().__setattr__('nutFrame', nutFrame)
        super().__setattr__('neckFrame', neckFrame)
        super().__setattr__('frets', frets)
        super().__setattr__('bridgePos', bridgePos)
        super().__setattr__('filletRadius', filletRadius)
        super().__setattr__('heelOffset', heelOffset)
        # store both names for compatibility
        super().__setattr__('EDO', int(edo))
        super().__setattr__('edo', int(edo))

        ihash = hash((frame, virtStrFrame, scaleFrame, nutFrame, neckFrame, (*frets,), bridgePos, filletRadius, heelOffset, int(edo)))
        super().__setattr__('_ihash', ihash)

    def __setattr__(self, name, value):
        raise AttributeError(f"{self.__class__.__name__}.{name} is not writable.")

    def __hash__(self):
        return self._ihash

    def __eq__(self, other):
        return (
            self.frame == other.frame
            and self.virtStrFrame == other.virtStrFrame
            and self.scaleFrame == other.scaleFrame
            and self.nutFrame == other.nutFrame
            and self.neckFrame == other.neckFrame
            and self.frets == other.frets
            and self.bridgePos == other.bridgePos
            and self.filletRadius == other.filletRadius
            and self.heelOffset == other.heelOffset
            and getattr(self, 'edo', getattr(self, 'EDO', None)) == getattr(other, 'edo', getattr(other, 'EDO', None))
        )

    def translate(self, v):
        (frame, virtStrFrame, scaleFrame, nutFrame, bridgePos, neckFrame) = \
            [f.translate(v) for f in (self.frame, self.virtStrFrame, self.scaleFrame, self.nutFrame, self.bridgePos, self.neckFrame)]
        frets = [f.translate(v) for f in self.frets]
        return FretboardData(frame, virtStrFrame, scaleFrame, nutFrame, 
                             frets, bridgePos, neckFrame, 
                             self.filletRadius, self.heelOffset, getattr(self, 'edo', getattr(self, 'EDO', 12)))

    def widthAt(self, dist):
        fbSlope = (self.neckFrame.bridge.length - self.neckFrame.nut.length) / self.neckFrame.midLine.length
        return self.neckFrame.nut.length + fbSlope * dist
