import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import io

st.title("📊 Aderenza (intervalli o PDC) con grafici e export Excel")

# --- Upload ---
disp_file = st.file_uploader("Carica DISPENSAZIONI (xlsx/csv)", type=["xlsx","csv"])
ddd_file  = st.file_uploader("Carica LOOKUP DDD giornaliera (xlsx/csv)", type=["xlsx","csv"])

period_days = st.number_input("Periodo di osservazione (giorni)", min_value=30, max_value=2000, value=365, step=30)
method = st.radio("Metodo di calcolo", ["Intervalli (tipo Access)", "Cumulato (PDC classico)"])

def read_any(f):
    if f.name.endswith(".csv"):
        return pd.read_csv(f, sep=";|,", engine="python")
    return pd.read_excel(f)

if disp_file and ddd_file:
    disp = read_any(disp_file)
    ddd  = read_any(ddd_file)

    st.subheader("Anteprima dispensazioni")
    st.dataframe(disp.head())
    st.subheader("Anteprima lookup DDD")
    st.dataframe(ddd.head())

    # --- scelte colonne DISP ---
    col_cf   = st.selectbox("Colonna codice fiscale (DISP)", disp.columns)
    col_ther = st.selectbox("Colonna terapia/gruppo (es. Principio Attivo) (DISP)", disp.columns)
    col_keyD = st.selectbox("Colonna CHIAVE per join con lookup (DISP)", disp.columns)
    col_date = st.selectbox("Colonna data erogazione (DISP)", disp.columns)
    col_dddE = st.selectbox("Colonna DDD erogate (DISP)", disp.columns)
    col_atc  = st.selectbox("Colonna ATC (opzionale, DISP)", ["(Nessuna)"] + list(disp.columns))

    # --- scelte colonne LOOKUP ---
    col_keyL = st.selectbox("Colonna CHIAVE nel lookup (DDD)", ddd.columns)
    col_std  = st.selectbox("Colonna DDD_standard_giornaliera (DDD)", ddd.columns)

    # --- parsing date ---
    disp[col_date] = pd.to_datetime(disp[col_date], dayfirst=True, errors="coerce")
    disp = disp.dropna(subset=[col_date])

    # --- join con lookup ---
    ddd_slim = ddd[[col_keyL, col_std]].rename(columns={col_keyL:"__KEY__", col_std:"__DDD_STD__"})
    disp = disp.rename(columns={col_keyD:"__KEY__"})
    disp = disp.merge(ddd_slim, on="__KEY__", how="left")

    miss = disp["__DDD_STD__"].isna().sum()
    if miss > 0:
        st.warning(f"⚠️ {miss} righe senza DDD_standard_giornaliera → escluse")
        disp = disp.dropna(subset=["__DDD_STD__"])

    # --- giorni coperti per riga ---
    disp["__giorni_coperti_disp__"] = disp[col_dddE].astype(float) / disp["__DDD_STD__"].astype(float)

    # Dedup opzionale
    if st.checkbox("Somma duplicati stesso giorno/paziente/terapia", value=True):
        disp = (disp
                .groupby([col_cf, col_ther, "__KEY__", col_date], as_index=False)
                .agg({"__giorni_coperti_disp__":"sum"}))

    # --- calcolo ---
    results_rows = []
    if method == "Intervalli (tipo Access)":
        for (cf, ther), g in disp.sort_values(col_date).groupby([col_cf, col_ther]):
            t0 = g[col_date].min()
            fine = t0 + pd.Timedelta(days=period_days)
            gg = g[g[col_date].between(t0, fine)].copy()
            if gg.empty: continue
            gg = gg.sort_values(col_date).reset_index(drop=True)
            adh_intervals = []
            for i, r in gg.iterrows():
                start_i = r[col_date]
                next_date = gg.loc[i+1, col_date] if i < len(gg)-1 else fine
                end_i = min(next_date, fine)
                delta_i = (end_i - start_i).days
                if delta_i <= 0: continue
                covered_i = min(r["__giorni_coperti_disp__"], delta_i)
                adh_i = covered_i / delta_i
                adh_intervals.append(adh_i)
            if adh_intervals:
                ADH_anno = sum(adh_intervals)/len(adh_intervals)
                results_rows.append({col_cf: cf, col_ther: ther, "ADH_anno": min(ADH_anno,1)})
        res = pd.DataFrame(results_rows)

    else:  # PDC classico
        rows = []
        for (cf, ther), g in disp.groupby([col_cf, col_ther]):
            t0 = g[col_date].min()
            fine = t0 + pd.Timedelta(days=period_days)
            gg = g[g[col_date].between(t0, fine)].copy()
            if gg.empty: continue
            giorni_coperti = gg["__giorni_coperti_disp__"].sum()
            pdc = min(giorni_coperti/period_days, 1)
            rows.append({col_cf: cf, col_ther: ther, "PDC": pdc, "giorni_coperti": giorni_coperti})
        res = pd.DataFrame(rows)

    st.subheader("📂 Risultati per paziente × terapia")
    st.dataframe(res)

    # --- media per ATC ---
    media_atc = None
    if col_atc != "(Nessuna)" and col_atc in disp.columns and not res.empty:
        atc_map = (
            disp.sort_values(col_date)
            .groupby([col_cf, col_ther])[col_atc]
            .agg(lambda s: s.mode().iloc[0] if not s.mode().empty else s.iloc[0])
            .reset_index()
        )
        out = res.merge(atc_map, on=[col_cf, col_ther], how="left")
        if method == "Intervalli (tipo Access)":
            media_atc = out.groupby(col_atc)["ADH_anno"].mean().reset_index()
        else:
            media_atc = out.groupby(col_atc)["PDC"].mean().reset_index()

        st.subheader("📊 Media per ATC")
        st.dataframe(media_atc)

    # --- grafico dispersione ---
    if not res.empty:
        valori = res["ADH_anno"] if "ADH_anno" in res else res["PDC"]
        media, vmin, vmax = valori.mean(), valori.min(), valori.max()

        fig = go.Figure()
        fig.add_trace(go.Scatter(y=valori, mode="markers", name="Pazienti"))
        fig.add_hline(y=media, line_color="blue", annotation_text=f"Media={media:.2f}")
        fig.add_hline(y=vmin, line_dash="dot", line_color="red", annotation_text=f"Min={vmin:.2f}")
        fig.add_hline(y=vmax, line_dash="dot", line_color="green", annotation_text=f"Max={vmax:.2f}")
        st.subheader("📈 Dispersione valori")
        st.plotly_chart(fig, use_container_width=True)

        # --- export Excel ---
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            res.to_excel(writer, index=False, sheet_name="pazienti")
            if media_atc is not None:
                media_atc.to_excel(writer, index=False, sheet_name="media_ATC")
            pd.DataFrame({
                "Media":[media],
                "Min":[vmin],
                "Max":[vmax],
                "Pazienti_aderenti":[(valori>=0.8).mean()]
            }).to_excel(writer, index=False, sheet_name="totali")
        st.download_button(
            "⬇️ Scarica risultati Excel",
            data=output.getvalue(),
            file_name="aderenza_risultati.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
