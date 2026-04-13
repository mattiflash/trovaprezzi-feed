import re


def _clean(value):
    if value is None:
        return ""
    return str(value).strip()


def extract_lead_time(value):
    """
    Estrae il numero da stringhe tipo:
    '7 gg', '10 giorni', '30'
    """
    value = _clean(value)
    if not value:
        return None

    match = re.search(r"\d+", value)
    if not match:
        return None

    return int(match.group(0))


def parse_price(value):
    """
    Converte stringhe tipo:
    99,99
    99.99
    1.299,99
    1,299.99
    """
    if value is None:
        return None

    value = str(value).strip()

    if value == "":
        return None

    value = value.replace(" ", "")

    if "," in value and "." in value:
        if value.find(",") > value.find("."):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
    else:
        value = value.replace(",", ".")

    try:
        return float(value)
    except Exception:
        return None


def matches_filters(product, variant, filters):
    # -------------------------
    # BRAND
    # -------------------------
    brand_filters = filters.get("brand", [])
    allowed_brands = [
        _clean(b).lower()
        for b in brand_filters
        if isinstance(b, str) and _clean(b)
    ]

    if allowed_brands:
        product_brand = _clean(product.get("vendor")).lower()

        if product_brand not in allowed_brands:
            return False

    # -------------------------
    # COLLECTION
    # -------------------------
    collection_filters = filters.get("collection", [])
    allowed_collections = [
        _clean(c).lower()
        for c in collection_filters
        if isinstance(c, str) and _clean(c)
    ]

    if allowed_collections:
        matched = False

        for c in product.get("collections", {}).get("edges", []):
            node = c.get("node", {})
            handle = _clean(node.get("handle")).lower()
            title = _clean(node.get("title")).lower()

            if handle in allowed_collections or title in allowed_collections:
                matched = True
                break

        if not matched:
            return False

    # -------------------------
    # AVAILABILITY
    # 0 = non disponibile
    # 1 = limitata
    # >1 = disponibile
    # -------------------------
    availability_filter = _clean(filters.get("availability")).lower()

    if availability_filter:
        qty = variant.get("inventoryQuantity")
        qty = 0 if qty is None else qty

        if qty > 1:
            current_availability = "disponibile"
        elif qty == 1:
            current_availability = "limitata"
        else:
            current_availability = "non disponibile"

        if availability_filter != current_availability:
            return False

    # -------------------------
    # PRICE RANGES (OR logic)
    # -------------------------
    price = parse_price(variant.get("price"))
    if price is None:
        return False

    use_price_ranges = filters.get("use_price_ranges", False)
    price_mins = filters.get("price_min", [])
    price_maxs = filters.get("price_max", [])

    if use_price_ranges:
        valid_ranges = []

        if not isinstance(price_mins, list):
            price_mins = [price_mins]
        if not isinstance(price_maxs, list):
            price_maxs = [price_maxs]

        max_len = max(len(price_mins), len(price_maxs))

        for i in range(max_len):
            min_raw = price_mins[i] if i < len(price_mins) else ""
            max_raw = price_maxs[i] if i < len(price_maxs) else ""

            min_raw = _clean(min_raw)
            max_raw = _clean(max_raw)

            if min_raw == "" and max_raw == "":
                continue

            min_val = parse_price(min_raw) if min_raw != "" else None
            max_val = parse_price(max_raw) if max_raw != "" else None

            if min_raw != "" and min_val is None:
                continue
            if max_raw != "" and max_val is None:
                continue

            valid_ranges.append((min_val, max_val))

        if not valid_ranges:
            return False

        matched_any_range = False

        for min_val, max_val in valid_ranges:
            if min_val is not None and price < min_val:
                continue
            if max_val is not None and price > max_val:
                continue

            matched_any_range = True
            break

        if not matched_any_range:
            return False

    # -------------------------
    # LEAD TIME
    # -------------------------
    use_lead_time = filters.get("use_lead_time", False)
    lead_time_min = filters.get("lead_time_min", "")
    lead_time_max = filters.get("lead_time_max", "")

    if use_lead_time:
        metafield = product.get("metafield")
        lead_time_raw = metafield.get("value") if metafield else None
        lead_time_days = extract_lead_time(lead_time_raw)

        if lead_time_days is None:
            return False

        if lead_time_min != "":
            try:
                if lead_time_days < int(lead_time_min):
                    return False
            except (TypeError, ValueError):
                return False

        if lead_time_max != "":
            try:
                if lead_time_days > int(lead_time_max):
                    return False
            except (TypeError, ValueError):
                return False

    return True