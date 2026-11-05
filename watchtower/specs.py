"""Heuristic extraction of laptop/tablet/phone specs from source text.

Everything returned is *source-stated text parsed by rules*, not verified against a datasheet.
Unknown keys are simply absent (missing != zero).
"""
import re

BRANDS = ["Apple", "Asus", "Acer", "Lenovo", "HP", "Dell", "Samsung", "MSI", "Packard", "Toshiba",
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


def detect_brand(title: str) -> str | None:
    low = title.lower()
    for b in BRANDS:
        if re.search(rf"\b{re.escape(b.lower())}\b", low):
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
    # capacities in order of appearance. Convention in retail copy: RAM first, then storage.
    caps = [int(n) * (1024 if u.upper() == "TB" else 1)
            for n, u in re.findall(r"\b(\d{1,4})\s*(GB|TB)\b", text, re.I)]
    if len(caps) >= 2 and caps[0] <= 64:
        specs["ram_gb"] = caps[0]
        specs["storage_gb"] = max(caps[1:])
    elif len(caps) == 1:
        # ambiguous single figure: small => RAM, large => storage
        small_is_ram = caps[0] <= 32 and category not in ("tablet", "phone")
        specs["ram_gb" if small_is_ram else "storage_gb"] = caps[0]
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
