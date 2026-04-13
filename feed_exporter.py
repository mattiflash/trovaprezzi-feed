import os
import re
import requests
from dotenv import load_dotenv
from pathlib import Path
from filters import matches_filters

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

MAX_OUTPUT_ROWS = None

# =========================
# UTILS
# =========================
def clean(value):
    if value is None:
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


def get_product_lead_time(product):
    metafield = product.get("metafield")
    if not metafield:
        return None

    raw_value = metafield.get("value")
    return extract_lead_time(raw_value)

def get_availability(qty, lead_time=None):
    if qty is None:
        return ""
    if qty > 1:
        return "disponibile"
    if qty == 1:
        return "limitata"
    return "non disponibile"

    # qty <= 0
    if lead_time is not None:
        return "in arrivo"

    return "non disponibile"


def normalize_collection_output(filters):
    """
    Decide cosa scrivere in output in 'Albero Categorie'.
    Per ora, se l'utente ha selezionato una o più collezioni, usiamo:
    - una sola collezione -> quella
    - più collezioni -> le uniamo con virgola
    Altrimenti usiamo la mappatura standard.
    """
    if not filters:
        return ""

    collections = filters.get("collection", [])

    if isinstance(collections, str):
        collections = [collections]

    collections = [clean(c) for c in collections if clean(c)]

    if not collections:
        return ""

    if len(collections) == 1:
        return collections[0]

    return ", ".join(collections)


def build_shopify_query(filters=None):
    """
    Costruisce la query testuale Shopify per cercare in tutto lo shop.
    Al momento:
    - status ACTIVE sempre
    - brand (vendor) con supporto multi-brand
    - collezione filtrata localmente dopo il fetch
    """
    parts = ["status:ACTIVE"]

    if not filters:
        return " ".join(parts)

    brands = filters.get("brand", [])
    if isinstance(brands, str):
        brands = [brands]

    if brands:
        brand_queries = []
        for b in brands:
            b = clean(b)
            if b:
                brand_queries.append(f'vendor:"{b}"')

        if brand_queries:
            if len(brand_queries) == 1:
                parts.append(brand_queries[0])
            else:
                parts.append("(" + " OR ".join(brand_queries) + ")")

    return " ".join(parts)

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


def get_products(token, first=100, search_query=None):
    query = """
    query ($first: Int!, $after: String, $query: String) {
      products(first: $first, after: $after, query: $query) {
        pageInfo {
          hasNextPage
          endCursor
        }
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
            collections(first: 20) {
              edges {
                node {
                  handle
                  title
                }
              }
            }
            variants(first: 50) {
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

    all_edges = []
    after = None

    while True:
        data = graphql(token, query, {
            "first": first,
            "after": after,
            "query": search_query
        })["products"]

        all_edges.extend(data["edges"])

        if not data["pageInfo"]["hasNextPage"]:
            break

        after = data["pageInfo"]["endCursor"]

    return all_edges

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

# =========================
# FEED
# =========================
def build_feed(products, filters=None):
    if filters is None:
        filters = {}

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

        forced_category = normalize_collection_output(filters)

        if forced_category:
            category = forced_category
        else:
            category = pick_category(p["collections"]["edges"]) or ""

        title = clean(p["title"])
        brand = clean(p["vendor"])
        description = clean_description(p.get("description", ""), p.get("title", ""))
        handle = clean(p["handle"])
        image = clean((p.get("featuredImage") or {}).get("url"))
        lead_time = get_product_lead_time(p)

        if not title or not handle or not image:
            continue

        link = f"{PUBLIC_DOMAIN}/products/{handle}"

        for v in p["variants"]["edges"]:
            node = v["node"]

            if not matches_filters(p, node, filters):
                continue

            try:
                price = float(node["price"])
            except (TypeError, ValueError):
                continue

            if price <= 0:
                continue

            sku = clean(node["sku"]) or clean(node["id"])
            qty = node.get("inventoryQuantity")
            availability = get_availability(qty, lead_time)
            ean = clean(node.get("barcode"))

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

    user_brand = input("Inserisci il brand da cercare: ").strip()

    filters = {}
    if user_brand:
        filters["brand"] = [user_brand]

    search_query = build_shopify_query(filters)

    print(f"Query Shopify: {search_query}")

    products = get_products(
        token=token,
        first=100,
        search_query=search_query
    )

    print(f"Prodotti trovati da Shopify: {len(products)}")

    feed = build_feed(products, filters=filters)

    with open("trovaprezzi-feed.txt", "w", encoding="utf-8") as f:
        f.write(feed)

    print("Feed aggiornato correttamente!")