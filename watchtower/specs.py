"""Heuristic extraction of laptop/tablet/phone specs from source text.

Everything returned is *source-stated text parsed by rules*, not verified against a datasheet.
Unknown keys are simply absent (missing != zero).
"""
import re

BRANDS = ["Raspberry Pi", "Adafruit", "SparkFun", "Arduino", "Pimoroni", "Seeed", "DFRobot", "Pololu", "Espressif", "micro:bit", "Teensy", "Apple", "Asus", "Acer", "Lenovo", "HP", "Dell", "Samsung", "MSI", "Packard", "Toshiba",
          "Huawei", "Amazon", "Microsoft", "Sony", "Nokia", "Xiaomi", "Razer", "LG", "Prestigio",
          "IdeaTab", "Ubuntu", "Aspire", "ThinkPad", "iPad", "iPhone", "Galaxy", "Kindle"]
_BRAND_ALIAS = {"thinkpad": "Lenovo", "ideatab": "Lenovo", "aspire": "Acer", "ipad": "Apple",
                "iphone": "Apple", "galaxy": "Samsung", "kindle": "Amazon", "ubuntu": None}

_CPU_FAMILIES = [
    ("core_i9", r"\bi9\b|core\s*i9"), ("core_i7", r"\bi7\b|core\s*i7"), ("core_i5", r"\bi5\b|core\s*i5"),
    ("core_i3", r"\bi3\b|core\s*i3"), ("ryzen", r"ryzen"), ("apple_m", r"\bm[1-4]\b|apple\s*m[1-4]"),
    ("celeron", r"celeron"), ("pentium", r"pentium"), ("atom", r"\batom\b"), ("amd_e", r"\bamd\s*e[12]?-"),
    ("amd_a", r"\bamd\s*a[4-9]-"), ("core_m", r"core\s*m\b"), ("xeon", r"xeon"),
]
CPU_TIER = {"core_i9": 5, "core_i7": 4, "ryzen": 4, "apple_m": 4, "core_i5": 3, "core_i3": 2,
            "core_m": 2, "amd_a": 2, "pentium": 1, "celeron": 1, "atom": 1, "amd_e": 1, "xeon": 4}
_OS = ["Windows 11", "Windows 10", "Windows 8.1", "Windows 7", "Windows", "Linux", "Endless OS",
       "FreeDOS", "DOS", "macOS", "Mac OS", "Chrome OS", "Android", "iOS"]


_THIRD_PARTY = re.compile(r"(for|compatible with|fits|fit|works with|designed for|suitable for|supports?)\s+(the\s+)?$")


def detect_brand(title: str) -> str | None:
    """Brand named in the title — unless it only appears as 'for <brand>' / 'compatible with <brand>' (an accessory by
    someone else; naming the platform it fits does not make it that brand's product)."""
    low = title.lower()
    for b in BRANDS:
        for m in re.finditer(rf"\b{re.escape(b.lower())}\b", low):
            if _THIRD_PARTY.search(low[max(0, m.start() - 20):m.start()]):
                continue
            return _BRAND_ALIAS.get(b.lower(), b)
    return None


def parse_specs(title: str, description: str, category: str | None = None) -> dict:
    text = f"{title}, {description}"
    specs: dict = {}
    m = re.search(r'(\d{1,2}(?:\.\d)?)\s*(?:"|”|″|\'\'|inch\b|-inch\b)', text)
    if m:
        specs["screen_in"] = float(m.group(1))
    low = text.lower()
    for fam, pat in _CPU_FAMILIES:
        if re.search(pat, low):
            specs["cpu_family"] = fam
            break
    # capacities: labels beat position ("128GB SSD 4GB RAM"); unlabeled figures fall back to retail order (RAM, storage)
    ram = storage = None
    unlabeled: list[int] = []
    for m in re.finditer(r"\b(\d{1,4})\s*(GB|TB)\b(?:\s*(?:of\s+)?(SSD|HDD|eMMC|NVMe|storage|flash|ROM|RAM|memory|DDR\w*|LPDDR\w*))?", text, re.I):
        val = int(m.group(1)) * (1024 if m.group(2).upper() == "TB" else 1)
        tag = (m.group(3) or "").lower()
        if tag in ("ram", "memory") or tag.startswith(("ddr", "lpddr")):
            ram = ram if ram is not None else val
        elif tag:
            storage = max(storage or 0, val)
        else:
            unlabeled.append(val)
    if category == "maker-electronics":
        # Boards, modules and cards: a bare "16GB" may be RAM (Pi 4/5), eMMC (Compute Module 3+) or an SD card, so it is
        # never assumed to be RAM. Storage words make it storage; otherwise it is kept as an AMBIGUOUS capacity.
        if len(unlabeled) == 1 and ram is None and storage is None:
            if re.search(r"\b(sd|microsd|card|emmc|flash|storage|ssd|nvme)\b", low):
                storage = unlabeled[0]
            else:
                specs["capacity_gb"] = unlabeled[0]
        unlabeled = []
    for val in unlabeled:
        if ram is None and storage is None and len(unlabeled) >= 2 and val <= 64 and val == unlabeled[0]:
            ram = val
        elif storage is None and (ram is not None or len(unlabeled) >= 2 or category in ("tablet", "phone") or val > 32):
            storage = max(storage or 0, val) if val != ram else storage
        elif ram is None and val <= 32 and category not in ("tablet", "phone"):
            ram = val
    if ram is not None:
        specs["ram_gb"] = ram
    if storage:
        specs["storage_gb"] = storage
    if re.search(r"\bssd\b|nvme", low):
        specs["storage_type"] = "ssd"
    elif re.search(r"\bhdd\b", low):
        specs["storage_type"] = "hdd"
    elif re.search(r"\bemmc\b", low):
        specs["storage_type"] = "emmc"
    for os_ in _OS:
        if os_.lower() in low:
            specs["os"] = os_
            break
    return specs


_CORP = re.compile(r"\b(ltd|limited|inc|incorporated|llc|corp|corporation|co|company|gmbh|industries|electronics|technologies|technology|plc|sa)\b\.?")


def canon_brand(brand: str | None) -> str:
    """'Raspberry Pi Ltd' / 'RASPBERRY PI' / 'Raspberry Pi Foundation' -> 'raspberry pi'. Empty string if unknown."""
    if not brand:
        return ""
    b = re.sub(r"[^a-z0-9: ]", " ", brand.lower())
    b = _CORP.sub(" ", b)
    b = re.sub(r"\b(foundation|trading|official)\b", " ", b)
    return re.sub(r"\s+", " ", b).strip()
