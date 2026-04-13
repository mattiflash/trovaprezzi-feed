from pathlib import Path
from flask import Flask, abort, request
from markupsafe import escape
import time
import re
from datetime import datetime

from feed_exporter import get_token, get_products, build_shopify_query, build_feed
from config_schema import DEFAULT_FILTERS

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
LAST_FEED_FILE = BASE_DIR / "latest-feed.txt"

# Cache semplice in memoria per evitare di rileggere Shopify a ogni refresh
FILTER_CACHE = {
    "timestamp": 0,
    "brands": [],
    "collections": []
}
FILTER_CACHE_TTL = 600  # 10 minuti


def is_valid_price_input(value):
    if not value:
        return True

    value = value.strip()

    # accetta:
    # 99,99
    # 99.99
    # 1.299,99
    # 1299
    pattern = r"^\d{1,3}([.,]?\d{3})*([.,]\d{1,2})?$"
    return re.match(pattern, value) is not None


def get_filter_options():
    now = time.time()

    if (
        FILTER_CACHE["brands"]
        and FILTER_CACHE["collections"]
        and (now - FILTER_CACHE["timestamp"] < FILTER_CACHE_TTL)
    ):
        return FILTER_CACHE["brands"], FILTER_CACHE["collections"]

    token = get_token()
    products = get_products(token=token, first=100, search_query="status:ACTIVE")

    brands = set()
    collections = {}

    for edge in products:
        p = edge["node"]

        vendor = (p.get("vendor") or "").strip()
        if vendor:
            brands.add(vendor)

        for c in p.get("collections", {}).get("edges", []):
            node = c.get("node", {})
            title = (node.get("title") or "").strip()
            handle = (node.get("handle") or "").strip()

            if title and handle:
                collections[title] = handle

    sorted_brands = sorted(brands, key=lambda x: x.lower())
    sorted_collections = sorted(collections.items(), key=lambda x: x[0].lower())

    FILTER_CACHE["timestamp"] = now
    FILTER_CACHE["brands"] = sorted_brands
    FILTER_CACHE["collections"] = sorted_collections

    return sorted_brands, sorted_collections


@app.route("/", methods=["GET"])
def admin_home():
    brands, collections = get_filter_options()

    brand_checkboxes = "".join(
        f"""
        <div>
            <label>
                <input type="checkbox" name="brand" value="{escape(brand)}">
                {escape(brand)}
            </label>
        </div>
        """
        for brand in brands
    )

    collection_checkboxes = "".join(
        f"""
        <div>
            <label>
                <input type="checkbox" name="collection" value="{escape(title)}">
                {escape(title)} ({escape(handle)})
            </label>
        </div>
        """
        for title, handle in collections
    )

    latest_link_html = ""
    if LAST_FEED_FILE.exists():
        latest_link_html = '<p><a href="/feed/latest-feed.txt" target="_blank">Apri ultimo feed pubblico</a></p>'

    return f"""
    <h1>ADMIN APP</h1>

    <form action="/admin/genera-feed" method="get">
        <div>
            <label>
                <input type="checkbox" name="use_brand" value="1">
                Usa brand
            </label>
            <br>
            <div style="max-height:220px; overflow:auto; border:1px solid #ccc; padding:10px; margin-top:8px;">
                {brand_checkboxes}
            </div>
        </div>

        <br>

        <div>
            <label>
                <input type="checkbox" name="use_collection" value="1">
                Usa collezione
            </label>
            <br>
            <div style="max-height:220px; overflow:auto; border:1px solid #ccc; padding:10px; margin-top:8px;">
                {collection_checkboxes}
            </div>
        </div>

        <br>

        <div>
            <label>
                <input type="checkbox" name="use_availability" value="1">
                Usa disponibilità
            </label>
            <br>
            <select name="availability">
                <option value="">-- Seleziona disponibilità --</option>
                <option value="non disponibile">non disponibile (quantità pari a 0)</option>
                <option value="limitata">limitata (quantità pari a 1)</option>
                <option value="disponibile">disponibile (quantità superiore a 1)</option>
            </select>
        </div>

        <br>

        <div>
            <label>
                <input type="checkbox" name="use_lead_time" value="1">
                Usa lead time consegna
            </label>
            <br><br>

            <label for="lead_time_min">Lead time minimo:</label>
            <input type="number" name="lead_time_min" min="0" step="1" placeholder="es. 21">

            <br><br>

            <label for="lead_time_max">Lead time massimo:</label>
            <input type="number" name="lead_time_max" min="0" step="1" placeholder="es. 999">
        </div>

        <br>

        <div>
            <label>
                <input type="checkbox" name="use_price_ranges" value="1">
                Usa range di prezzo
            </label>

            <div id="price-ranges" style="margin-top:10px;">
                <div class="price-range-row" style="margin-bottom:10px;">
                    <label>Prezzo da:</label>
                    <input type="text" name="price_min" placeholder="es. 99,99">

                    <label style="margin-left:10px;">a:</label>
                    <input type="text" name="price_max" placeholder="es. 200,00">
                </div>
            </div>

            <button type="button" onclick="addPriceRange()">Aggiungi un altro range di prezzo</button>
        </div>

        <br>

        <script>
        function addPriceRange() {{
            const container = document.getElementById('price-ranges');
            const row = document.createElement('div');
            row.className = 'price-range-row';
            row.style.marginBottom = '10px';
            row.innerHTML = `
                <label>Prezzo da:</label>
                <input type="text" name="price_min" placeholder="es. 99,99">

                <label style="margin-left:10px;">a:</label>
                <input type="text" name="price_max" placeholder="es. 200,00">

                <button type="button" onclick="this.parentElement.remove()" style="margin-left:10px;">Rimuovi</button>
            `;
            container.appendChild(row);
        }}
        </script>

        <br>

        <button type="submit">Genera feed</button>
    </form>

    {latest_link_html}
    """


