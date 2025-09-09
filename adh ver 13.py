# aderenza_app.py
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import io

st.set_page_config(page_title="Aderenza con grafici ed export", layout="wide")
st.title("📊 Aderenza (intervalli o PDC) con grafici e export Excel")

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
    out = out.dropna(subset=[col])
    return out

def _cap_positive(x):
    return max(0.0, float(x)) if pd.notna(x) else np.nan

# --------------- Inputs ---------------
disp_file = st.file_uploader("Carica DISPENSAZIONI (xlsx/csv)", type=["xlsx", "csv"])
ddd_file  = st.file_uploader("Carica LOOKUP DDD giornaliera (xlsx/csv)", type=["xlsx", "csv"])

c1, c2, c3 = st.columns([1,1,1])
with c1:
    period_days = st.number_input("Periodo di osservazione (giorni)", min_value=30, max_value=2000, value=365, step=30)
with c2:
    method = st.radio("Metodo di calcolo", ["Intervalli (tipo Access)", "Cumulato (PDC classico)"])
with c3:
    thr = st.slider("Soglia aderenza (≥)", 0.50, 1.00, 0.80, 0.05)

stock_cap = False
if method == "Cumulato (PDC classico)":
    stock_cap = st.checkbox("Limita l'accumulo (stockpiling) a coprire il periodo (consigliato)", value=True)

