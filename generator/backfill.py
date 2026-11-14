"""
One-off historical backfill for the fake shop.

Writes ~12 months of realistic history into the SOURCE database, so it flows
through the normal CDC path (Debezium -> Kafka -> loader -> dbt) just like
live data. Business timestamps (created_at, order_ts, updated_at) are set to
historical times; dbt uses them as "effective time" for SCD2 and lifecycle facts.

Realism built in:
  - growth trend (~4% per month), weekly pattern, evening peak hours
  - seasonality: January dip, summer dip, Black Friday spike, December rush
  - customers with different loyalty and lifetimes -> real retention curves
  - long-tail product popularity (a few products drive most revenue)
  - price changes with demand reacting to price (price elasticity)
  - Black Friday discounts that are reverted afterwards
  - payment, shipping and delivery delays that depend on weekday and city
  - cancellations before and after payment
  - customers moving city (SCD Type 2 history)

Usage (from the project root, venv active, Docker stack running):
    python generator/backfill.py               # default: 365 days, ~20 orders/day at start
    python generator/backfill.py --days 180 --base-orders 15
"""
import argparse
import math
import os
import random
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import psycopg
from dotenv import load_dotenv
from faker import Faker

load_dotenv()
fake = Faker("fi_FI")
HELSINKI = ZoneInfo("Europe/Helsinki")

# ---------------------------------------------------------------- settings

CITY_WEIGHTS = {
    "Helsinki": 30, "Espoo": 15, "Vantaa": 12, "Tampere": 12, "Turku": 10,
    "Oulu": 7, "Jyväskylä": 5, "Lahti": 4, "Kuopio": 3, "Pori": 2,
}
# typical days in transit from the warehouse (in the Helsinki region)
TRANSIT_DAYS = {
    "Helsinki": 1.0, "Espoo": 1.0, "Vantaa": 1.0, "Lahti": 1.5, "Tampere": 1.8,
    "Turku": 1.8, "Pori": 2.3, "Jyväskylä": 2.3, "Kuopio": 2.8, "Oulu": 3.3,
}
CATEGORIES = {
    #              items                                                       price range
    "Electronics": (["Headphones", "Charger", "Smartwatch", "Speaker", "Keyboard",
                     "Mouse", "Webcam", "Power Bank"], (25, 250)),
    "Home":        (["Lamp", "Blanket", "Mug", "Cushion", "Candle", "Vase",
                     "Throw Rug", "Coffee Grinder"], (9, 90)),
    "Sports":      (["Yoga Mat", "Water Bottle", "Running Socks", "Dumbbell",
                     "Bike Light", "Ski Gloves", "Foam Roller", "Backpack"], (9, 130)),
    "Books":       (["Novel", "Cookbook", "Travel Guide", "Notebook", "Comic",
                     "Biography", "Puzzle Book", "Atlas"], (8, 45)),
}
ADJECTIVES = ["Classic", "Nordic", "Eco", "Pro", "Mini", "Deluxe", "Smart", "Arctic"]

# order volume by hour of day (Helsinki time): quiet nights, lunch bump, evening peak
HOUR_WEIGHTS = [1, 0.6, 0.4, 0.3, 0.3, 0.5, 1, 2, 3, 3.5, 4, 4.5,
                5, 4.5, 4, 4, 4.5, 5, 6, 7, 7.5, 6.5, 4, 2]
# Monday ... Sunday
WEEKDAY_FACTOR = [0.9, 0.95, 1.0, 1.0, 1.1, 1.15, 1.2]

PRICE_ELASTICITY = 1.8      # demand ~ (base_price / price) ** elasticity
RETURNING_SHARE = 0.55      # share of orders placed by existing customers (if any are active)
ONE_TIME_BUYER_SHARE = 0.4  # customers who never come back
MOVE_RATE_PER_YEAR = 0.06   # share of customers moving city per year


def connect():
    return psycopg.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5433"),
        dbname=os.getenv("POSTGRES_DB", "shop"),
        user=os.getenv("POSTGRES_USER", "shop"),
        password=os.getenv("POSTGRES_PASSWORD", "shop_pw"),
    )


