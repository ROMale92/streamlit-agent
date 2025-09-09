# aderenza_intervalli_excel_v2.py
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import io

st.set_page_config(page_title="Aderenza - Intervalli (stile agent)", layout="wide")
st.title("📊 Aderenza a Intervalli (stile agent) + Export Excel")

# ---------------- Utils ----------------
@st.cache_data(show_spinner=False)
def _read_any(file_bytes: bytes, name: str) -> pd.DataFrame:
    """Legge CSV (auto-sep, fallback ;) o XLSX, con cache."""
    if name.lower().endswith(".csv"):
        try:
            df = pd.read_csv(io.BytesIO(file_bytes), sep=None, engine="python")
        except Exception:
            df = pd.read_csv(io.BytesIO(file_bytes), sep=";", engine="python", decimal=",")
        return df
    else:
        return pd.read_excel(io.BytesIO(file_bytes))

def _as_str_col(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.replace(r"\s+", " ", regex=True)

def _parse_dates(df: pd.DataFrame, col: str) -> pd.DataFrame:
    out = df.copy()
    out[col] = pd.to_datetime(out[col], dayfirst=True, errors="coerce")
    return out.dropna(subset=[col])

def _cap_positive(x):
    return max(0.0, float(x)) if pd.notna(x) else np.nan

# ---------------- Inputs ----------------
disp_file = st.file_uploader("Carica DISPENSAZIONI (xlsx/csv)", type=["xlsx", "csv"])
ddd_file  = st.file_uploader("Carica LOOKUP DDD giornaliera (xlsx/csv)", type=["xlsx", "csv"])

c1, c2, c3 = st.columns([1,1,1])
with c1:
    period_days = st.number_input("Periodo di osservazione (giorni)", min_value=30, max_value=2000, value=365, step=30)
with c2:
    thr = st.slider("Soglia per % aderenti (≥)", 0.50, 1.00, 0.80, 0.05)
with c3:
    dedup = st.checkbox("Somma duplicati stesso giorno/paziente/terapia", value=True)

if disp_file and ddd_file:
    disp = _read_any(disp_file.getvalue(), disp_file.name)
    ddd  = _read_any(ddd_file.getvalue(),  ddd_file.name)

    st.subheader("Anteprima dispensazioni")
    st.dataframe(disp.head())
    st.subheader("Anteprima lookup DDD")
    st.dataframe(ddd.head())

    # --- Select columns (DISP) ---
    col_cf   = st.selectbox("Colonna codice fiscale (DISP)", disp.columns)
    # prova a suggerire un campo 'Principio/ATC/terap'
    sugg = next((c for c in disp.columns if "Principio" in c or "ATC" in c or "terap" in c.lower()), disp.columns[0])
    col_ther = st.selectbox("Colonna terapia/gruppo (DISP, es. Principio Attivo)", disp.columns, index=list(disp.columns).index(sugg))
    col_keyD = st.selectbox("Colonna CHIAVE per join con lookup (DISP)", disp.columns)
    col_date = st.selectbox("Colonna data erogazione (DISP)", disp.columns)
    col_dddE = st.selectbox("Colonna DDD erogate (DISP)", disp.columns)

    # --- Select columns (DDD) ---
    col_keyL = st.selectbox("Colonna CHIAVE nel lookup (DDD)", ddd.columns)
    col_std  = st.selectbox("Colonna DDD_standard_giornaliera (DDD)", ddd.columns)

    # --- Colonna per stratificazione (es. Principio Attivo) ---
    group_candidate_cols = [c for c in disp.columns if c not in {col_cf, col_date, col_dddE}]
    group_by_col = st.selectbox(
        "Stratifica e riepiloga per:",
        group_candidate_cols,
        index=(group_candidate_cols.index(col_ther) if col_ther in group_candidate_cols else 0)
    )

    # --- Cleanup & join ---
    disp = disp.copy(); ddd = ddd.copy()
    disp[col_keyD] = _as_str_col(disp[col_keyD]); ddd[col_keyL] = _as_str_col(ddd[col_keyL])
    disp = _parse_dates(disp, col_date)
    disp[col_dddE] = pd.to_numeric(disp[col_dddE], errors="coerce").map(_cap_positive)

    ddd_slim = ddd[[col_keyL, col_std]].rename(columns={col_keyL: "__KEY__", col_std: "__DDD_STD__"})
    ddd_slim["__DDD_STD__"] = pd.to_numeric(ddd_slim["__DDD_STD__"], errors="coerce").map(_cap_positive)
    disp = disp.rename(columns={col_keyD: "__KEY__"}).merge(ddd_slim, on="__KEY__", how="left")

    miss_key = disp["__DDD_STD__"].isna().sum()
    if miss_key > 0:
        st.warning(f"⚠️ {miss_key} righe senza DDD_standard_giornaliera → escluse")
        disp = disp.dropna(subset=["__DDD_STD__"])

    # Giorni coperti riga = DDD erogate / DDD standard
    disp["__giorni_coperti_disp__"] = (disp[col_dddE] / disp["__DDD_STD__"]).clip(lower=0)

    # --- Dedup opzionale ROBUSTA (niente collisioni reset_index) ---
    if dedup:
        base_keys = [col_cf, col_ther, col_date]
        if "__KEY__" in disp.columns and "__KEY__" not in base_keys:
            base_keys = base_keys[:2] + ["__KEY__"] + base_keys[2:]
        group_keys = list(dict.fromkeys(base_keys))

        g = disp.groupby(group_keys, dropna=False)["__giorni_coperti_disp__"].sum()
        disp = (
            g.rename("__coperti_tmp__")   # nome temporaneo che non confligge
             .reset_index()               # ora DataFrame
             .rename(columns={"__coperti_tmp__": "__giorni_coperti_disp__"})
        )

    # ---------------- CALCOLO A INTERVALLI (pesati sul periodo) ----------------
    results_rows = []
    for (cf, ther), g in disp.sort_values(col_date).groupby([col_cf, col_ther], sort=False):
        t0 = g[col_date].min()
        fine = t0 + pd.Timedelta(days=int(period_days))
        gg = g[g[col_date].between(t0, fine, inclusive="left")].sort_values(col_date).reset_index(drop=True)
        if gg.empty:
            continue

        total_covered = 0.0
        for i, r in gg.iterrows():
            start_i = r[col_date]
            next_date = gg.loc[i+1, col_date] if i < len(gg)-1 else fine
            end_i = min(next_date, fine)
            delta_i = (end_i - start_i).days
            if delta_i <= 0:
                continue
            covered_i = min(float(r["__giorni_coperti_disp__"]), float(delta_i))
            total_covered += covered_i

        ADH_anno = max(0.0, min(total_covered / float(period_days), 1.0))
        results_rows.append({col_cf: cf, col_ther: ther, "ADH_anno": ADH_anno})

    res = pd.DataFrame(results_rows)

    # ---------------- OUTPUT: per paziente × terapia ----------------
    st.subheader("📂 Risultati per paziente × terapia (Intervalli)")
    if res.empty:
        st.info("Nessun risultato nel periodo selezionato."); st.stop()
    st.dataframe(res)

    # ---------------- RIEPILOGO STRATIFICATO ----------------
    # Valore più frequente del campo scelto per ciascuna (cf, ther)
    strat_map = (
        disp.sort_values(col_date)
        .groupby([col_cf, col_ther])[group_by_col]
        .agg(lambda s: s.mode().iloc[0] if not s.mode().empty else s.iloc[0])
        .reset_index()
    )
    out = res.merge(strat_map, on=[col_cf, col_ther], how="left")

    grp = out.groupby(group_by_col, dropna=False)["ADH_anno"]
    summary = pd.DataFrame({
        group_by_col: grp.mean().index,
        "Media_ADH": grp.mean().round(4).values,
        "DS_ADH": grp.std(ddof=1).round(4).values,
        "N_paz": grp.count().values,
        "%_≥_soglia": (out.assign(_hit=out["ADH_anno"] >= thr).groupby(group_by_col)["_hit"].mean() * 100).round(2).values,
    }).sort_values("Media_ADH", ascending=False)

    st.subheader(f"📊 Riepilogo per **{group_by_col}**")
    st.dataframe(summary)

    # ---------------- GRAFICI ----------------
    st.subheader(f"📉 Dispersione per {group_by_col}")
    fig_box = go.Figure()
    for val, gdf in out.groupby(group_by_col, dropna=False):
        fig_box.add_trace(go.Box(
            y=gdf["ADH_anno"],
            name=str(val),
            boxpoints="all",
            jitter=0.4,
            pointpos=0,
            boxmean="sd"   # media + DS
        ))
    fig_box.add_hline(y=thr, line_dash="dash", line_color="black", annotation_text=f"Soglia={thr:.2f}")
    fig_box.update_layout(yaxis_title="ADH_anno", xaxis_title=group_by_col)
    st.plotly_chart(fig_box, use_container_width=True)

    st.subheader("📈 Dispersione complessiva")
    media = float(out["ADH_anno"].mean()); vmin = float(out["ADH_anno"].min()); vmax = float(out["ADH_anno"].max())
    fig = go.Figure()
    fig.add_trace(go.Scatter(y=out["ADH_anno"], mode="markers", name="Pazienti"))
    fig.add_hline(y=media, line_color="blue", annotation_text=f"Media={media:.2f}")
    fig.add_hline(y=thr, line_dash="dash", line_color="black", annotation_text=f"Soglia={thr:.2f}")
    fig.add_hline(y=vmin, line_dash="dot", line_color="red", annotation_text=f"Min={vmin:.2f}")
    fig.add_hline(y=vmax, line_dash="dot", line_color="green", annotation_text=f"Max={vmax:.2f}")
    fig.update_layout(yaxis_title="ADH_anno", xaxis_title="Indice paziente")
    st.plotly_chart(fig, use_container_width=True)

    # ---------------- EXPORT EXCEL ----------------
    st.subheader("⬇️ Esporta Excel")
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        # Foglio pazienti
        res_x = res.copy()
        res_x["ADH_anno"] = res_x["ADH_anno"].round(4)
        res_x.to_excel(writer, index=False, sheet_name="pazienti")

        # Foglio riepilogo_{campo}
        summ_x = summary.copy()
        summ_x.to_excel(writer, index=False, sheet_name=f"riepilogo_{group_by_col[:28]}")

        # Foglio totali
        tot = pd.DataFrame({
            "Periodo_giorni": [period_days],
            "Soglia": [thr],
            "Media_globale": [round(media, 4)],
            "Min_globale": [round(vmin, 4)],
            "Max_globale": [round(vmax, 4)],
            "N_pazienti": [len(out)],
            "N_aderenti_(≥soglia)": [int((out['ADH_anno'] >= thr).sum())],
            "%_aderenti_(≥soglia)": [round((out['ADH_anno'] >= thr).mean() * 100, 2)],
        })
        tot.to_excel(writer, index=False, sheet_name="totali")

    st.download_button(
        "Scarica risultati Excel",
        data=output.getvalue(),
        file_name=f"aderenza_intervalli_{group_by_col}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    # ---------------- Note ----------------
    with st.expander("ℹ️ Note metodologiche"):
        st.markdown(
            f"""
- **Metodo**: *Intervalli pesati sul periodo* (stile agent).
- **Periodo**: da prima dispensazione del paziente/terapia per **{period_days}** giorni.
- **Per intervallo**: `coperti_i = min(giorni_coperti, durata_intervallo)`; ultimo intervallo troncato a fine periodo.
- **Aderenza**: `ADH_anno = (Σ coperti_i) / {period_days}` (limitata a [0,1]).
- **Riepilogo**: Media, DS, N e % ≥ soglia per **{group_by_col}**.
"""
        )
