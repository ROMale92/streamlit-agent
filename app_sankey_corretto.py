
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

        # IDENTIFICA LINEE TERAPEUTICHE
        df["Linea"] = (
            df.groupby(id_col)[cat_col]
            .transform(lambda x: x.ne(x.shift()).cumsum())
            .astype(str)
        )
        df["Terapia"] = df[cat_col] + " (Linea " + df["Linea"] + ")"

        # AGGIUNGI STATO FINALE
        last_dates = df.groupby(id_col)[date_col].max().reset_index()
        last_dates["Esito"] = last_dates[date_col].apply(
            lambda x: "In trattamento" if x >= pd.to_datetime(cutoff_followup) else "Perso al follow-up"
        )
        df = df.merge(last_dates[[id_col, "Esito"]], on=id_col, how="left")

        # CONVERTI E PULISCI LA COLONNA "Linea"
        df["Linea"] = pd.to_numeric(df["Linea"], errors="coerce")
        df = df.dropna(subset=["Linea"])
        df["Linea"] = df["Linea"].astype(int)

        # Se non ci sono dati validi, interrompi con messaggio
        if df.empty:
            st.warning("⚠️ Nessun dato valido disponibile dopo la conversione delle linee terapeutiche.")
        else:
            max_line = df["Linea"].max()

            if max_line < 2:
                st.warning("⚠️ Dati insufficienti per costruire un diagramma Sankey.")
            else:
                # COSTRUISCI I FLUSSI
                st.subheader("🔄 Flussi terapeutici (Sankey)")
                sankey_data = []

                for i in range(1, max_line):
                    step = df[df["Linea"].isin([i, i + 1])]
                    pivot = step.pivot_table(index=id_col, columns="Linea", values="Terapia", aggfunc="first").dropna()
                    flow = pivot.groupby([i, i + 1]).size().reset_index(name="Count")
                    sankey_data.append(flow)

                # FLUSSO FINALE
                last_step = df.groupby(id_col).agg({"Linea": "max", "Terapia": "last", "Esito": "last"}).reset_index()
                flow_end = last_step.groupby(["Terapia", "Esito"]).size().reset_index(name="Count")
                flow_end.columns = [max_line, max_line + 1, "Count"]
                sankey_data.append(flow_end)

                # UNISCI TUTTO
                sankey_df = pd.concat(sankey_data, ignore_index=True)
                all_labels = pd.Series(pd.concat([sankey_df.iloc[:, 0], sankey_df.iloc[:, 1]])).dropna().unique().tolist()
                sankey_df["source"] = sankey_df[sankey_df.columns[0]].apply(lambda x: all_labels.index(x))
                sankey_df["target"] = sankey_df[sankey_df.columns[1]].apply(lambda x: all_labels.index(x))

                # DIAGRAMMA SANKEY
                fig = go.Figure(go.Sankey(
                    node=dict(
                        label=all_labels,
                        pad=20,
                        thickness=20,
                        line=dict(color="black", width=0.5),
                    ),
                    link=dict(
                        source=sankey_df["source"],
                        target=sankey_df["target"],
                        value=sankey_df["Count"],
                    )
                ))
                fig.update_layout(title_text="Sankey - Linee terapeutiche", font_size=14)
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