def black_friday(year: int) -> date:
    """Black Friday = the day after the 4th Thursday of November."""
    nov1 = date(year, 11, 1)
    first_thursday = nov1 + timedelta(days=(3 - nov1.weekday()) % 7)
    return first_thursday + timedelta(days=22)


def seasonality(d: date) -> float:
    bf = black_friday(d.year)
    if d == bf:
        return 3.0
    if bf - timedelta(days=3) <= d <= bf + timedelta(days=3):   # BF week + Cyber Monday
        return 1.7
    if d.month == 12 and d.day <= 21:
        return 1.6
    if d.month == 12 and 24 <= d.day <= 26:
        return 0.5
    if d.month == 1:
        return 0.8
    if d.month == 7:
        return 0.75
    return 1.0


def to_utc(local: datetime) -> datetime:
    return local.replace(tzinfo=HELSINKI).astimezone(timezone.utc)


def weighted_choice(items, weights):
    return random.choices(items, weights=weights, k=1)[0]


def business_delay(start: datetime, hours: float) -> datetime:
    """Add working hours roughly: weekends push shipping to Monday."""
    t = start + timedelta(hours=hours)
    local = t.astimezone(HELSINKI)
    if local.weekday() == 5:
        t += timedelta(days=2)
    elif local.weekday() == 6:
        t += timedelta(days=1)
    return t


# ---------------------------------------------------------------- main


