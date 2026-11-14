-- One row per calendar day, for grouping and filtering by date.
select
    to_char(d, 'YYYYMMDD')::int        as date_key,
    d::date                            as date_day,
    extract(year from d)::int          as year,
    extract(quarter from d)::int       as quarter,
    extract(month from d)::int         as month,
    to_char(d, 'FMMonth')              as month_name,
    extract(isodow from d)::int        as day_of_week,
    to_char(d, 'FMDay')                as day_name,
    extract(isodow from d) in (6, 7)   as is_weekend,
    extract(week from d)::int          as iso_week
from generate_series('2026-01-01'::date, '2027-12-31'::date, interval '1 day') as d