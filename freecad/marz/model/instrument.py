# freecad/marz/model/instrument.py
# -*- coding: utf-8 -*-
import functools
import math
from enum import Enum
from freecad.marz.model.transitions import TransitionFunction

def inch_to_mm(i):
    return i * 25.4

def mm_to_inch(millimeters):
    return millimeters / 25.4

def deg_to_rad(degrees):
    return math.pi * degrees / 180.0

def rad_to_deg(radians):
    return 180.0 * radians / math.pi

class FretboardCut(Enum):
    PARALLEL = 'Parallel to last fret'
    PERPENDICULAR = 'Perpendicular to Mid Line'
    CUSTOM = 'Custom'

class NutPosition(Enum):
    PARALLEL = 'Parallel to fret zero'
    PERPENDICULAR = 'Perpendicular to Mid Line'

class NeckJoint(Enum):
    BOLTED = 'Bolt On'
    SETIN = 'Set In'
    THROUGH = 'Through All'
    EXTRA_CHUNK = 'Extra Chunk'

class NutSpacing(Enum):
    EQ_CENTER = 'Equal Center'
    EQ_GAP = 'Equal Gap'

class Feature:
    def __init__(self, instrument):
        self._instrument = instrument
    @property
    def instrument(self):
        return self._instrument

FRET_RATIO = 1.05946309436
def fret(n: int, scale: float, edo: int = 12) -> float:
    return scale - (scale / (2 ** (n / edo)))

class Scale(Feature):
    def __init__(self, instrument, bass=700.0, treble=647.7):
        super().__init__(instrument)
        self._bass = 0
        self._treble = 0
        self.bass = bass
        self.treble = treble
    @property
    def bass(self):
        return self._bass
    @bass.setter
    def bass(self, v):
        self._bass = v
        if self._treble > v:
            self._treble = v
    @property
    def treble(self):
        return self._treble
    @treble.setter
    def treble(self, v):
        self._treble = v
        if self._bass < v:
            self._bass = v
    @property
    def avg(self):
        return (self._bass + self._treble) / 2.0
    @property
    def max(self):
        return self._bass
    @property
    def min(self):
        return self._treble
    @property
    def isMultiScale(self):
        return self._treble != self._bass

class Nut(Feature):
    def __init__(self, instrument, thickness=5.0, spacing=NutSpacing.EQ_GAP,
                 position=NutPosition.PARALLEL, offset=3.0, depth=0.75, stringDistanceProj=43.5):
        super().__init__(instrument)
        self.thickness = thickness
        self.spacing = spacing
        self.position = position
        self.offset = offset
        self.depth = depth
        self.stringDistanceProj = stringDistanceProj

class Fretboard(Feature):
    def __init__(self, instrument,
                 thickness: float = 7.0,
                 startRadius: float = inch_to_mm(10),
                 endRadius: float = inch_to_mm(14),
                 startMargin: float = 5.0,
                 endMargin: float = 5.0,
                 sideMargin: float = 3.0,
                 cut: FretboardCut = FretboardCut.PARALLEL,
                 frets: int = 24,
                 fretNipping: float = 2.0,
                 cutBassDistance: float = 400.0,
                 cutTrebleDistance: float = 400.0,
                 perpendicularFret: int = 7,
                 inlayDepth: float = 1.0,
                 filletRadius: float = 1.0,
                 edo: int = 12,
                 fretboard_length: float = None,
                 isZeroFret: bool = False):
        super().__init__(instrument)
        self.thickness = thickness
        self.startRadius = startRadius
        self.endRadius = endRadius
        self.startMargin = startMargin
        self.endMargin = endMargin
        self.sideMargin = sideMargin
        self.cut = cut
        self.frets = frets
        self.fretNipping = fretNipping
        self.cutBassDistance = cutBassDistance
        self.cutTrebleDistance = cutTrebleDistance
        self.perpendicularFret = perpendicularFret
        self.inlayDepth = inlayDepth
        self.filletRadius = filletRadius

        
        # physical fretboard length measured from the nut to the board end (mm).
        # This value is set by the Instrument properties (see instrument_properties.py).
        # When None or 0.0, existing behaviour is preserved (board end calculated from last fret).
        self._length = fretboard_length
        # canonical lowercase property used by code
        self._edo = int(edo)
        self._isZeroFret = bool(isZeroFret)

    @property
    def length(self):
        return self._length

    @length.setter
    def length(self, v):
        self._length = float(v) if v is not None else None

    @property
    def edo(self):
        return self._edo

    @edo.setter
    def edo(self, v):
        self._edo = int(v)

    @property
    def EDO(self):
        return self._edo

    @EDO.setter
    def EDO(self, v):
        self._edo = int(v)

    @property
    def isZeroFret(self):
        return self._isZeroFret

    @isZeroFret.setter
    def isZeroFret(self, v):
        self._isZeroFret = bool(v)


def get_is_zero_fret(obj):
    """Safely get isZeroFret from any object (Instrument, Fretboard, or FreeCAD DocumentObject)"""
    # Try direct attribute
    for attr in ['isZeroFret', '_isZeroFret', 'Fretboard_IsZeroFret']:
        if hasattr(obj, attr):
            val = getattr(obj, attr)
            if val is not None:
                return bool(val)

    # Try dictionary-like access for FreeCAD objects
    try:
        return bool(obj.Fretboard_IsZeroFret)
    except Exception:
        pass

    return False


