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
    raise RuntimeError("Variabili .env mancanti.")

# =========================
# CONFIG
# =========================
MAX_FETCH_PRODUCTS = 300        # 🚨 limite sicurezza
MAX_OUTPUT_ROWS = 1000         # 🚨 limite feed
REQUEST_TIMEOUT = 15           # 🚨 evita freeze

PRIORITY_COLLECTIONS = {
    "fotocamere-mirrorless": "Fotografia;Fotocamere Digitali;Mirrorless",
    "mirrorless": "Fotografia;Fotocamere Digitali;Mirrorless",
    "obiettivi-mirrorless": "Fotografia;Obiettivi Fotografici;Obiettivi Mirrorless",
}

BLACKLIST = {"home", "frontpage", "promo"}

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


def parse_price(value):
    if value is None:
        return None

    value = str(value).strip().replace(" ", "")

    if "," in value and "." in value:
        if value.find(",") > value.find("."):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
    else:
        value = value.replace(",", ".")

    try:
        return float(value)
    except:
        return None


def extract_lead_time(value):
    value = clean(value)
    match = re.search(r"\d+", value)
    return int(match.group(0)) if match else None


def get_product_lead_time(product):
    metafield = product.get("metafield")
    if not metafield:
        return None
    return extract_lead_time(metafield.get("value"))


def get_availability(qty):
    if qty is None:
        qty = 0

    if qty > 1:
        return "disponibile"
    if qty == 1:
        return "limitata"
    return "non disponibile"


# =========================
# SHOPIFY QUERY
# =========================
def build_shopify_query(filters=None):
    parts = ["status:ACTIVE"]

    if filters:
        brands = filters.get("brand", [])
        if isinstance(brands, str):
            brands = [brands]

        if brands:
            q = [f'vendor:"{clean(b)}"' for b in brands if clean(b)]
            if q:
                parts.append("(" + " OR ".join(q) + ")")

    return " ".join(parts)


# =========================
# AUTH
# =========================
def get_token():
    url = f"https://{SHOP_DOMAIN}/admin/oauth/access_token"

    r = requests.post(url, json={
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "client_credentials",
    }, timeout=REQUEST_TIMEOUT)

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
        timeout=REQUEST_TIMEOUT
    )

    r.raise_for_status()
    data = r.json()

    if "errors" in data:
        raise Exception(data["errors"])

    return data["data"]


# =========================
# FETCH LIMITATO
# =========================
def get_products(token, first=100, search_query=None):
    query = """
    query ($first: Int!, $after: String, $query: String) {
      products(first: $first, after: $after, query: $query) {
        pageInfo { hasNextPage endCursor }
        edges {
          node {
            title
            handle
            status
            vendor
            description
            featuredImage { url }
            metafield(namespace: "custom", key: "lead_time_consegna") { value }
            collections(first: 10) {
              edges { node { handle title } }
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

    all_edges = []
    after = None

    while True:
        data = graphql(token, query, {
            "first": first,
            "after": after,
            "query": search_query
        })["products"]

        all_edges.extend(data["edges"])

        print(f"[DEBUG] prodotti caricati: {len(all_edges)}")

        if len(all_edges) >= MAX_FETCH_PRODUCTS:
            print("[STOP] raggiunto limite sicurezza")
            break

        if not data["pageInfo"]["hasNextPage"]:
            break

        after = data["pageInfo"]["endCursor"]

    return all_edges


# =========================
# FEED
# =========================
def build_feed(products, filters=None):
    lines = []
    lines.append("Nome|Marca|Prezzo|Disponibilità|Link <endrecord>")

    count = 0

    for edge in products:
        p = edge["node"]

        for v in p["variants"]["edges"]:
            node = v["node"]

            if not matches_filters(p, node, filters or {}):
                continue

            price = parse_price(node["price"])
            if not price or price <= 0:
                continue

            qty = node.get("inventoryQuantity")
            availability = get_availability(qty)

            line = "|".join([
                clean(p["title"]),
                clean(p["vendor"]),
                f"{price:.2f}",
                availability,
                f"{PUBLIC_DOMAIN}/products/{p['handle']}"
            ]) + " <endrecord>"

            lines.append(line)
            count += 1

            if count >= MAX_OUTPUT_ROWS:
                print("[STOP] limite righe feed")
                return "\n".join(lines)

    return "\n".join(lines)