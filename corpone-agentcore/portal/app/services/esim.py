"""eSIM activation code + QR generation (GSMA SGP.22 'LPA:1$<SM-DP+>$<MatchingID>')."""
import io

import qrcode

from app.config import get_settings
from app.models import Sim


def activation_code(sim: Sim) -> str:
    smdp = sim.smdp_address or get_settings().default_smdp_address
    matching_id = sim.matching_id or sim.iccid
    return f"LPA:1${smdp}${matching_id}"


def qr_png(data: str) -> bytes:
    img = qrcode.make(data, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=4)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
