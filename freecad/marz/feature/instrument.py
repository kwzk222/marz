# freecad/marz/feature/instrument.py
# -*- coding: utf-8 -*-
from freecad.marz.extension.fc import App, Gui
from freecad.marz.extension.fcdoc import transaction
from freecad.marz.extension.fcui import progress_indicator, ui_thread
from freecad.marz.extension.qt import QApplication
from freecad.marz.extension.paths import iconPath
from freecad.marz.feature import MarzInstrument_Name
from freecad.marz.feature.body import BodyFeature
from freecad.marz.feature.instrument_properties import InstrumentProps
from freecad.marz.feature.neck import NeckFeature
from freecad.marz.feature.progress import ProgressListener
from freecad.marz.model.instrument import Instrument
from freecad.marz.utils import traceTime
from freecad.marz.feature.logging import MarzLogger
from freecad.marz.model import fretboard_builder as builder

# make ProgressListener tolerant if it lacks update()
if not hasattr(ProgressListener, "update"):
    def _update(self, value):
        try:
            self.add(f"Progress {value}%")
        except Exception:
            pass
    ProgressListener.update = _update

class MarzInstrument:
    Type = 'MarzInstrument'
    Object = None

    def __init__(self, obj):
        self.Object = obj
        obj.Proxy = self
        MarzInstrumentVP(obj.ViewObject)
        InstrumentProps.create(obj)
        self._suppress_onchange = False

        # only add the FreeCAD property if it's not present already
        if not hasattr(obj, "EDO"):
            obj.addProperty("App::PropertyInteger", "EDO", "Fretboard", "Equal Divisions of the Octave")
            obj.EDO = 12

    def execute(self, obj):
        if not hasattr(self, 'Object') or self.Object is None:
                self.Object = obj
        # --- Sync FreeCAD property to model before building geometry ---
        try:
            if hasattr(obj, "Fretboard_Length") and hasattr(self, "model"):
                val = float(obj.Fretboard_Length)
                if hasattr(self.model, "fretboard") and hasattr(self.model.fretboard, "length"):
                    self.model.fretboard.length = val
        except Exception as e:
            App.Console.PrintWarning(f"[Marz] Could not sync Fretboard_Length to model: {e}\n")
        

    def onChanged(self, obj, prop: str):
        if not hasattr(self, 'Object') or self.Object is None:
            self.Object = obj
        # If we are programmatically updating properties, skip handling changes
        if getattr(self, "_suppress_onchange", False):
            return

        # EDO change: simple rebuild
        if prop == "EDO":
            try:
                self.build_all()
            except Exception as e:
                try:
                    App.Console.PrintWarning(f"[Marz] Rebuild after EDO change failed: {e}\n")
                except Exception:
                    pass
            return

        # Fretboard length change: validate, write to model, and rebuild safely
        if prop == "Fretboard_Length":
            try:
                # Read raw value
                val_raw = getattr(obj, "Fretboard_Length", None)
                if val_raw is None:
                    val = 0.0
                else:
                    try:
                        val = float(val_raw)
                    except Exception:
                        try:
                            val = float(str(val_raw).replace(" mm", "").strip())
                        except Exception:
                            val = 0.0

                # validation/clamping
                min_len = 30.0
                try:
                    max_len = max(getattr(self.model.scale, "bass", 1000.0), 5000.0)
                except Exception:
                    max_len = 5000.0

                if val != 0.0 and val < min_len:
                    try:
                        App.Console.PrintWarning(f"[Marz] Fretboard length too small ({val} mm). Clamping to {min_len} mm.\n")
                    except Exception:
                        pass
                    val = min_len
                if val > max_len:
                    try:
                        App.Console.PrintWarning(f"[Marz] Fretboard length too large ({val} mm). Clamping to {max_len} mm.\n")
                    except Exception:
                        pass
                    val = max_len

                # write to internal model
                try:
                    if hasattr(self, "model") and hasattr(self.model, "fretboard"):
                        self.model.fretboard.length = val
                except Exception:
                    pass

                # Rebuild geometry honoring the new length, but avoid recursion by suppressing onChanged
                try:
                    self._suppress_onchange = True
                except Exception:
                    pass
                try:
                    self.build_all()
                except Exception as e:
                    try:
                        App.Console.PrintWarning(f"[Marz] Rebuild after Fretboard_Length change failed: {e}\n")
                    except Exception:
                        pass
                finally:
                    try:
                        self._suppress_onchange = False
                    except Exception:
                        pass

            except Exception:
                # swallow any unexpected errors so onChanged doesn't break other handlers
                pass

            # handled this property
            return

        # other property changes fallthrough to default behavior (if any)


    def __getstate__(self):
        return None
    def __setstate__(self, state):
        return None
    def dumps(self):
        return None
    def loads(self, state):
        return None

    def build_constructions(self, progress=None):
        # quick-path
        return self.build_all(progress)

    def build_parts(self, progress=None):
        # quick-path
        return self.build_all(progress)

    def build_all(self, progress_listener: ProgressListener = None) -> Instrument:
        # local imports to avoid module-level circulars in FreeCAD load-time
        from freecad.marz.feature.body import BodyFeature
        from freecad.marz.feature.neck import NeckFeature
        from freecad.marz.extension.fcui import progress_indicator
        from freecad.marz.extension.fcdoc import transaction
        from freecad.marz.extension.qt import QApplication
        from freecad.marz.utils import traceTime
        import FreeCAD, Part

        # tolerant dummy progress listener
        if progress_listener is None:
            class DummyProgressListener:
                def add(self, label, duration=None):
                    pass
                def update(self, value):
                    pass
            progress_listener = DummyProgressListener()

        model = Instrument()
        InstrumentProps.save_object_to_model(model, self.Object)

        # Sync EDO from FreeCAD object into model (Instrument.fretboard.edo)
        model.fretboard.edo = getattr(self.Object, 'EDO', getattr(model.fretboard, 'edo', 12))
        # keep uppercase too for compatibility
        model.fretboard.EDO = model.fretboard.edo

        body_builder = BodyFeature(model)
        neck_builder = NeckFeature(model)

        # If the user provided an explicit Fretboard_Length in the FreeCAD UI (non-zero),
        # honor it by writing into the model before building the FretboardData.
        try:
            if hasattr(self.Object, "Fretboard_Length"):
                ui_fb_raw = getattr(self.Object, "Fretboard_Length", None)
                ui_fb = 0.0
                if ui_fb_raw is not None:
                    try:
                        ui_fb = float(ui_fb_raw)
                    except Exception:
                        try:
                            ui_fb = float(str(ui_fb_raw).replace(" mm", "").strip())
                        except Exception:
                            ui_fb = 0.0
                if ui_fb > 0.0:
                    try:
                        model.fretboard.length = ui_fb
                    except Exception:
                        pass
        except Exception:
            pass

        # Build FretboardData from pure model
        fbd = builder.buildFretboardData(model)

        # After builder ran, ensure model.fretboard.length is set and reflect it in the UI property
        model_len = float(getattr(model.fretboard, "length", 0.0) or 0.0)

        if hasattr(self, "_suppress_onchange"):
            try:
                # Prevent onChanged handler from reacting to this programmatic update.
                self._suppress_onchange = True
                if hasattr(self.Object, "Fretboard_Length"):
                    try:
                        self.Object.Fretboard_Length = model_len
                    except Exception:
                        pass
            finally:
                try:
                    self._suppress_onchange = False
                except Exception:
                    # best-effort reset; nothing else to do
                    pass
        else:
            # Fallback: no suppression available, write directly (less safe)
            try:
                if hasattr(self.Object, "Fretboard_Length"):
                    self.Object.Fretboard_Length = model_len
            except Exception:
                pass

        with progress_indicator("Update All..."):
            with transaction("Marz Update Parts"):
                with traceTime('Update Constructions...', progress_listener):
                    builder.createConstructionShapesParts(fbd)
                    QApplication.processEvents()

                with traceTime('Update Fretboard...', progress_listener):
                    # debug: print out model type and fretboard presence
                    try:
                        import FreeCAD
                        FreeCAD.Console.PrintMessage("[Marz DEBUG] About to call createFretboardPart; model type: {}, has model.fretboard? {}\n".format(
                            type(model), hasattr(model, "fretboard") and (model.fretboard is not None)
                        ))
                    except Exception:
                        pass

                    # pass the *model* to createFretboardPart so builder uses EDO and other model values
                    builder.createFretboardPart(fbd, model, progress_listener)

                    QApplication.processEvents()
                # --- Measure actual geometry end and push to UI (fret0 -> nearest geometry point) ---
                try:
                    # find the created fretboard object (heuristic)
                    fb_obj = None
                    for o in App.ActiveDocument.Objects:
                        n = o.Name.lower()
                        if n.startswith("marz_part_fretboard") or "fretboard" in n or n.startswith("ref_fretboard"):
                            fb_obj = o
                            break
                    if fb_obj is not None and hasattr(fbd, "frets") and fbd.frets and hasattr(fbd, "neckFrame"):
                        shp = fb_obj.Shape
                        # safe import for lineIntersection helper
                        try:
                            from freecad.marz.model.neck_data import lineIntersection
                        except Exception:
                            lineIntersection = None

                        if lineIntersection:
                            # helper to convert builder vxy -> FreeCAD.Vector
                            def vxy_to_Vector(vxy):
                                try:
                                    return FreeCAD.Vector(float(vxy.x), float(vxy.y), float(getattr(vxy, 'z', 0.0)))
                                except Exception:
                                    try:
                                        return FreeCAD.Vector(float(vxy[0]), float(vxy[1]), float(vxy[2]) if len(vxy)>2 else 0.0)
                                    except Exception:
                                        return None

                            # get projection points (vxy) and convert
                            try:
                                f0_vxy = lineIntersection(fbd.frets[0], fbd.neckFrame.midLine).point
                                last_vxy = lineIntersection(fbd.frets[-1], fbd.neckFrame.midLine).point
                                f0_proj = vxy_to_Vector(f0_vxy)
                                last_proj = vxy_to_Vector(last_vxy)
                                if last_proj is None or f0_proj is None:
                                    raise Exception("Could not convert projection points to FreeCAD.Vector")
                            except Exception:
                                last_proj = None
                                f0_proj = None
                except Exception:
                    pass

                with traceTime('Update Neck...', progress_listener):
                    neck_builder.createPart(progress_listener)
                    QApplication.processEvents()

                with traceTime('Update Body...', progress_listener):
                    body_builder.create_parts(progress_listener)
                    QApplication.processEvents()

                self.recompute()

        # --- Store built model for later syncing (Fretboard_Length, EDO, etc.) ---
        self.model = model
        return model


    def show_form(self):
        if not hasattr(self, 'form') or self.form is None:
            import freecad.marz.feature.edit_form as lib
            self.form = lib.InstrumentForm(self.Object)
        self.form.open()

    @ui_thread(delay=10)
    def recompute(self):
        if not App.ActiveDocument.Recomputing:
            MarzLogger.info("Recomputing...")
            App.ActiveDocument.recompute()

    def onDocumentRestored(self, obj):
        self.Object = obj
        self.model = Instrument()
        from freecad.marz.feature.instrument_properties import InstrumentProps
        InstrumentProps.migrate(obj)

        # Fix: ensure EDO property is present and synced
        if not hasattr(obj, "EDO"):
            obj.addProperty("App::PropertyInteger", "EDO", "Fretboard", "Equal Divisions of the Octave")
        if hasattr(obj, "edo"):   # some presets may have lowercase
            obj.EDO = int(obj.edo)
        elif not obj.EDO:
            obj.EDO = 12
        # Ensure the FreeCAD Fretboard_Length property exists and sync it from the internal model
        try:
            if not hasattr(obj, "Fretboard_Length"):
                try:
                    obj.addProperty("App::PropertyLength", "Fretboard_Length", "Fretboard",
                                    "Fretboard length (nut to end) in mm. 0 = unset")
                except Exception:
                    # property creation may fail if it already exists under a legacy name
                    pass

            # If the model has a fretboard length, copy it into the FreeCAD property
            try:
                model_len = getattr(self.model.fretboard, "length", None)
                if model_len is None:
                    model_len = 0.0
                # Ensure a float value
                obj.Fretboard_Length = float(model_len)
            except Exception:
                # ignore sync errors so document restoration doesn't fail
                pass
        except Exception:
            # protect the restore process from any unexpected errors
            pass

        # --- Ensure the model and FreeCAD property are initialized properly ---
        try:
            self.model = self.build_all()
            if hasattr(self.model, "fretboard"):
                obj.Fretboard_Length = float(getattr(self.model.fretboard, "length", 0.0))
        except Exception as e:
            App.Console.PrintWarning(f"[Marz] Could not initialize Fretboard_Length after restore: {e}\n")




