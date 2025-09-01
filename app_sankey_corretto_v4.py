
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import io

st.set_page_config(layout="wide")
st.title("Analisi linee terapeutiche - Sankey")

file = st.file_uploader("📁 Carica file Excel con dispensazioni singole", type=["xlsx"])
if file:
    df = pd.read_excel(file)
    st.success("✅ File caricato!")

    # FORM INPUT
    with st.form("setup"):
        col1, col2 = st.columns(2)
        with col1:
            id_col = st.selectbox("Colonna identificativo paziente", df.columns)
            cat_col = st.selectbox("Colonna categoria terapeutica (es. ATC)", df.columns)
        with col2:
            date_col = st.selectbox("Colonna data dispensazione", df.columns)
            cutoff_naive = st.date_input("📅 Data indice (per selezionare naïve)")
            cutoff_followup = st.date_input("📅 Cut-off follow-up (per stato finale)", value=pd.to_datetime("2024-09-30"))
        submitted = st.form_submit_button("Avvia analisi")

    if submitted:
        # PARSING DATE
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce", dayfirst=True)
        df = df.dropna(subset=[date_col])

        # IDENTIFICA NAÏVE
        first_disp = df.groupby(id_col)[date_col].min().reset_index()
        naive_ids = first_disp[first_disp[date_col] >= pd.to_datetime(cutoff_naive)][id_col]
        df = df[df[id_col].isin(naive_ids)]

        # ORDINAMENTO
        df = df.sort_values(by=[id_col, date_col])

        # IDENTIFICA LINEE TERAPEUTICHE (come numeri interi)
        df["Linea"] = df.groupby(id_col)[cat_col].transform(lambda x: x.ne(x.shift()).cumsum())
        df["Terapia"] = df[cat_col] + " (Linea " + df["Linea"].astype(str) + ")"

        # AGGIUNGI STATO FINALE
        last_dates = df.groupby(id_col)[date_col].max().reset_index()
        last_dates["Esito"] = last_dates[date_col].apply(
            lambda x: "In trattamento" if x >= pd.to_datetime(cutoff_followup) else "Perso al follow-up"
        )
        df = df.merge(last_dates[[id_col, "Esito"]], on=id_col, how="left")

        # PULIZIA
        df["Linea"] = pd.to_numeric(df["Linea"], errors="coerce").astype(int)

        if df.empty:
            st.warning("⚠️ Nessun dato valido disponibile dopo la conversione delle linee terapeutiche.")
        else:
            max_line = df["Linea"].max()

            if max_line < 1:
                st.warning("⚠️ Dati insufficienti per costruire un diagramma Sankey.")
            else:
                # COSTRUISCI I FLUSSI
                st.subheader("🔄 Flussi terapeutici (Sankey)")
                sankey_data = []

                for i in range(1, max_line):
                    step = df[df["Linea"].isin([i, i + 1])]
                    pivot = step.pivot_table(index=id_col, columns="Linea", values="Terapia", aggfunc="first").dropna()
                    if not pivot.empty:
                        flow = pivot.groupby([i, i + 1]).size().reset_index(name="Count")
                        flow.columns = ["source", "target", "Count"]
                        sankey_data.append(flow)

                # FLUSSO FINALE verso "Esito"
                last_step = df.groupby(id_col).agg({"Linea": "max", "Terapia": "last", "Esito": "last"}).reset_index()
                flow_end = last_step.groupby(["Terapia", "Esito"]).size().reset_index(name="Count")
                flow_end.columns = ["source", "target", "Count"]
                sankey_data.append(flow_end)

                # UNISCI TUTTO
                sankey_df = pd.concat(sankey_data, ignore_index=True)
                all_labels = list(pd.unique(sankey_df["source"].tolist() + sankey_df["target"].tolist()))

                # Pulisci da valori non presenti nei label
                sankey_df = sankey_df[
                    sankey_df["source"].isin(all_labels) &
                    sankey_df["target"].isin(all_labels)
                ]

                sankey_df["source_id"] = sankey_df["source"].apply(lambda x: all_labels.index(x))
                sankey_df["target_id"] = sankey_df["target"].apply(lambda x: all_labels.index(x))

                # DIAGRAMMA SANKEY
                fig = go.Figure(go.Sankey(
                    node=dict(
                        label=all_labels,
                        pad=30,
                        thickness=25,
                        line=dict(color="black", width=0.5),
                    ),
                    link=dict(
                        source=sankey_df["source_id"],
                        target=sankey_df["target_id"],
                        value=sankey_df["Count"],
                    )
                ))

                fig.update_layout(
                    title_text="Sankey - Linee terapeutiche",
                    font=dict(size=16, family="Arial", color="black")
                )

                st.plotly_chart(fig, use_container_width=True)

                # DOWNLOAD DATI SANK
                st.subheader("📥 Scarica dati Sankey")
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                    sankey_df.to_excel(writer, index=False, sheet_name="Dati Sankey")
                st.download_button(
                    label="💾 Scarica dati Sankey (Excel)",
                    data=buffer.getvalue(),
                    file_name="dati_sankey.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
