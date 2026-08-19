"""
recommendation.py
Converts internal signals (crowd tier, headway, bunching risk) into a single
plain-language decision card for commuters. This is the ONLY thing the
frontend renders as the "hero" recommendation -- no raw numbers, formulas,
or model scores are exposed to the end user, per the product requirement
that the UI stay usable by the general public.
"""

HIGH_TIER = "high"
LOW_MED_TIERS = ("low", "moderate")


def _mins(seconds):
    return max(1, round(seconds / 60))


def build_recommendation(lead, trail, headway_s, predicted_bunch_s, lang_neutral=True):
    """
    lead / trail: dicts with 'tier' ('low'|'moderate'|'high') and bus info
    headway_s: current headway in seconds between trail and lead bus
    predicted_bunch_s: ML-predicted seconds until bunching (used only to
        flag risk internally -- not shown as a number to the user)
    Returns a card dict the frontend renders directly.
    """
    headway_min = _mins(headway_s)
    bunching_soon = predicted_bunch_s < 240  # model thinks bunching within 4 min

    lead_crowded = lead["tier"] == HIGH_TIER
    trail_has_room = trail["tier"] in LOW_MED_TIERS

    if lead_crowded and trail_has_room and headway_s <= 4 * 60:
        return {
            "status": "wait",
            "color": "red",
            "icon": "hand",
            "title_en": "Let this one go",
            "title_ta": "இதை விட்டுவிடுங்கள்",
            "subtitle_en": f"It's packed. A roomier bus is about {headway_min} min behind with seats available.",
            "subtitle_ta": f"இது நிறைந்துள்ளது. இடம் கொண்ட அடுத்த பேருந்து சுமார் {headway_min} நிமிடத்தில் வரும்.",
            "eta_min": headway_min,
        }

    if lead_crowded and headway_s > 12 * 60:
        return {
            "status": "board_if_urgent",
            "color": "amber",
            "icon": "clock",
            "title_en": "Crowded, but next one is far",
            "title_ta": "நெரிசல், அடுத்தது தூரம்",
            "subtitle_en": f"This bus is full. The next one is about {headway_min} min away — board now if you're in a hurry.",
            "subtitle_ta": f"இந்த பேருந்து நிறைந்துள்ளது. அடுத்தது சுமார் {headway_min} நிமிடத்தில். அவசரமாக இருந்தால் இதிலேயே ஏறுங்கள்.",
            "eta_min": headway_min,
        }

    if lead_crowded and bunching_soon:
        return {
            "status": "wait_short",
            "color": "amber",
            "icon": "hand",
            "title_en": "A better bus is close behind",
            "title_ta": "பின்னால் சிறந்த பேருந்து",
            "subtitle_en": "This one's full, and a lighter bus is catching up fast. A short wait may get you a seat.",
            "subtitle_ta": "இது நிறைந்துள்ளது, இலகுவான பேருந்து விரைவில் வரும். சிறிது காத்திருந்தால் இருக்கை கிடைக்கலாம்.",
            "eta_min": headway_min,
        }

    if lead["tier"] == "moderate":
        return {
            "status": "normal",
            "color": "green",
            "icon": "check",
            "title_en": "Good to board",
            "title_ta": "ஏறலாம்",
            "subtitle_en": "Standing room available. No need to wait.",
            "subtitle_ta": "நின்று செல்ல இடம் உள்ளது. காத்திருக்க தேவையில்லை.",
            "eta_min": headway_min,
        }

    return {
        "status": "normal",
        "color": "green",
        "icon": "check",
        "title_en": "Good to board",
        "title_ta": "ஏறலாம்",
        "subtitle_en": "Plenty of seats on this bus.",
        "subtitle_ta": "இந்த பேருந்தில் இருக்கைகள் நிறைய உள்ளன.",
        "eta_min": headway_min,
    }