@app.route("/admin/verifica-collezioni", methods=["GET"])
def verify_collections():
    brand = (request.args.get("brand") or "").strip()

    filters = {}
    if brand:
        filters["brand"] = [brand]

    search_query = build_shopify_query(filters)
    token = get_token()
    products = get_products(token=token, first=100, search_query=search_query)

    html = ["<h1>Verifica collezioni prodotto</h1>"]
    html.append("""
    <form method="get">
        <label for="brand">Brand:</label>
        <input type="text" id="brand" name="brand" placeholder="Es. Nikon, Canon, Godox">
        <button type="submit">Cerca</button>
    </form>
    <br>
    """)

    if not products:
        html.append("<p>Nessun prodotto trovato.</p>")
    else:
        for edge in products[:50]:
            p = edge["node"]
            title = escape(p.get("title", ""))
            vendor = escape(p.get("vendor", ""))

            collections = []
            for c in p.get("collections", {}).get("edges", []):
                node = c.get("node", {})
                coll_title = node.get("title", "")
                coll_handle = node.get("handle", "")
                collections.append(
                    f"{escape(coll_title)} <small>({escape(coll_handle)})</small>"
                )

            html.append(f"<hr><h3>{title}</h3>")
            html.append(f"<p><strong>Brand:</strong> {vendor}</p>")

            if collections:
                html.append("<p><strong>Collezioni:</strong><br>" + "<br>".join(collections) + "</p>")
            else:
                html.append("<p><strong>Collezioni:</strong> Nessuna</p>")

    html.append('<p><a href="/">Torna indietro</a></p>')
    return "".join(html)


