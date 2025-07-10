
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from collections import Counter
from datetime import datetime
import io

st.set_page_config(page_title="Analisi prescrizione estesa", layout="wide")
st.title("📊 Analisi prescrittiva avanzata (naive, Sankey, aderenza)")

uploaded_file = st.file_uploader("📁 Carica il file Excel con i dati dei pazienti", type=["xlsx"])

if uploaded_file:
    df = pd.read_excel(uploaded_file)

    st.markdown("### 🔧 Seleziona le colonne chiave")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        col_id = st.selectbox("Identificativo paziente", df.columns)
    with col2:
        col_cat = st.selectbox("Categoria terapeutica", df.columns)
    with col3:
        col_data = st.selectbox("Data erogazione", df.columns)
    with col4:
        col_sesso = st.selectbox("Sesso (opzionale)", [""] + list(df.columns))

    data_indice = st.date_input("📅 Data indice (naive)", value=datetime(2023, 1, 1))
    cutoff_followup = st.date_input("📅 Cut-off follow-up", value=datetime(2024, 9, 30))

    df = df.copy()
    df[col_data] = pd.to_datetime(df[col_data], errors='coerce')
    df = df.sort_values(by=[col_id, col_data])

    # Filtro naive
    first_disp = df.groupby(col_id)[col_data].min()
    naive_ids = first_disp[first_disp >= pd.to_datetime(data_indice)].index
    df_naive = df[df[col_id].isin(naive_ids)]

    st.markdown(f"✅ Pazienti naive trovati: **{df_naive[col_id].nunique()}**")

    # Linee terapeutiche
    def assign_linee(group):
        group = group.sort_values(by=col_data)
        group["Linea"] = (group[col_cat] != group[col_cat].shift()).cumsum()
        return group

    df_linee = df_naive.groupby(col_id).apply(assign_linee).reset_index(drop=True)
    df_linee["label"] = df_linee[col_cat] + " (Linea " + df_linee["Linea"].astype(str) + ")"

    # Stato finale
    last_dates = df_linee.groupby(col_id)[col_data].max()
    final_status = last_dates.apply(lambda d: "In trattamento" if d >= pd.to_datetime(cutoff_followup) else "Perso al follow-up")

    # Sankey data
    grouped = df_linee.groupby(col_id).apply(lambda g: list(g["label"]))
    labeled_paths = []
    for patient, steps in grouped.items():
        steps = steps[:3]
        steps.append(final_status.get(patient))
        labeled_paths.append(steps)

    flow_counts = Counter()
    for path in labeled_paths:
        for i in range(len(path) - 1):
            flow_counts[(path[i], path[i + 1])] += 1

    sankey_df = pd.DataFrame([(src, tgt, val) for (src, tgt), val in flow_counts.items()],
                             columns=["source", "target", "value"])
    sankey_df["percentuale"] = (sankey_df["value"] / sankey_df.groupby("source")["value"].transform("sum") * 100).round(2)

    all_nodes = list(set(sankey_df["source"]).union(set(sankey_df["target"])))
    node_indices = {name: idx for idx, name in enumerate(all_nodes)}
    source_indices = sankey_df["source"].map(node_indices)
    target_indices = sankey_df["target"].map(node_indices)

    fig = go.Figure(data=[go.Sankey(
        node=dict(pad=15, thickness=20, line=dict(color="black", width=0.5), label=all_nodes),
        link=dict(source=source_indices, target=target_indices, value=sankey_df["value"]))])
    fig.update_layout(title_text="Flussi terapeutici", font_size=10)
    st.plotly_chart(fig, use_container_width=True)

    # Export figura
    fig_bytes = fig.to_image(format="png")
    st.download_button("📸 Scarica figura Sankey (PNG)", fig_bytes, file_name="sankey.png")

    # Tabella 1 per linea
    tab1 = df_linee.groupby(col_cat).agg(
        Numero_pazienti=(col_id, "nunique"),
        Percentuale_pazienti=(col_id, lambda x: round(100 * x.nunique() / df_linee[col_id].nunique(), 2)),
        Percentuale_maschi=(col_sesso, lambda s: round(100 * (s == "M").sum() / len(s), 2)) if col_sesso else None,
        Età_mediana=("Età", "median") if "Età" in df_linee.columns else None,
        Età_minima=("Età", "min") if "Età" in df_linee.columns else None,
        Età_massima=("Età", "max") if "Età" in df_linee.columns else None
    ).reset_index()

    # Aderenza
    linea_target = st.selectbox("📌 Linea per analisi aderenza", sorted(df_linee["Linea"].unique()))
    df_pdc = df_linee[df_linee["Linea"] == linea_target]
    aderenza = df_pdc.groupby(col_id).apply(
        lambda g: g[col_data].nunique() / ((g[col_data].max() - g[col_data].min()).days / 30 + 1)
    ).clip(upper=1).reset_index()
    aderenza.columns = [col_id, "PDC"]

    tab_aderenza = df_pdc.merge(aderenza, on=col_id).groupby(col_cat).agg(
        mean=("PDC", "mean"),
        sd=("PDC", "std"),
        N_totali=(col_id, "nunique"),
        N_aderenti=("PDC", lambda x: (x >= 0.8).sum())
    ).reset_index()
    tab_aderenza["%_aderenti"] = (tab_aderenza["N_aderenti"] / tab_aderenza["N_totali"] * 100).round(2)

    # Export excel
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        sankey_df.to_excel(writer, index=False, sheet_name="Flussi Sankey")
        tab1.to_excel(writer, index=False, sheet_name="Tabella 1")
        tab_aderenza.to_excel(writer, index=False, sheet_name="Aderenza PDC")
        df_pdc.merge(aderenza, on=col_id).to_excel(writer, index=False, sheet_name="Dettaglio Linea")

    st.download_button("📥 Scarica tutte le tabelle (Excel)", output.getvalue(), file_name="report_terapie.xlsx")
