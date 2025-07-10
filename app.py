
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from collections import Counter
from datetime import datetime

st.set_page_config(page_title="Analisi terapeutica personalizzata", layout="wide")
st.title("🧪 Analisi terapeutica - pazienti cronici")

uploaded_file = st.file_uploader("📁 Carica un file Excel con i dati dei pazienti", type=["xlsx"])

if uploaded_file:
    df = pd.read_excel(uploaded_file)

    st.markdown("### 🔧 Seleziona le colonne chiave")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        col_id = st.selectbox("Identificativo paziente", df.columns)
    with col2:
        col_linea = st.selectbox("Numero linea", df.columns)
    with col3:
        col_cat = st.selectbox("Categoria terapeutica", df.columns)
    with col4:
        col_data = st.selectbox("Data erogazione", df.columns)

    cutoff = st.date_input("📅 Seleziona cut-off di follow-up", value=datetime(2024, 9, 30))

    df = df.copy()
    df[col_linea] = pd.to_numeric(df[col_linea], errors='coerce')
    df[col_data] = pd.to_datetime(df[col_data], errors='coerce')
    df = df.sort_values(by=[col_id, col_linea])

    last_dates = df.groupby(col_id)[col_data].max()
    final_status = last_dates.apply(lambda d: "In trattamento" if d >= pd.to_datetime(cutoff) else "Perso al follow-up")

    grouped = df.groupby(col_id).apply(
        lambda g: sorted(zip(g[col_linea], g[col_cat]), key=lambda x: x[0])
    )

    labeled_paths = []
    for patient, steps in grouped.items():
        steps = steps[:3]
        labeled = [f"{cat} (Linea {int(linea)})" for linea, cat in steps]
        labeled.append(final_status.get(patient))
        labeled_paths.append(labeled)

    flow_counts = Counter()
    for path in labeled_paths:
        for i in range(len(path) - 1):
            flow_counts[(path[i], path[i + 1])] += 1

    sankey_df = pd.DataFrame(
        [(src, tgt, val) for (src, tgt), val in flow_counts.items()],
        columns=["source", "target", "value"]
    )

    sankey_df["percentuale"] = (sankey_df["value"] / sankey_df.groupby("source")["value"].transform("sum") * 100).round(2)

    all_nodes = list(set(sankey_df["source"]).union(set(sankey_df["target"])))
    node_indices = {name: idx for idx, name in enumerate(all_nodes)}
    source_indices = sankey_df["source"].map(node_indices)
    target_indices = sankey_df["target"].map(node_indices)

    fig = go.Figure(data=[go.Sankey(
        node=dict(
            pad=15,
            thickness=20,
            line=dict(color="black", width=0.5),
            label=all_nodes,
        ),
        link=dict(
            source=source_indices,
            target=target_indices,
            value=sankey_df["value"],
        ))])

    fig.update_layout(title_text="Flussi terapeutici", font_size=10)
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("### 📊 Aderenza terapeutica (PDC)")
    aderenza = df.groupby(col_id).apply(
        lambda g: g[col_data].nunique() / ((g[col_data].max() - g[col_data].min()).days / 30 + 1)
    ).clip(upper=1).reset_index()
    aderenza.columns = [col_id, "Aderenza_PDC"]
    st.dataframe(aderenza.head(10))

    st.download_button("📥 Scarica flussi in CSV", data=sankey_df.to_csv(index=False).encode(), file_name="flussi.csv")
    st.download_button("📥 Scarica aderenza PDC", data=aderenza.to_csv(index=False).encode(), file_name="aderenza_PDC.csv")
