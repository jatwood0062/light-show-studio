"""Govee model table for the LAN / "razer" per-segment realtime command.

Data is sourced in docs/DEVICES.md. `segments` and `razer` are None where no source says.
`razer=True` means a source or the project owner confirmed the BB..B0 colour frame works;
it does NOT mean every SKU in the same family does. The official LAN API list is not
encoded here (see DEVICES.md); the table lists models seen in the openHAB Govee LAN-API
binding (117 entries) plus H6199 and the C9 SKUs.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Model:
    sku: str
    name: str
    kind: str
    segments: int | None
    razer: bool | None


_MODELS = (
    Model('H6042', 'Govee TV Light Bar #2', 'tv_backlight', None, None),
    Model('H6043', 'Govee TV Light Bars #2', 'tv_backlight', None, None),
    Model('H6046', 'RGBIC TV Light Bars', 'tv_backlight', None, None),
    Model('H6047', 'RGBIC Gaming Light Bars with Smart Controller', 'tv_backlight', None, None),
    Model('H6051', 'Aura Smart Table Lamp', 'table_lamp', None, None),
    Model('H6052', 'Govee Table Lamp', 'table_lamp', None, None),
    Model('H6056', 'Flow Plus', 'table_lamp', None, None),
    Model('H6059', 'RGBWW Night Light for Kids', 'table_lamp', None, None),
    Model('H6061', 'Glide Hexa LED Panels', 'panel', None, None),
    Model('H6062', 'Glide Wall Light', 'panel', None, None),
    Model('H6063', 'Gaming Wall Light', 'panel', None, None),
    Model('H6065', 'Glide RGBIC Y Lights', 'panel', None, None),
    Model('H6066', 'Glide Hexa Pro LED Panel', 'panel', None, None),
    Model('H6067', 'Glide Triangle Light Panels', 'panel', None, None),
    Model('H606A', 'Glide Hexa Light Panel Ultra', 'panel', None, None),
    Model('H6072', 'RGBICWW Corner Floor Lamp', 'floor_lamp', None, None),
    Model('H6073', 'LED Floor Lamp', 'floor_lamp', None, None),
    Model('H6076', 'RGBICW Smart Corner Floor Lamp', 'floor_lamp', None, None),
    Model('H6078', 'Cylinder Floor Lamp', 'floor_lamp', None, None),
    Model('H607C', 'Floor Lamp #2', 'floor_lamp', None, None),
    Model('H6087', 'RGBIC Smart Wall Sconces', 'panel', None, None),
    Model('H6088', 'RGBIC Cube Wall Sconces', 'panel', None, None),
    Model('H608A', 'String Downlights 5M', 'string', None, None),
    Model('H608B', 'String Downlights 3M', 'string', None, None),
    Model('H608C', 'String Downlights 2M', 'string', None, None),
    Model('H608D', 'String Downlights 10M', 'string', None, None),
    Model('H60A0', 'Ceiling Light', 'ceiling', None, None),
    Model('H60A1', 'Smart Ceiling Light', 'ceiling', None, None),
    Model('H610A', 'Glide Lively Wall Lights', 'panel', None, None),
    Model('H610B', 'Music Wall Lights', 'panel', None, None),
    Model('H6110', '2x5M Multicolor Strip with Alexa', 'strip', None, None),
    Model('H6117', 'Dream Color LED Strip Light 10M', 'strip', None, None),
    Model('H6141', '5M Smart Multicolor Strip Light', 'strip', None, None),
    Model('H6143', '5M Strip Light', 'strip', None, None),
    Model('H6144', '2x5M Strip Light', 'strip', None, None),
    Model('H6159', 'RGB Light Strip', 'strip', None, None),
    Model('H615A', '5M Light Strip with Alexa', 'strip', None, None),
    Model('H615B', '10M Light Strip with Alexa', 'strip', None, None),
    Model('H615C', '15M Light Strip with Alexa', 'strip', None, None),
    Model('H615D', '20M Light Strip with Alexa', 'strip', None, None),
    Model('H615E', '30M Light Strip with Alexa', 'strip', None, None),
    Model('H6163', 'Dreamcolor LED Strip Light 5M', 'strip', None, None),
    Model('H6167', 'TV Backlight 2.4M', 'tv_backlight', None, None),
    Model('H6168', 'TV Backlight 2x0.7M+2x1.2M', 'tv_backlight', None, None),
    Model('H616C', 'Outdoor Strip 10M', 'strip', None, None),
    Model('H616D', 'Outdoor Strip 2x7.5M', 'strip', None, None),
    Model('H616E', 'Outdoor Strip 2x10M', 'strip', None, None),
    Model('H6172', 'Outdoor LED Strip 10M', 'strip', None, None),
    Model('H6173', 'RGBIC Outdoor Strip Lights', 'strip', None, None),
    Model('H6175', 'RGBIC Outdoor Strip Lights 10M', 'strip', None, None),
    Model('H6176', 'RGBIC Outdoor Strip Lights 30M', 'strip', None, None),
    Model('H6182', 'WiFi Multicolor TV Strip Light', 'tv_backlight', None, None),
    Model('H618A', 'RGBIC Basic LED Strip Lights 5M', 'strip', 15, True),
    Model('H618C', 'RGBIC Basic LED Strip Lights 5M', 'strip', None, None),
    Model('H618E', 'LED Strip Lights 22M', 'strip', None, None),
    Model('H618F', 'RGBIC LED Strip Lights', 'strip', None, None),
    Model('H619A', 'Strip Lights with Protective Coating 5M', 'strip', None, None),
    Model('H619B', 'Strip Lights with Protective Coating 7.5M', 'strip', None, None),
    Model('H619C', 'Strip Lights with Protective Coating 10M', 'strip', None, None),
    Model('H619D', 'Pro LED Strip Lights 2x7.5M', 'strip', None, None),
    Model('H619E', 'Strip Lights with Protective Coating 2x10M', 'strip', None, None),
    Model('H619Z', 'Pro LED Strip Lights 3M', 'strip', None, None),
    Model('H61A0', 'RGBIC Neon Rope Light 3M', 'neon_rope', None, None),
    Model('H61A1', 'RGBIC Neon Rope Light 2M', 'neon_rope', None, None),
    Model('H61A2', 'RGBIC Neon Rope Light 5M', 'neon_rope', None, None),
    Model('H61A3', 'RGBIC Neon Rope Light 4M', 'neon_rope', None, None),
    Model('H61A5', 'Neon LED Strip Light 10M', 'neon_rope', None, None),
    Model('H61A8', 'Neon Rope Light 10M/20M', 'neon_rope', None, None),
    Model('H61B1', 'Strip Light with Cover 5M', 'strip', None, None),
    Model('H61B2', 'RGBIC Neon TV Backlight 3M', 'tv_backlight', None, None),
    Model('H61BA', 'LED Strip Light 5M', 'strip', None, None),
    Model('H61BC', 'LED Strip Light 10M', 'strip', None, None),
    Model('H61BE', 'LED Strip Light 2x10M', 'strip', None, None),
    Model('H61C2', 'Neon LED Strip Light 2M/3M/5M', 'neon_rope', None, None),
    Model('H61D3', 'Neon Rope Light 2 3M', 'neon_rope', None, None),
    Model('H61D5', 'Neon Rope Light 2 5M', 'neon_rope', None, None),
    Model('H61E0', 'LED Strip Light M1', 'strip', None, None),
    Model('H61E1', 'LED Strip Light M1', 'strip', None, None),
    Model('H6199', 'DreamView T1 TV Backlight', 'tv_backlight', None, None),
    Model('H7012', 'Warm White Outdoor String Lights', 'string', None, None),
    Model('H7013', 'Warm White Outdoor String Lights', 'string', None, None),
    Model('H7021', 'RGBIC Warm White Smart Outdoor String', 'string', None, None),
    Model('H7028', 'Lynx Dream LED-Bulb String', 'string', None, None),
    Model('H7033', 'LED-Bulb String Lights', 'string', None, None),
    Model('H7041', 'LED Outdoor Bulb String Lights', 'string', None, None),
    Model('H7042', 'LED Outdoor Bulb String Lights', 'string', None, None),
    Model('H7050', 'Outdoor Ground Lights 11M', 'outdoor_fixture', None, None),
    Model('H7051', 'Outdoor Ground Lights 15M', 'outdoor_fixture', None, None),
    Model('H7052', 'Outdoor Ground Lights 15M/30M', 'outdoor_fixture', None, None),
    Model('H7055', 'Pathway Light', 'outdoor_fixture', None, None),
    Model('H705A', 'Permanent Outdoor Lights 30M', 'permanent_outdoor', None, None),
    Model('H705B', 'Permanent Outdoor Lights 15M', 'permanent_outdoor', None, None),
    Model('H705C', 'Permanent Outdoor Lights 45M', 'permanent_outdoor', None, None),
    Model('H705D', 'Permanent Outdoor Lights #2 15M', 'permanent_outdoor', None, None),
    Model('H705E', 'Permanent Outdoor Lights #2 30M', 'permanent_outdoor', None, None),
    Model('H705F', 'Permanent Outdoor Lights #2 45M', 'permanent_outdoor', None, None),
    Model('H7060', 'LED Flood Lights (2-Pack)', 'outdoor_fixture', None, None),
    Model('H7061', 'LED Flood Lights (4-Pack)', 'outdoor_fixture', None, None),
    Model('H7062', 'LED Flood Lights (6-Pack)', 'outdoor_fixture', None, None),
    Model('H7063', 'Outdoor Flood Lights', 'outdoor_fixture', None, None),
    Model('H7065', 'Outdoor Spot Lights', 'outdoor_fixture', None, None),
    Model('H7066', 'Outdoor Spot Lights', 'outdoor_fixture', None, None),
    Model('H706A', 'Permanent Outdoor Lights Pro 30M', 'permanent_outdoor', None, None),
    Model('H706B', 'Permanent Outdoor Lights Pro 45M', 'permanent_outdoor', None, None),
    Model('H706C', 'Permanent Outdoor Lights Pro 60M', 'permanent_outdoor', None, None),
    Model('H7070', 'Outdoor Projector Light', 'outdoor_fixture', None, None),
    Model('H7075', 'Outdoor Wall Light', 'panel', None, None),
    Model('H70B1', '520 LED Curtain Lights', 'curtain', None, None),
    Model('H70BC', '400 LED Curtain Lights', 'curtain', None, None),
    Model('H70C1', 'RGBIC String Light 10M (Christmas String Lights)', 'string', None, None),
    Model('H70C2', 'RGBIC String Light 20M (Christmas String Lights)', 'string', None, None),
    Model('H6860', 'C9 Christmas String Lights 50 bulbs (49ft)', 'c9_string', 50, True),
    Model('H6861', 'C9 Christmas String Lights 100 bulbs (98ft)', 'c9_string', None, None),
    Model('R6861', 'C9 Christmas String Lights (LAN reports H6860 as R6861)', 'c9_string', 50, True),
    Model('H805A', 'Permanent Outdoor Lights Elite 30M', 'permanent_outdoor', None, None),
    Model('H805B', 'Permanent Outdoor Lights Elite 15M', 'permanent_outdoor', None, None),
    Model('H805C', 'Permanent Outdoor Lights Elite 45M', 'permanent_outdoor', None, None),
)

MODELS: dict[str, Model] = {m.sku: m for m in _MODELS}


def lookup(sku: str) -> Model | None:
    """Case-insensitive exact SKU lookup; None if the SKU is not listed."""
    return MODELS.get((sku or "").strip().upper())