# --------------- Processing ---------------
if disp_file and ddd_file:
    disp = _read_any(disp_file.getvalue(), disp_file.name)
    ddd  = _read_any(ddd_file.getvalue(),  ddd_file.name)

    st.subheader("Anteprima dispensazioni")
    st.dataframe(disp.head())
    st.subheader("Anteprima lookup DDD")
    st.dataframe(ddd.head())

    # --- Select columns (DISP) ---
    col_cf   = st.selectbox("Colonna codice fiscale (DISP)", disp.columns)
    col_ther = st.selectbox("Colonna terapia/gruppo (es. Principio Attivo) (DISP)", disp.columns)
    col_keyD = st.selectbox("Colonna CHIAVE per join con lookup (DISP)", disp.columns)
    col_date = st.selectbox("Colonna data erogazione (DISP)", disp.columns)
    col_dddE = st.selectbox("Colonna DDD erogate (DISP)", disp.columns)
    col_atc  = st.selectbox("Colonna ATC (opzionale, DISP)", ["(Nessuna)"] + list(disp.columns))

    # --- Select columns (DDD) ---
    col_keyL = st.selectbox("Colonna CHIAVE nel lookup (DDD)", ddd.columns)
    col_std  = st.selectbox("Colonna DDD_standard_giornaliera (DDD)", ddd.columns)

    # --- Cleanup types & dates ---
    disp = disp.copy()
    ddd  = ddd.copy()

    disp[col_keyD] = _as_str_col(disp[col_keyD])
    ddd[col_keyL]  = _as_str_col(ddd[col_keyL])

    disp = _parse_dates(disp, col_date)

    # Numeric safety
    disp[col_dddE] = pd.to_numeric(disp[col_dddE], errors="coerce").map(_cap_positive)

    # --- Join ---
    ddd_slim = ddd[[col_keyL, col_std]].rename(columns={col_keyL: "__KEY__", col_std: "__DDD_STD__"})
    ddd_slim["__DDD_STD__"] = pd.to_numeric(ddd_slim["__DDD_STD__"], errors="coerce").map(_cap_positive)

    disp = disp.rename(columns={col_keyD: "__KEY__"})
    disp = disp.merge(ddd_slim, on="__KEY__", how="left")

    st.caption("Colonne presenti dopo la JOIN:")
    st.write(list(disp.columns))

    miss_key = disp["__DDD_STD__"].isna().sum()
    if miss_key > 0:
        st.warning(f"⚠️ {miss_key} righe senza DDD_standard_giornaliera → escluse dalla stima giorni coperti")
        disp = disp.dropna(subset=["__DDD_STD__"])

    # --- Giorni coperti per riga ---
    disp["__giorni_coperti_disp__"] = (disp[col_dddE] / disp["__DDD_STD__"]).clip(lower=0)

    # --- Dedup opzionale (robusta: evita 'cannot insert ... already exists') ---
    if st.checkbox("Somma duplicati stesso giorno/paziente/terapia", value=True):
        base_keys = [col_cf, col_ther, col_date]
        if "__KEY__" in disp.columns and "__KEY__" not in base_keys:
            base_keys = base_keys[:2] + ["__KEY__"] + base_keys[2:]  # [cf, ther, __KEY__, date]
        group_keys = list(dict.fromkeys(base_keys))  # dedup chiavi mantenendo ordine

        disp = (
            disp
            .groupby(group_keys, dropna=False)["__giorni_coperti_disp__"]
            .sum()
            .reset_index()   # niente as_index=False → niente collisioni
        )

    # --------------- Calcoli ---------------
    if method == "Intervalli (tipo Access)":
        results_rows = []
        for (cf, ther), g in disp.sort_values(col_date).groupby([col_cf, col_ther], sort=False):
            t0 = g[col_date].min()
            fine = t0 + pd.Timedelta(days=int(period_days))
            gg = g[g[col_date].between(t0, fine, inclusive="left")].sort_values(col_date).reset_index(drop=True)
            if gg.empty:
                continue

            adh_intervals = []
            for i, r in gg.iterrows():
                start_i = r[col_date]
                next_date = gg.loc[i+1, col_date] if i < len(gg)-1 else fine
                end_i = min(next_date, fine)
                delta_i = (end_i - start_i).days
                if delta_i <= 0:
                    continue
                covered_i = min(r["__giorni_coperti_disp__"], delta_i)
                adh_intervals.append(covered_i / delta_i)

            if adh_intervals:
                ADH_anno = min(float(np.mean(adh_intervals)), 1.0)
                results_rows.append({col_cf: cf, col_ther: ther, "ADH_anno": ADH_anno})

        res = pd.DataFrame(results_rows)

    else:
        rows = []
        for (cf, ther), g in disp.groupby([col_cf, col_ther], sort=False):
            t0 = g[col_date].min()
            fine = t0 + pd.Timedelta(days=int(period_days))
            gg = g[g[col_date].between(t0, fine, inclusive="left")].sort_values(col_date).reset_index(drop=True)
            if gg.empty:
                continue

            if stock_cap:
                covered_days = 0.0
                current = t0
                stock = 0.0
                for _, r in gg.iterrows():
                    until = min(r[col_date], fine)
                    gap = (until - current).days
                    use = min(stock, max(gap, 0))
                    covered_days += use
                    stock -= use
                    current = until
                    stock += float(r["__giorni_coperti_disp__"])
                tail_gap = (fine - current).days
                covered_days += min(stock, max(tail_gap, 0))
                giorni_coperti = min(covered_days, period_days)
            else:
                giorni_coperti = float(gg["__giorni_coperti_disp__"].sum())
                giorni_coperti = min(giorni_coperti, period_days)

            pdc = min(giorni_coperti / period_days, 1.0)
            rows.append({col_cf: cf, col_ther: ther, "PDC": pdc, "giorni_coperti": giorni_coperti})

        res = pd.DataFrame(rows)

    st.subheader("📂 Risultati per paziente × terapia")
    if res.empty:
        st.info("Nessun risultato nel periodo selezionato.")
        st.stop()
    st.dataframe(res)

    # --------------- Media per ATC ---------------
    media_atc = None
    if col_atc != "(Nessuna)" and col_atc in disp.columns and not res.empty:
        atc_map = (
            disp.sort_values(col_date)
            .groupby([col_cf, col_ther])[col_atc]
            .agg(lambda s: s.mode().iloc[0] if not s.mode().empty else s.iloc[0])
            .reset_index()
        )
        out = res.merge(atc_map, on=[col_cf, col_ther], how="left")
        if "ADH_anno" in out.columns:
            media_atc = out.groupby(col_atc, dropna=False)["ADH_anno"].mean().reset_index()
        else:
            media_atc = out.groupby(col_atc, dropna=False)["PDC"].mean().reset_index()

        st.subheader("📊 Media per ATC")
        st.dataframe(media_atc)

    # --------------- Grafici ---------------
    valori = res["ADH_anno"] if "ADH_anno" in res.columns else res["PDC"]
    metric_name = "ADH_anno" if "ADH_anno" in res.columns else "PDC"

    media, vmin, vmax = float(valori.mean()), float(valori.min()), float(valori.max())
    share_thr = float((valori >= thr).mean())
    count_thr = int((valori >= thr).sum())

    st.subheader("📈 Dispersione valori")
    fig = go.Figure()
    fig.add_trace(go.Scatter(y=valori, mode="markers", name="Pazienti"))
    fig.add_hline(y=media, line_color="blue", annotation_text=f"Media={media:.2f}")
    fig.add_hline(y=thr, line_dash="dash", line_color="black", annotation_text=f"Soglia={thr:.2f}")
    fig.add_hline(y=vmin, line_dash="dot", line_color="red", annotation_text=f"Min={vmin:.2f}")
    fig.add_hline(y=vmax, line_dash="dot", line_color="green", annotation_text=f"Max={vmax:.2f}")
    fig.update_layout(yaxis_title=metric_name, xaxis_title="Indice paziente")
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("📉 Istogramma")
    hist = go.Figure()
    hist.add_trace(go.Histogram(x=valori, nbinsx=20, name=metric_name))
    hist.add_vline(x=thr, line_dash="dash", line_color="black", annotation_text=f"Soglia={thr:.2f}")
    hist.update_layout(xaxis_title=metric_name, yaxis_title="Conteggio")
    st.plotly_chart(hist, use_container_width=True)

    # --------------- Export Excel ---------------
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        res_rounded = res.copy()
        if "ADH_anno" in res_rounded:
            res_rounded["ADH_anno"] = res_rounded["ADH_anno"].round(4)
        if "PDC" in res_rounded:
            res_rounded["PDC"] = res_rounded["PDC"].round(4)
        if "giorni_coperti" in res_rounded:
            res_rounded["giorni_coperti"] = res_rounded["giorni_coperti"].round(2)
        res_rounded.to_excel(writer, index=False, sheet_name="pazienti")

        if media_atc is not None:
            media_atc_round = media_atc.copy()
            col_val = "ADH_anno" if "ADH_anno" in media_atc_round.columns else "PDC"
            media_atc_round[col_val] = media_atc_round[col_val].round(4)
            media_atc_round.to_excel(writer, index=False, sheet_name="media_ATC")

        totali = pd.DataFrame({
            "Metrica": [metric_name],
            "Media": [round(media, 4)],
            "Min": [round(vmin, 4)],
            "Max": [round(vmax, 4)],
            "Period_days": [period_days],
            "Soglia": [thr],
            "N_pazienti": [len(valori)],
            "N_aderenti_(>=soglia)": [count_thr],
            "Share_aderenti_(%)": [round(share_thr * 100, 2)],
        })
        totali.to_excel(writer, index=False, sheet_name="totali")

    st.download_button(
        "⬇️ Scarica risultati Excel",
        data=output.getvalue(),
        file_name="aderenza_risultati.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    # --------------- Note ---------------
    with st.expander("ℹ️ Note metodologiche"):
        st.markdown(
            f"""
- **Periodo**: per ogni (paziente, terapia) parte dalla prima dispensazione e dura **{period_days}** giorni.
- **Intervalli**: calcola l'aderenza per ciascun intervallo tra dispensazioni e poi fa la media, troncando all'interno del periodo.
- **PDC**: giorni coperti sul periodo. Con *Limita l'accumulo* attivo, lo stock non supera il periodo.
- **Soglia**: la percentuale aderenti usa la soglia impostata sopra (default {thr:.2f}).
"""
        )