def main():
    parser = argparse.ArgumentParser(description="Backfill historical shop data")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--base-orders", type=float, default=20,
                        help="average orders per day at the start of the period")
    parser.add_argument("--monthly-growth", type=float, default=0.04)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--force", action="store_true",
                        help="run even if historical orders already exist")
    args = parser.parse_args()

    random.seed(args.seed)
    Faker.seed(args.seed)

    now = datetime.now(timezone.utc)
    today_local = now.astimezone(HELSINKI).date()
    start_day = today_local - timedelta(days=args.days)

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM orders WHERE order_ts < now() - interval '30 days'")
            if cur.fetchone()[0] > 0 and not args.force:
                print("Historical orders already exist. Backfill skipped (use --force to run again).")
                return

        # ------------------------------------------------ product catalogue
        catalogue_created = to_utc(datetime.combine(start_day - timedelta(days=1), datetime.min.time())
                                   + timedelta(hours=9))
        products = []   # dicts: id, base_price, price, popularity
        with conn.transaction(), conn.cursor() as cur:
            rank = 1
            for category, (items, (lo, hi)) in CATEGORIES.items():
                for item in items:
                    for _ in range(2):
                        name = f"{random.choice(ADJECTIVES)} {item}"
                        price = Decimal(str(round(random.uniform(lo, hi), 2)))
                        cur.execute(
                            """INSERT INTO products (name, category, price, created_at, updated_at)
                               VALUES (%s, %s, %s, %s, %s) RETURNING product_id""",
                            (name, category, price, catalogue_created, catalogue_created),
                        )
                        products.append({"id": cur.fetchone()[0], "base_price": price,
                                         "price": price, "category": category})
            # long-tail popularity: shuffle, then Zipf-like weights by rank
            random.shuffle(products)
            for rank, p in enumerate(products, start=1):
                p["popularity"] = 1 / (rank ** 1.1)
        print(f"Created {len(products)} products.")

        customers = []          # dicts: id, created, lifetime_end, loyalty, city
        bf_discounted = []      # products discounted for Black Friday
        stats = {"orders": 0, "items": 0, "customers": 0, "moves": 0,
                 "price_changes": 0, "cancelled": 0}

        day = start_day
        while day < today_local:
            months_elapsed = (day - start_day).days / 30.4
            expected = (args.base_orders
                        * (1 + args.monthly_growth) ** months_elapsed
                        * WEEKDAY_FACTOR[day.weekday()]
                        * seasonality(day))
            n_orders = max(0, round(random.gauss(expected, math.sqrt(expected))))

            with conn.transaction(), conn.cursor() as cur:
                day_events = []   # (utc_time, kind, payload) processed in time order

                # ---- orders for the day
                for _ in range(n_orders):
                    hour = weighted_choice(range(24), HOUR_WEIGHTS)
                    local_ts = datetime.combine(day, datetime.min.time()) + timedelta(
                        hours=hour, minutes=random.randint(0, 59), seconds=random.randint(0, 59))
                    day_events.append((to_utc(local_ts), "order", None))

                # ---- price changes (a few per week) and Black Friday discounts
                for _ in range(sum(1 for _ in range(len(products)) if random.random() < 0.004)):
                    t = to_utc(datetime.combine(day, datetime.min.time()) + timedelta(hours=6))
                    day_events.append((t, "price_change", random.choice(products)))
                bf = black_friday(day.year)
                if day == bf - timedelta(days=3):
                    t = to_utc(datetime.combine(day, datetime.min.time()) + timedelta(hours=5))
                    day_events.append((t, "bf_start", None))
                if day == bf + timedelta(days=4):   # Tuesday after Cyber Monday
                    t = to_utc(datetime.combine(day, datetime.min.time()) + timedelta(hours=5))
                    day_events.append((t, "bf_end", None))

                # ---- customers moving city
                active_now = len(customers)
                n_moves = sum(1 for _ in range(active_now) if random.random() < MOVE_RATE_PER_YEAR / 365)
                for _ in range(n_moves):
                    local_ts = datetime.combine(day, datetime.min.time()) + timedelta(
                        hours=random.randint(8, 21), minutes=random.randint(0, 59))
                    day_events.append((to_utc(local_ts), "move", random.choice(customers)))

                day_events.sort(key=lambda e: e[0])

                for t, kind, payload in day_events:
                    if kind == "order":
                        create_order(cur, t, day, customers, products, now, stats)
                    elif kind == "price_change":
                        change_price(cur, payload, t, stats)
                    elif kind == "bf_start":
                        bf_discounted = random.sample(products, k=min(12, len(products)))
                        for p in bf_discounted:
                            set_price(cur, p, (p["price"] * Decimal("0.75")).quantize(Decimal("0.01")), t)
                            stats["price_changes"] += 1
                    elif kind == "bf_end":
                        for p in bf_discounted:
                            set_price(cur, p, (p["price"] / Decimal("0.75")).quantize(Decimal("0.01")), t)
                            stats["price_changes"] += 1
                        bf_discounted = []
                    elif kind == "move":
                        move_customer(cur, payload, t, stats)

            if day.day == 1 or day == start_day:
                print(f"{day:%Y-%m}  orders so far: {stats['orders']:>6}  "
                      f"customers: {stats['customers']:>5}")
            day += timedelta(days=1)

    print("\nBackfill complete:")
    for k, v in stats.items():
        print(f"  {k:<14} {v}")
    print("\nNext: keep the consumer running until it catches up, then run `dbt build`.")


# ---------------------------------------------------------------- actions


def pick_customer(cur, order_ts: datetime, customers: list, stats: dict) -> dict:
    """Existing active customer (weighted by loyalty) or a brand new one."""
    # eligible: still "alive" as a customer and hasn't ordered in the last 5 days
    active = [c for c in customers
              if c["created"] < order_ts < c["lifetime_end"]
              and order_ts - c["last_order"] > timedelta(days=5)]
    # a small young customer base can't supply many repeat orders yet
    p_return = RETURNING_SHARE * min(1.0, len(active) / 300)
    if active and random.random() < p_return:
        customer = random.choices(active, weights=[c["loyalty"] for c in active], k=1)[0]
        customer["last_order"] = order_ts
        return customer

    created = order_ts - timedelta(minutes=random.randint(2, 30))   # signs up just before ordering
    first, last = fake.first_name(), fake.last_name()
    email = f"{first}.{last}.{random.randint(1, 999999)}@example.com".lower()
    city = weighted_choice(list(CITY_WEIGHTS), list(CITY_WEIGHTS.values()))
    cur.execute(
        """INSERT INTO customers (first_name, last_name, email, phone, city, country,
                                  created_at, updated_at)
           VALUES (%s, %s, %s, %s, %s, 'Finland', %s, %s) RETURNING customer_id""",
        (first, last, email, fake.phone_number(), city, created, created),
    )
    if random.random() < ONE_TIME_BUYER_SHARE:
        lifetime = timedelta(0)
    else:
        lifetime = timedelta(days=random.expovariate(1 / 160))
    customer = {"id": cur.fetchone()[0], "created": created,
                "lifetime_end": order_ts + lifetime,
                "loyalty": random.expovariate(1.0) + 0.1, "city": city,
                "last_order": order_ts}
    customers.append(customer)
    stats["customers"] += 1
    return customer