class MarzInstrumentVP:
    ViewObject = None
    Object = None

    def __init__(self, view_object):
        view_object.Proxy = self
        self.ViewObject = view_object
        self.Object = view_object.Object

    def attach(self, view_object):
        from pivy import coin
        self.ViewObject = view_object
        self.Object = view_object.Object
        view_object.Proxy = self
        self.standard = coin.SoGroup()
        view_object.addDisplayMode(self.standard, "Default")

    def getIcon(self):
        return iconPath('instrument_feature.svg')

    def getDisplayModes(self, view_object):
        return ['Default']

    def getDefaultDisplayMode(self):
        return 'Default'

    def doubleClicked(self, view_object):
        view_object.Object.Proxy.show_form()
        return True

    def __getstate__(self):
        return None
    def __setstate__(self, state):
        return None
    def dumps(self):
        return None
    def loads(self, state):
        return None

    def claimChildren(self):
        if hasattr(self, 'Object') and self.Object:
            return [obj for obj in self.Object.Document.Objects
                    if obj.Name.startswith('Marz_Group_') or obj.Name == 'Marz_Files']
        return []

def MarzInstrumentProxy(doc: App.Document=None) -> MarzInstrument:
    doc = doc or App.activeDocument() or App.newDocument('Instrument')
    obj = doc.getObject(MarzInstrument_Name)
    if obj is None:
        obj = doc.addObject('App::FeaturePython', MarzInstrument_Name)
        obj.Label = "Instrument Parameters"
        MarzInstrument(obj)
    return obj.Proxy