class Neck(Feature):
    def __init__(self, instrument, joint=NeckJoint.THROUGH, startThickness=15,
                 endThickness=17, jointFret=16, topOffset=0, angle=3, tenonThickness=10,
                 tenonLength=10, tenonOffset=2, profile="Custom"):
        super().__init__(instrument)
        self.joint = joint
        self.startThickness = startThickness
        self.endThickness = endThickness
        self.jointFret = jointFret
        self.topOffset = topOffset
        self.angle = angle
        self.tenonThickness = tenonThickness
        self.tenonLength = tenonLength
        self.tenonOffset = tenonOffset
        self.heelFillet = heelFillet
        self.heelOffset = heelOffset
        self.extraChunkLength = extraChunkLength
        self.extraChunkVerticalOffset = extraChunkVerticalOffset
        self.extraChunkDepthOffset = extraChunkDepthOffset
        self.extraChunkThickness = extraChunkThickness
        self.extraChunkVerticalThickness = extraChunkVerticalThickness

class StringSet(Feature):
    def __init__(self, instrument, name=None, strings=None):
        super().__init__(instrument)
        self.name = name or 'Guitar Std 6 strings 10,13,17,26,36,46'
        self.strings = strings or [
            inch_to_mm(0.010),
            inch_to_mm(0.013),
            inch_to_mm(0.017),
            inch_to_mm(0.026),
            inch_to_mm(0.036),
            inch_to_mm(0.046)
        ]
    @property
    def gauges(self):
        return [str(mm_to_inch(g)) for g in self.strings]
    @gauges.setter
    def gauges(self, gauges):
        self.strings = [inch_to_mm(float(g)) for g in gauges]
    @property
    def count(self):
        return len(self.strings)
    @property
    def min(self):
        return min(self.strings)
    @property
    def max(self):
        return max(self.strings)
    @property
    def first(self):
        return self.strings[0]
    @property
    def last(self):
        return self.strings[-1]
    @property
    def totalWidth(self):
        return sum(self.strings)
    def string(self, n):
        return self.strings[n]

class FretWire(Feature):
    def __init__(self, instrument, name=None, tangDepth=None, tangWidth=None, crownHeight=None, crownWidth=None):
        super().__init__(instrument)
        self.name = name or "Medium/Medium (Stewmac_TM)"
        self.tangDepth = tangDepth or inch_to_mm(0.055)
        self.tangWidth = tangWidth or inch_to_mm(0.020)
        self.crownHeight = crownHeight or inch_to_mm(0.039)
        self.crownWidth = crownWidth or inch_to_mm(0.084)

class HeadStock(Feature):
    def __init__(self, instrument, width=80.0, length=220.0, thickness=15.0, angle=deg_to_rad(9),
                 depth=7, voluteRadius=50.0, transitionParamHorizontal=0.5, voluteOffset=10,
                 topTransitionLength=20):
        super().__init__(instrument)
        self.width = width
        self.length = length
        self.thickness = thickness
        self.angle = angle
        self.depth = depth
        self.voluteRadius = voluteRadius
        self.transitionParamHorizontal = transitionParamHorizontal
        self.voluteOffset = voluteOffset
        self.topTransitionLength = topTransitionLength

class Bridge(Feature):
    def __init__(self, instrument, stringDistanceProj=63, height=16.363,
                 bassCompensation=0, trebleCompensation=0):
        super().__init__(instrument)
        self.stringDistanceProj = stringDistanceProj
        self.height = height
        self.bassCompensation = bassCompensation
        self.trebleCompensation = trebleCompensation

class Body(Feature):
    def __init__(self, instrument, topThickness=5, backThickness=40, length=500, width=350,
                 neckPocketDepth=15.875, neckPocketLength=50, neckPocketCarve=True):
        super().__init__(instrument)
        self.topThickness = topThickness
        self.backThickness = backThickness
        self.length = length
        self.width = width
        self.neckPocketDepth = neckPocketDepth
        self.neckPocketLength = neckPocketLength
        self.neckPocketCarve = neckPocketCarve

class TrussRod(Feature):
    def __init__(self, instrument, length=430, width=6, depth=9, start=0,
                 headLength=20, headWidth=8, headDepth=11,
                 tailLength=0, tailWidth=0, tailDepth=0):
        super().__init__(instrument)
        self.length = length
        self.width = width
        self.depth = depth
        self.start = start
        self.headLength = headLength
        self.headWidth = headWidth
        self.headDepth = headDepth
        self.tailLength = tailLength
        self.tailWidth = tailWidth
        self.tailDepth = tailDepth
    @property
    def end(self):
        return self.start + self.length

class InternalProps(Feature):
    def __init__(self, instrument):
        super().__init__(instrument)
        self.bodyImport = 0
        self.headstockImport = 0
        self.inlayImport = 0

class AutoUpdate(Feature):
    def __init__(self, instrument):
        super().__init__(instrument)
        self.fretboard = True
        self.neck = True
        self.body = True

class ModelException(Exception):
    def __init__(self, msg):
        super().__init__()
        self.message = msg

class Instrument:
    def __init__(self, edo=12):
        self.scale = Scale(self)
        self.nut = Nut(self)
        self.neck = Neck(self)
        self.fretboard = Fretboard(self, edo=edo)
        self.stringSet = StringSet(self)
        self.fretWire = FretWire(self)
        self.headStock = HeadStock(self)
        self.bridge = Bridge(self)
        self.body = Body(self)
        self.trussRod = TrussRod(self)
        self.internal = InternalProps(self)
        self.autoUpdate = AutoUpdate(self)

    def getSerializable(self):
        s = {}
        for name, value in self.__dict__.items():
            s[name] = value.__dict__
        return s

    def loadFromSerializable(self, s):
        for name, value in s.items():
            if hasattr(self, name):
                getattr(self, name).__dict__.update(value)
        return self
