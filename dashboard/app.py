"""
Shop analytics dashboard (entry point).
Run locally with:  python -m streamlit run dashboard/app.py

Pages live in dashboard/views/. Navigation is a menu bar across the top
(it collapses into a ☰ menu on small screens).
"""
import streamlit as st

st.set_page_config(page_title="Shop analytics | E-commerce CDC pipeline",
                   page_icon=":material/monitoring:", layout="wide",
                   initial_sidebar_state="collapsed")

pages = [
    st.Page("views/overview.py", title="Executive overview", icon=":material/insights:", default=True),
    st.Page("views/customers.py", title="Customers", icon=":material/group:"),
    st.Page("views/products.py", title="Products", icon=":material/inventory_2:"),
    st.Page("views/operations.py", title="Operations", icon=":material/local_shipping:"),
    st.Page("views/data_pipeline.py", title="Data pipeline", icon=":material/account_tree:"),
]
st.navigation(pages, position="top").run()