def create_order(cur, order_ts, day, customers, products, now, stats):
    customer = pick_customer(cur, order_ts, customers, stats)

    cur.execute(
        """INSERT INTO orders (customer_id, status, order_ts, updated_at)
           VALUES (%s, 'placed', %s, %s) RETURNING order_id""",
        (customer["id"], order_ts, order_ts),
    )
    order_id = cur.fetchone()[0]

    # basket: 1-4 different products, demand reacts to current price
    n_items = weighted_choice([1, 2, 3, 4], [50, 30, 14, 6])
    weights = [p["popularity"] * float(p["base_price"] / p["price"]) ** PRICE_ELASTICITY
               for p in products]
    basket = set()
    while len(basket) < n_items:
        basket.add(random.choices(range(len(products)), weights=weights, k=1)[0])
    items = []
    for idx in basket:
        p = products[idx]
        qty = weighted_choice([1, 2, 3], [80, 15, 5])
        items.append((order_id, p["id"], qty, p["price"]))
    cur.executemany(
        """INSERT INTO order_items (order_id, product_id, quantity, unit_price)
           VALUES (%s, %s, %s, %s)""",
        items,
    )
    stats["orders"] += 1
    stats["items"] += len(items)

    # ---- lifecycle, only the steps that would already have happened by now
    def set_status(status, t):
        cur.execute("UPDATE orders SET status = %s, updated_at = %s WHERE order_id = %s",
                    (status, t, order_id))

    if random.random() < 0.03:   # payment never completes -> cancelled next day
        t = order_ts + timedelta(hours=random.uniform(20, 30))
        if t < now:
            set_status("cancelled", t)
            stats["cancelled"] += 1
        return

    paid_at = order_ts + timedelta(minutes=random.expovariate(1 / 25) + 1)
    if paid_at >= now:
        return
    set_status("paid", paid_at)

    if random.random() < 0.015:  # customer cancels after paying, before shipping
        t = paid_at + timedelta(hours=random.uniform(1, 12))
        if t < now:
            set_status("cancelled", t)
            stats["cancelled"] += 1
        return

    # warehouse processing: busier in December, weekend orders wait for Monday
    processing_hours = random.gammavariate(2.0, 9.0) * (1.6 if day.month == 12 else 1.0)
    shipped_at = business_delay(paid_at, processing_hours)
    if shipped_at >= now:
        return
    set_status("shipped", shipped_at)

    transit_days = max(0.4, random.gauss(TRANSIT_DAYS[customer["city"]], 0.4))
    delivered_at = shipped_at + timedelta(days=transit_days)
    if delivered_at >= now:
        return
    set_status("delivered", delivered_at)


def set_price(cur, product, new_price, t):
    product["price"] = new_price
    cur.execute("UPDATE products SET price = %s, updated_at = %s WHERE product_id = %s",
                (new_price, t, product["id"]))


def change_price(cur, product, t, stats):
    factor = Decimal(str(round(random.uniform(0.85, 1.18), 2)))
    set_price(cur, product, (product["price"] * factor).quantize(Decimal("0.01")), t)
    stats["price_changes"] += 1


def move_customer(cur, customer, t, stats):
    if t <= customer["created"]:
        return
    new_city = weighted_choice([c for c in CITY_WEIGHTS if c != customer["city"]],
                               [w for c, w in CITY_WEIGHTS.items() if c != customer["city"]])
    customer["city"] = new_city
    cur.execute("UPDATE customers SET city = %s, updated_at = %s WHERE customer_id = %s",
                (new_city, t, customer["id"]))
    stats["moves"] += 1


if __name__ == "__main__":
    main()
