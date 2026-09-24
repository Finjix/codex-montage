from enum import IntFlag

import comtypes.gen._00020430_0000_0000_C000_000000000046_0_2_0 as __wrapper_module__
from comtypes.gen._00020430_0000_0000_C000_000000000046_0_2_0 import (
    IDispatch, FONTSTRIKETHROUGH, OLE_HANDLE, Monochrome, GUID,
    DISPMETHOD, COMMETHOD, OLE_YPOS_HIMETRIC, Font,
    OLE_YPOS_CONTAINER, OLE_COLOR, IFontEventsDisp, FONTITALIC,
    OLE_XPOS_HIMETRIC, DISPPARAMS, typelib_path, FONTSIZE, StdPicture,
    Library, FontEvents, OLE_YSIZE_PIXELS, OLE_YPOS_PIXELS, StdFont,
    VARIANT_BOOL, IFontDisp, FONTBOLD, _lcid, FONTNAME, Checked,
    OLE_XSIZE_HIMETRIC, OLE_XPOS_CONTAINER, CoClass, dispid,
    OLE_YSIZE_CONTAINER, OLE_YSIZE_HIMETRIC, IPicture,
    OLE_XSIZE_CONTAINER, Gray, Color, OLE_CANCELBOOL, OLE_XPOS_PIXELS,
    EXCEPINFO, IEnumVARIANT, OLE_XSIZE_PIXELS, _check_version,
    Picture, Unchecked, IUnknown, IFont, OLE_ENABLEDEFAULTBOOL,
    FONTUNDERSCORE, Default, BSTR, VgaColor, DISPPROPERTY,
    IPictureDisp, HRESULT, OLE_OPTEXCLUSIVE
)


class OLE_TRISTATE(IntFlag):
    Unchecked = 0
    Checked = 1
    Gray = 2


class LoadPictureConstants(IntFlag):
    Default = 0
    Monochrome = 1
    VgaColor = 2
    Color = 4


__all__ = [
    'FONTSTRIKETHROUGH', 'IPicture', 'OLE_HANDLE', 'Monochrome',
    'OLE_XSIZE_CONTAINER', 'OLE_YPOS_HIMETRIC', 'Gray', 'Font',
    'OLE_YPOS_CONTAINER', 'Color', 'IFontEventsDisp', 'FONTITALIC',
    'OLE_XPOS_HIMETRIC', 'OLE_COLOR', 'typelib_path', 'FONTSIZE',
    'OLE_CANCELBOOL', 'OLE_XPOS_PIXELS', 'StdPicture', 'Library',
    'FontEvents', 'OLE_XSIZE_PIXELS', 'OLE_TRISTATE',
    'OLE_YSIZE_PIXELS', 'OLE_YPOS_PIXELS', 'StdFont', 'Picture',
    'LoadPictureConstants', 'Unchecked', 'IFontDisp', 'IFont',
    'FONTBOLD', 'OLE_ENABLEDEFAULTBOOL', 'FONTUNDERSCORE', 'FONTNAME',
    'Checked', 'Default', 'OLE_XSIZE_HIMETRIC', 'OLE_XPOS_CONTAINER',
    'VgaColor', 'IPictureDisp', 'OLE_YSIZE_CONTAINER',
    'OLE_YSIZE_HIMETRIC', 'OLE_OPTEXCLUSIVE'
]