@app.route("/admin/genera-feed", methods=["GET"])
def generate_feed():
    filters = DEFAULT_FILTERS.copy()

    selected_brands = request.args.getlist("brand")
    selected_brands = [b.strip() for b in selected_brands if b.strip()]

    selected_collections = request.args.getlist("collection")
    selected_collections = [c.strip() for c in selected_collections if c.strip()]

    price_mins = request.args.getlist("price_min")
    price_maxs = request.args.getlist("price_max")

    price_mins = [p.strip() for p in price_mins]
    price_maxs = [p.strip() for p in price_maxs]

    for i in range(max(len(price_mins), len(price_maxs))):
        min_val = price_mins[i] if i < len(price_mins) else ""
        max_val = price_maxs[i] if i < len(price_maxs) else ""

        if not is_valid_price_input(min_val) or not is_valid_price_input(max_val):
            return """
            <h2>Errore input prezzo</h2>
            <p>Hai inserito un formato prezzo non valido.</p>
            <p>Formato corretto:</p>
            <ul>
                <li>99,99</li>
                <li>99.99</li>
                <li>1.299,99</li>
                <li>1299</li>
            </ul>
            <p><a href="/">Torna indietro</a></p>
            """

    filters["use_brand"] = request.args.get("use_brand") == "1"
    filters["brand"] = selected_brands

    filters["use_collection"] = request.args.get("use_collection") == "1"
    filters["collection"] = selected_collections

    filters["use_availability"] = request.args.get("use_availability") == "1"
    filters["availability"] = (request.args.get("availability") or "").strip()

    filters["use_lead_time"] = request.args.get("use_lead_time") == "1"
    filters["lead_time_min"] = (request.args.get("lead_time_min") or "").strip()
    filters["lead_time_max"] = (request.args.get("lead_time_max") or "").strip()

    filters["use_price_ranges"] = request.args.get("use_price_ranges") == "1"
    filters["price_min"] = price_mins
    filters["price_max"] = price_maxs

    active_filters = {}

    if filters["use_brand"] and filters["brand"]:
        active_filters["brand"] = filters["brand"]

    if filters["use_collection"] and filters["collection"]:
        active_filters["collection"] = filters["collection"]

    if filters["use_availability"] and filters["availability"]:
        active_filters["availability"] = filters["availability"]

    if filters["use_lead_time"]:
        active_filters["use_lead_time"] = True
        active_filters["lead_time_min"] = filters["lead_time_min"]
        active_filters["lead_time_max"] = filters["lead_time_max"]

    if filters["use_price_ranges"]:
        active_filters["use_price_ranges"] = True
        active_filters["price_min"] = filters["price_min"]
        active_filters["price_max"] = filters["price_max"]

    search_query = build_shopify_query(active_filters)
    token = get_token()
    products = get_products(token=token, first=100, search_query=search_query)
    feed = build_feed(products, filters=active_filters)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"trovaprezzi-feed-{timestamp}.txt"
    file_path = BASE_DIR / filename

    file_path.write_text(feed, encoding="utf-8")
    LAST_FEED_FILE.write_text(feed, encoding="utf-8")

    brand_text = ", ".join(filters["brand"]) if filters["use_brand"] and filters["brand"] else "NO"
    collection_text = ", ".join(filters["collection"]) if filters["use_collection"] and filters["collection"] else "NO"
    availability_text = filters["availability"] if filters["use_availability"] and filters["availability"] else "NO"
    lead_time_min_text = filters["lead_time_min"] if filters["use_lead_time"] and filters["lead_time_min"] else "NO"
    lead_time_max_text = filters["lead_time_max"] if filters["use_lead_time"] and filters["lead_time_max"] else "NO"

    price_ranges_text = "NO"
    if filters["use_price_ranges"]:
        pairs = []
        max_len = max(len(filters["price_min"]), len(filters["price_max"]))

        for i in range(max_len):
            min_val = filters["price_min"][i] if i < len(filters["price_min"]) else ""
            max_val = filters["price_max"][i] if i < len(filters["price_max"]) else ""

            min_val = min_val.strip()
            max_val = max_val.strip()

            if min_val == "" and max_val == "":
                continue

            pairs.append(f"{min_val or '*'} - {max_val or '*'}")

        if pairs:
            price_ranges_text = ", ".join(pairs)

    return f"""
    <h2>Feed generato correttamente</h2>
    <p>Brand usati: {escape(brand_text)}</p>
    <p>Collezioni usate: {escape(collection_text)}</p>
    <p>Disponibilità usata: {escape(availability_text)}</p>
    <p>Lead time minimo usato: {escape(lead_time_min_text)}</p>
    <p>Lead time massimo usato: {escape(lead_time_max_text)}</p>
    <p>Range prezzo usati: {escape(price_ranges_text)}</p>
    <p><a href="/feed/{filename}" target="_blank">Apri feed pubblico di questa generazione</a></p>
    <p><a href="/feed/latest-feed.txt" target="_blank">Apri ultimo feed pubblico</a></p>
    <p><a href="/">Torna indietro</a></p>
    """


@app.route("/feed/<filename>")
def public_feed(filename):
    file_path = BASE_DIR / filename

    if not file_path.exists():
        abort(404, description="Feed non trovato")

    content = file_path.read_text(encoding="utf-8")
    return content, 200, {"Content-Type": "text/plain; charset=utf-8"}


if __name__ == "__main__":
    app.run(debug=True)