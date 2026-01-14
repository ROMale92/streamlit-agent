import streamlit as st
import pandas as pd
import io

st.set_page_config(layout="wide")
st.title("Analisi linee terapeutiche per paziente – con Tabella 1 (linea selezionata)")

# -------------------------
# Session state init
# -------------------------
if "df_day" not in st.session_state:
    st.session_state.df_day = None
if "params" not in st.session_state:
    st.session_state.params = {}

file = st.file_uploader("① Carica file Excel con dispensazioni", type=["xlsx"])

if file:
    df = pd.read_excel(file)
    st.success("File caricato.")
    st.dataframe(df.head())

    with st.form("parametri"):
        col1, col2 = st.columns(2)
        with col1:
            id_col = st.selectbox("Colonna ID paziente", df.columns, key="id_col")
            cat_col = st.selectbox("Colonna categoria terapeutica (es. ATC o classe)", df.columns, key="cat_col")
            ex_col = st.selectbox("Colonna sesso", df.columns, key="ex_col")
        with col2:
            date_col = st.selectbox("Colonna data dispensazione", df.columns, key="date_col")
            age_col = st.selectbox("Colonna età", df.columns, key="age_col")
            data_indice = st.date_input("Data indice (pazienti naïve)", key="data_indice")
        invia = st.form_submit_button("Esegui analisi")

    # -------------------------
    # Quando premi il bottone: calcolo e salvo df_day in session_state
    # -------------------------
    if invia:
        d = df.copy()
        d[date_col] = pd.to_datetime(d[date_col], errors="coerce")
        d = d.dropna(subset=[date_col])

        # Naïve: prima dispensazione >= data_indice
        prima_disp = d.groupby(id_col)[date_col].min().reset_index()
        naive_ids = prima_disp[prima_disp[date_col] >= pd.to_datetime(data_indice)][id_col]
        d = d[d[id_col].isin(naive_ids)].copy()

        if d.empty:
            st.session_state.df_day = None
            st.warning("Nessun paziente soddisfa il criterio naïve per la data indice selezionata.")
        else:
            # combo nello stesso giorno
            d = d.sort_values([id_col, date_col, cat_col])

            def join_unique_sorted(x):
                vals = [str(v) for v in x if pd.notna(v)]
                vals = sorted(pd.unique(vals))
                return "+".join(vals)

            df_day = (
                d.groupby([id_col, date_col], as_index=False)
                 .agg(
                     **{
                         cat_col: (cat_col, join_unique_sorted),
                         ex_col: (ex_col, "first"),
                         age_col: (age_col, "first"),
                     }
                 )
            )

            # linee su cambi di regimen
            df_day = df_day.sort_values([id_col, date_col])
            df_day["Linea"] = (
                df_day.groupby(id_col)[cat_col]
                      .transform(lambda x: x.ne(x.shift()).cumsum())
                      .astype(int)
            )
            df_day["Terapia_linea"] = df_day[cat_col] + " (Linea " + df_day["Linea"].astype(str) + ")"

            # salvo risultati + parametri usati
            st.session_state.df_day = df_day
            st.session_state.params = dict(
                id_col=id_col, cat_col=cat_col, ex_col=ex_col,
                date_col=date_col, age_col=age_col, data_indice=str(data_indice)
            )
            st.success("Analisi completata e salvata. Ora puoi cambiare linea senza perdere i risultati.")

    # -------------------------
    # Se ho già df_day in memoria, posso lavorare sulla linea scelta
    # -------------------------
    if st.session_state.df_day is not None:
        df_day = st.session_state.df_day
        p = st.session_state.params

        # colonne (riprese dai parametri salvati)
        id_col = p["id_col"]
        cat_col = p["cat_col"]
        ex_col = p["ex_col"]
        date_col = p["date_col"]
        age_col = p["age_col"]

        st.subheader("📊 Linee terapeutiche (combo nello stesso giorno)")
        st.dataframe(df_day[[id_col, date_col, cat_col, "Linea", "Terapia_linea"]])

        # scelta linea target (questa ora NON “rompe” nulla)
        linee_disponibili = sorted(df_day["Linea"].unique())
        default_idx = linee_disponibili.index(1) if 1 in linee_disponibili else 0

        linea_target = st.selectbox(
            "🎯 Seleziona la linea su cui lavorare (Tabella 1 + output)",
            options=linee_disponibili,
            index=default_idx,
            key="linea_target"
        )

        df_linea = df_day[df_day["Linea"] == linea_target].copy()
        st.info(
            f"Lavoro sulla Linea {linea_target} — righe: {len(df_linea):,} — pazienti: {df_linea[id_col].nunique():,}"
        )

        # Tabella 1 + Totale
        st.subheader(f"📋 Tabella 1 – Caratteristiche pazienti per categoria (Linea {linea_target})")

        sex_norm = df_linea[ex_col].astype(str).str.strip().str.upper()
        df_linea["_is_male"] = sex_norm.eq("M")

        tab1 = df_linea.groupby(cat_col).agg(
            N_pazienti=(id_col, "nunique"),
            Perc_maschi=("_is_male", lambda x: round(x.mean(skipna=True) * 100, 2)),
            Età_media=(age_col, "mean"),
            Età_mediana=(age_col, "median"),
            Età_min=(age_col, "min"),
            Età_max=(age_col, "max")
        ).reset_index()

        tab1["Età_media"] = pd.to_numeric(tab1["Età_media"], errors="coerce").round(2)

        total = pd.DataFrame([{
            cat_col: "Totale",
            "N_pazienti": df_linea[id_col].nunique(),
            "Perc_maschi": round(df_linea["_is_male"].mean(skipna=True) * 100, 2),
            "Età_media": pd.to_numeric(df_linea[age_col], errors="coerce").mean(),
            "Età_mediana": pd.to_numeric(df_linea[age_col], errors="coerce").median(),
            "Età_min": pd.to_numeric(df_linea[age_col], errors="coerce").min(),
            "Età_max": pd.to_numeric(df_linea[age_col], errors="coerce").max(),
        }])
        total["Età_media"] = pd.to_numeric(total["Età_media"], errors="coerce").round(2)

        tab1_out = pd.concat([tab1, total], ignore_index=True)
        st.dataframe(tab1_out)

        # Riassunto linea scelta
        summary_lines = (
            df_linea.groupby([id_col, "Linea"], as_index=False)
                    .agg(
                        line_start=(date_col, "min"),
                        line_end=(date_col, "max"),
                        terapia=(cat_col, "first"),
                        n_giorni_disp=(date_col, "count"),
                        sesso=(ex_col, "first"),
                        eta=(age_col, "first"),
                    )
                    .sort_values([id_col, "Linea"])
        )
        st.subheader(f"🧾 Riassunto linea {linea_target} (per paziente)")
        st.dataframe(summary_lines)

        # Export Excel SOLO linea scelta
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df_linea[[id_col, date_col, cat_col, "Linea", "Terapia_linea", ex_col, age_col]].to_excel(
                writer, index=False, sheet_name="Linee_terapeutiche"
            )
            tab1_out.to_excel(writer, index=False, sheet_name="Tabella1")
            summary_lines.to_excel(writer, index=False, sheet_name="Riassunto_linee")

        st.download_button(
            f"⬇️ Scarica risultati in Excel (Linea {linea_target})",
            data=buffer.getvalue(),
            file_name=f"linee_terapeutiche_tab1_linea{linea_target}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        # opzionale: pulsante reset per rifare analisi da zero
        if st.button("🔄 Reset analisi"):
            st.session_state.df_day = None
            st.session_state.params = {}
            st.rerun()
