"""
Fake e-commerce activity generator.
Simulates a live online shop writing to the source Postgres database,
so Debezium has real inserts, updates and deletes to capture.
"""
import argparse
import os
import random
import time
from decimal import Decimal

import psycopg
from dotenv import load_dotenv
from faker import Faker

load_dotenv()
fake = Faker("fi_FI")

CITIES = ["Helsinki", "Espoo", "Vantaa", "Tampere", "Turku",
          "Oulu", "Jyväskylä", "Lahti", "Kuopio", "Pori"]

CATEGORIES = {
    "Electronics": ["Headphones", "Charger", "Smartwatch", "Speaker", "Keyboard"],
    "Home": ["Lamp", "Blanket", "Mug", "Cushion", "Candle"],
    "Sports": ["Yoga Mat", "Water Bottle", "Running Socks", "Dumbbell", "Bike Light"],
    "Books": ["Novel", "Cookbook", "Travel Guide", "Notebook", "Comic"],
}
ADJECTIVES = ["Classic", "Nordic", "Eco", "Pro", "Mini", "Deluxe", "Smart"]

# Allowed status transitions, like a real order lifecycle
STATUS_FLOW = {"placed": "paid", "paid": "shipped", "shipped": "delivered"}


def connect():
    # autocommit=True + explicit transactions = one clear commit per action
    return psycopg.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5433"),
        dbname=os.getenv("POSTGRES_DB", "shop"),
        user=os.getenv("POSTGRES_USER", "shop"),
        password=os.getenv("POSTGRES_PASSWORD", "shop_pw"),
        autocommit=True,
    )


# ---------- Actions: each one is a realistic thing a shop does ----------

def create_customer(cur):
    first, last = fake.first_name(), fake.last_name()
    email = f"{first}.{last}.{random.randint(1, 99999)}@example.com".lower()
    cur.execute(
        """INSERT INTO customers (first_name, last_name, email, phone, city, country)
           VALUES (%s, %s, %s, %s, %s, 'Finland') RETURNING customer_id""",
        (first, last, email, fake.phone_number(), random.choice(CITIES)),
    )
    return f"new customer {cur.fetchone()[0]}"


def create_product(cur):
    category = random.choice(list(CATEGORIES))
    name = f"{random.choice(ADJECTIVES)} {random.choice(CATEGORIES[category])}"
    price = Decimal(random.randint(500, 20000)) / 100  # 5.00 to 200.00 EUR
    cur.execute(
        "INSERT INTO products (name, category, price) VALUES (%s, %s, %s) RETURNING product_id",
        (name, category, price),
    )
    return f"new product {cur.fetchone()[0]}"


def create_order(cur):
    cur.execute("SELECT customer_id FROM customers ORDER BY random() LIMIT 1")
    row = cur.fetchone()
    if not row:
        return None
    cur.execute(
        "INSERT INTO orders (customer_id, status) VALUES (%s, 'placed') RETURNING order_id",
        row,
    )
    order_id = cur.fetchone()[0]
    cur.execute(
        "SELECT product_id, price FROM products ORDER BY random() LIMIT %s",
        (random.randint(1, 4),),
    )
    items = cur.fetchall()
    for product_id, price in items:
        # unit_price is copied at purchase time, so later price changes
        # don't rewrite old orders
        cur.execute(
            """INSERT INTO order_items (order_id, product_id, quantity, unit_price)
               VALUES (%s, %s, %s, %s)""",
            (order_id, product_id, random.randint(1, 3), price),
        )
    return f"new order {order_id} ({len(items)} items)"


def advance_order(cur):
    cur.execute(
        """SELECT order_id, status FROM orders
           WHERE status IN ('placed', 'paid', 'shipped')
           ORDER BY random() LIMIT 1"""
    )
    row = cur.fetchone()
    if not row:
        return None
    order_id, status = row
    new_status = STATUS_FLOW[status]
    cur.execute(
        "UPDATE orders SET status = %s, updated_at = now() WHERE order_id = %s",
        (new_status, order_id),
    )
    return f"order {order_id}: {status} -> {new_status}"


