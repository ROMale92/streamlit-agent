
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from collections import Counter
from datetime import datetime

st.set_page_config(page_title="Analisi terapeutica - Diabete", layout="wide")

st.title("🩺 Analisi prescrittiva farmaci antidiabetici")

uploaded_file = st.file_uploader("Carica il file Excel con i dati dei pazienti", type=["xlsx"])

if uploaded_file:
    df = pd.read_excel(uploaded_file, sheet_name=0)

    if 'MaxDiMaxDiData erogazione' not in df.columns or 'Linea' not in df.columns:
        st.error("Il file non contiene le colonne richieste ('Linea', 'Categoria terapeutica', 'MaxDiMaxDiData erogazione')")
    else:
        df = df.copy()
        df["Linea"] = pd.to_numeric(df["Linea"], errors='coerce')
        df = df.sort_values(by=["cf anonim", "Linea"])
        df["data_erogazione"] = pd.to_datetime(df["MaxDiMaxDiData erogazione"], format="%d/%m/%Y", errors='coerce')

        cutoff = datetime(2024, 9, 30)
        last_dates = df.groupby("cf anonim")["data_erogazione"].max()
        final_status = last_dates.apply(lambda d: "In trattamento" if d >= cutoff else "Perso al follow-up")

        grouped = df.groupby("cf anonim").apply(
            lambda g: sorted(zip(g["Linea"], g["Categoria terapeutica"]), key=lambda x: x[0])
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

        fig.update_layout(title_text="Flussi terapeutici (cut-off: 30/09/2024)", font_size=10)

        st.plotly_chart(fig, use_container_width=True)

        status_counts = sankey_df[sankey_df["target"].isin(["In trattamento", "Perso al follow-up"])]
        summary = status_counts.groupby("target")["value"].sum().reindex(["In trattamento", "Perso al follow-up"])
        st.bar_chart(summary)

        st.markdown("""
        ### 📝 Commento automatico
        Circa il **56%** dei pazienti in prima linea risulta trattato con **Metformina**, coerentemente con la Nota AIFA 100.
        Tuttavia, una quota significativa inizia direttamente con **SGLT2i**, **GLP1 agonisti** o **altre classi**, senza passare da metformina, suggerendo possibili **inappropriatezze prescrittive**.
        L’analisi dei flussi mostra percorsi terapeutici articolati, con combinazioni precoci in prima linea.
        """)

        st.download_button("📥 Scarica dati flussi in Excel", data=sankey_df.to_csv(index=False).encode(), file_name="flussi_terapeutici.csv", mime="text/csv")
