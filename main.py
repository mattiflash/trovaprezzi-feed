import os
import re
import requests
from dotenv import load_dotenv
from pathlib import Path

# =========================
# ENV
# =========================
env_path = Path(__file__).with_name(".env")
load_dotenv(dotenv_path=env_path)

SHOP_DOMAIN = os.getenv("SHOP_DOMAIN")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
PUBLIC_DOMAIN = os.getenv("PUBLIC_DOMAIN")

if not SHOP_DOMAIN or not CLIENT_ID or not CLIENT_SECRET or not PUBLIC_DOMAIN:
    raise RuntimeError(
        "Variabili .env mancanti. Controlla SHOP_DOMAIN, CLIENT_ID, CLIENT_SECRET, PUBLIC_DOMAIN."
    )

# =========================
# CONFIG
# =========================
PRIORITY_COLLECTIONS = {
    "fotocamere-mirrorless": "Fotografia;Fotocamere Digitali;Mirrorless",
    "mirrorless": "Fotografia;Fotocamere Digitali;Mirrorless",
    "foto-video-nikon-fotocamere-mirrorless": "Fotografia;Fotocamere Digitali;Mirrorless",
    "obiettivi-mirrorless": "Fotografia;Obiettivi Fotografici;Obiettivi Mirrorless",
    "trigger-flash": "Fotografia;Flash e Illuminazione;Trigger Flash",
    "flash": "Fotografia;Flash e Illuminazione;Flash",
    "accessori-illuminazione": "Fotografia;Flash e Illuminazione;Accessori Illuminazione",
}

BLACKLIST = {
    "home",
    "frontpage",
    "in-evidenza",
    "novita",
    "promo",
    "avada-best-sellers",
    "best-seller",
    "bestseller",
    "nikon-estasconti",
    "winter-promotion-2023-24-nikon",
    "nikon-winter-promotion-sconto-in-cassa",
    "fotografia",
    "videografia",
    "nikon-1",
}

MAX_OUTPUT_ROWS = 10

INCLUDE_AVAILABLE_PRODUCTS = True
INCLUDE_OUT_OF_STOCK_PRODUCTS = True

# =========================
# UTILS
# =========================
def clean(value):
    if not value:
        return ""
    value = str(value)
    value = value.replace("|", " - ")
    value = value.replace("\n", " ").replace("\r", " ")
    return re.sub(r"\s+", " ", value).strip()


def clean_description(text, title=""):
    text = clean(text)
    title = clean(title)

    if not text:
        return ""

    if text.lower() == title.lower():
        return ""

    max_len = 255

    if len(text) <= max_len:
        return text

    trimmed = text[:max_len]
    last_space = trimmed.rfind(" ")
    if last_space > 0:
        trimmed = trimmed[:last_space]

    trimmed = trimmed.strip(" ,.;:-")
    return trimmed


def extract_lead_time(value):
    """
    Estrae il numero da stringhe tipo:
    '7 gg', '10 giorni', '3'
    """
    value = clean(value).lower()
    if not value:
        return None

    match = re.search(r"\d+", value)
    if not match:
        return None

    return int(match.group(0))


def get_availability_label(qty):
    """
    Disponibilità richiesta dal capo:
    0 = non disponibile
    1 = limitata
    >1 = disponibile
    """
    if qty is None:
        return ""

    if qty > 1:
        return "disponibile"

    if qty == 1:
        return "limitata"

    return "non disponibile"


def should_include_product(qty):
    """
    Regola semplice:
    - qty > 0 -> entra solo se INCLUDE_AVAILABLE_PRODUCTS = True
    - qty <= 0 -> entra solo se INCLUDE_OUT_OF_STOCK_PRODUCTS = True
    """
    if qty is None:
        return False

    if qty > 0:
        return INCLUDE_AVAILABLE_PRODUCTS

    return INCLUDE_OUT_OF_STOCK_PRODUCTS