def cancel_order(cur):
    cur.execute(
        """UPDATE orders SET status = 'cancelled', updated_at = now()
           WHERE order_id = (
               SELECT order_id FROM orders WHERE status IN ('placed', 'paid')
               ORDER BY random() LIMIT 1)
           RETURNING order_id"""
    )
    row = cur.fetchone()
    return f"order {row[0]} cancelled" if row else None


def customer_moves(cur):
    # This creates SCD Type 2 history: same customer, new city
    cur.execute("SELECT customer_id, city FROM customers ORDER BY random() LIMIT 1")
    row = cur.fetchone()
    if not row:
        return None
    customer_id, old_city = row
    new_city = random.choice([c for c in CITIES if c != old_city])
    cur.execute(
        "UPDATE customers SET city = %s, updated_at = now() WHERE customer_id = %s",
        (new_city, customer_id),
    )
    return f"customer {customer_id} moved {old_city} -> {new_city}"


def price_change(cur):
    # Another SCD Type 2 source: product price history
    cur.execute("SELECT product_id, price FROM products ORDER BY random() LIMIT 1")
    row = cur.fetchone()
    if not row:
        return None
    product_id, old_price = row
    factor = Decimal(str(round(random.uniform(0.85, 1.20), 2)))
    new_price = (old_price * factor).quantize(Decimal("0.01"))
    cur.execute(
        "UPDATE products SET price = %s, updated_at = now() WHERE product_id = %s",
        (new_price, product_id),
    )
    return f"product {product_id} price {old_price} -> {new_price}"


def delete_inactive_customer(cur):
    # A GDPR-style account deletion; only customers with no orders,
    # so foreign keys stay valid
    cur.execute(
        """DELETE FROM customers WHERE customer_id = (
               SELECT c.customer_id FROM customers c
               WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.customer_id)
               ORDER BY random() LIMIT 1)
           RETURNING customer_id"""
    )
    row = cur.fetchone()
    return f"customer {row[0]} deleted" if row else None


# How often each action happens (weights, not percentages)
ACTIONS = [
    (create_order, 35),
    (advance_order, 30),
    (create_customer, 10),
    (customer_moves, 8),
    (price_change, 7),
    (cancel_order, 4),
    (create_product, 3),
    (delete_inactive_customer, 3),
]
FUNCS = [a for a, _ in ACTIONS]
WEIGHTS = [w for _, w in ACTIONS]


def seed(conn, n_customers=50, n_products=30):
    """Give the shop a starting catalogue and customer base, once."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM products")
        if cur.fetchone()[0] > 0:
            print("Data already exists, skipping seed.")
            return
        for _ in range(n_products):
            create_product(cur)
        for _ in range(n_customers):
            create_customer(cur)
    print(f"Seeded {n_products} products and {n_customers} customers.")


def main():
    parser = argparse.ArgumentParser(description="Fake e-commerce activity generator")
    parser.add_argument("--rate", type=float, default=2.0, help="actions per second")
    parser.add_argument("--max-actions", type=int, default=0, help="stop after N actions (0 = forever)")
    parser.add_argument("--seed-only", action="store_true", help="only load starting data")
    args = parser.parse_args()

    with connect() as conn:
        seed(conn)
        if args.seed_only:
            return
        count = 0
        try:
            while args.max_actions == 0 or count < args.max_actions:
                action = random.choices(FUNCS, weights=WEIGHTS)[0]
                with conn.transaction(), conn.cursor() as cur:
                    result = action(cur)
                if result:
                    count += 1
                    print(f"[{count}] {result}")
                time.sleep(1 / args.rate)
        except KeyboardInterrupt:
            print(f"\nStopped after {count} actions.")


if __name__ == "__main__":
    main()