# =========================
# AUTH
# =========================
def get_token():
    url = f"https://{SHOP_DOMAIN}/admin/oauth/access_token"
    payload = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "client_credentials",
    }

    r = requests.post(url, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


# =========================
# GRAPHQL
# =========================
def graphql(token, query, variables=None):
    url = f"https://{SHOP_DOMAIN}/admin/api/2026-04/graphql.json"

    r = requests.post(
        url,
        headers={
            "X-Shopify-Access-Token": token,
            "Content-Type": "application/json"
        },
        json={"query": query, "variables": variables or {}},
        timeout=30
    )

    r.raise_for_status()
    data = r.json()

    if "errors" in data:
        raise Exception(data["errors"])

    return data["data"]


def get_products(token, first=50):
    query = """
    query ($first: Int!) {
      products(first: $first) {
        edges {
          node {
            title
            handle
            status
            vendor
            description
            featuredImage {
              url
            }
            metafield(namespace: "custom", key: "lead_time_consegna") {
              value
            }
            collections(first: 10) {
              edges {
                node {
                  handle
                }
              }
            }
            variants(first: 20) {
              edges {
                node {
                  id
                  sku
                  barcode
                  price
                  inventoryQuantity
                }
              }
            }
          }
        }
      }
    }
    """
    return graphql(token, query, {"first": first})["products"]["edges"]


# =========================
# CATEGORY LOGIC
# =========================
def pick_category(collections):
    cleaned_handles = []

    for c in collections:
        handle = clean(c["node"]["handle"]).lower()
        if not handle or handle in BLACKLIST:
            continue
        cleaned_handles.append(handle)

    for handle in cleaned_handles:
        if handle in PRIORITY_COLLECTIONS:
            return PRIORITY_COLLECTIONS[handle]

    return None


def get_product_lead_time(product_node):
    metafield = product_node.get("metafield")
    if not metafield:
        return None

    raw_value = metafield.get("value")
    return extract_lead_time(raw_value)


# =========================
# FEED
# =========================
def build_feed(products):
    lines = []

    header = (
        "Nome|Marca|Descrizione|Prezzo Vendita|Codice Interno|Link all’offerta|"
        "Disponibilità|Albero Categorie|Link Immagine|Spese di Spedizione|Codice EAN <endrecord>"
    )
    lines.append(header)

    added = 0

    for edge in products:
        p = edge["node"]

        if p["status"] != "ACTIVE":
            continue

        category = pick_category(p["collections"]["edges"])
        if not category:
            continue

        title = clean(p["title"])
        brand = clean(p["vendor"])
        description = clean_description(p.get("description", ""), p.get("title", ""))
        handle = clean(p["handle"])
        image = clean((p.get("featuredImage") or {}).get("url"))

        # opzionale: letto ma non usato nel feed
        lead_time = get_product_lead_time(p)

        if not title or not handle or not image:
            continue

        link = f"{PUBLIC_DOMAIN}/products/{handle}"

        for v in p["variants"]["edges"]:
            node = v["node"]

            try:
                price = float(node["price"])
            except (TypeError, ValueError):
                continue

            if price <= 0:
                continue

            sku = clean(node["sku"]) or clean(node["id"])
            qty = node.get("inventoryQuantity")
            ean = clean(node.get("barcode"))

            if not should_include_product(qty):
                continue

            availability = get_availability_label(qty)

            line = "|".join([
                title,
                brand,
                description,
                f"{price:.2f}",
                sku,
                link,
                availability,
                category,
                image,
                "0",
                ean
            ]) + " <endrecord>"

            lines.append(line)
            added += 1

            if MAX_OUTPUT_ROWS is not None and added >= MAX_OUTPUT_ROWS:
                return "\n".join(lines)

    return "\n".join(lines)


# =========================
# MAIN
# =========================
if __name__ == "__main__":
    token = get_token()
    products = get_products(token, first=50)

    feed = build_feed(products)

    with open("trovaprezzi-feed.txt", "w", encoding="utf-8") as f:
        f.write(feed)

    print("Feed aggiornato correttamente!")